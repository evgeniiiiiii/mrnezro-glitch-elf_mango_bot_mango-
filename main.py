import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================== CONFIG & LOGS ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
COURIER_ROUTES_RAW = os.environ.get("COURIER_ROUTES", "").strip()

if not BOT_TOKEN:
    raise ValueError("Не задано BOT_TOKEN у змінних середовища.")

if not COURIER_ROUTES_RAW:
    raise ValueError(
        "Не задано COURIER_ROUTES у змінних середовища.\n"
        'Приклад: {"Берлін":{"chat_id":-100111,"name":"Кур\'єр Berlin"},"__default__":{"chat_id":-100222,"name":"Other Courier"}}'
    )


def load_courier_routes(raw_value: str) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError as e:
        raise ValueError(f"COURIER_ROUTES має бути валідним JSON. Помилка: {e}")

    if not isinstance(data, dict):
        raise ValueError("COURIER_ROUTES має бути JSON-об'єктом.")

    normalized_routes = {}

    for city_name, route_data in data.items():
        if not isinstance(route_data, dict):
            raise ValueError(f"Значення для міста '{city_name}' має бути JSON-об'єктом.")

        if "chat_id" not in route_data:
            raise ValueError(f"Для міста '{city_name}' відсутній chat_id.")

        try:
            chat_id = int(route_data["chat_id"])
        except (ValueError, TypeError):
            raise ValueError(f"chat_id для міста '{city_name}' має бути числом.")

        courier_name = str(route_data.get("name", city_name)).strip() or city_name

        normalized_routes[city_name.strip().lower()] = {
            "chat_id": chat_id,
            "name": courier_name
        }

    return normalized_routes


COURIER_ROUTES = load_courier_routes(COURIER_ROUTES_RAW)

if "__default__" not in COURIER_ROUTES:
    logger.warning("У COURIER_ROUTES немає '__default__'. Для невідомих міст кур'єр не буде визначений.")


# ================== MEMORY STORAGE ==================
# Після перезапуску все очиститься
user_carts: Dict[int, List[Dict[str, Any]]] = {}
user_cities: Dict[int, str] = {}


# ================== LOAD CATALOG ==================
BASE_DIR = Path(__file__).resolve().parent
CATALOG_FILE = BASE_DIR / "catalog.json"


def load_catalog() -> Dict[str, Any]:
    if not CATALOG_FILE.exists():
        raise FileNotFoundError(f"Файл каталогу не знайдено: {CATALOG_FILE}")

    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Помилка JSON у catalog.json: {e}")

    if not isinstance(data, dict):
        raise ValueError("catalog.json має містити JSON-об'єкт.")
    if "categories" not in data or not isinstance(data["categories"], dict):
        raise ValueError("У catalog.json має бути ключ 'categories' типу object.")

    return data


CATALOG = load_catalog()


# ================== HELPERS ==================
def normalize_city_name(city_name: str) -> str:
    return (city_name or "").strip().lower()


def get_courier_for_city(city_name: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_city_name(city_name)

    if normalized in COURIER_ROUTES:
        return COURIER_ROUTES[normalized]

    # кілька корисних синонімів
    aliases = {
        "berlin": "берлін",
        "dresden": "дрезден",
        "leipzig": "лейпциг",
    }

    if normalized in aliases:
        mapped = aliases[normalized]
        if mapped in COURIER_ROUTES:
            return COURIER_ROUTES[mapped]

    return COURIER_ROUTES.get("__default__")


def make_markup(button_rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(button_rows)


async def smart_edit_or_send(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    image: Optional[str] = None,
):
    q = update.callback_query
    chat_id = update.effective_chat.id

    if q:
        if image:
            try:
                await q.edit_message_media(
                    media=InputMediaPhoto(media=image, caption=text, parse_mode="HTML"),
                    reply_markup=reply_markup
                )
                return
            except Exception:
                try:
                    await q.message.delete()
                except Exception:
                    pass
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=image,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                return
        else:
            try:
                await q.edit_message_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                return
            except Exception:
                try:
                    await q.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                return
    else:
        if image:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=image,
                caption=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )


def _extract_flavor_name(flavor_obj: Any) -> str:
    if isinstance(flavor_obj, dict):
        return str(flavor_obj.get("name", "Невідомий смак"))
    return str(flavor_obj)


def _has_nicotine_levels(item: Dict[str, Any]) -> bool:
    return isinstance(item.get("nicotine_levels"), list) and len(item.get("nicotine_levels")) > 0


def _format_cart_items(cart: List[Dict[str, Any]]) -> Tuple[str, float]:
    items_text = ""
    total_price = 0.0

    for idx, item in enumerate(cart, start=1):
        price = float(item.get("price", 0))
        total_price += price
        items_text += f"{idx}. {item.get('name', 'Товар')} — {price:g}€\n"

    return items_text, total_price


def _build_order_message(order_id: str, city: str, user_id: int, full_name: str, username: str, cart: List[Dict[str, Any]]) -> Tuple[str, float, str]:
    items_lines = ""
    total_price = 0.0

    for item in cart:
        price = float(item.get("price", 0))
        total_price += price
        items_lines += f"• <b>{item.get('name', 'Товар')}</b> — {price:g}€\n"

    order_text = (
        f"🛍️ <b>НОВЕ ЗАМОВЛЕННЯ №{order_id}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📍 <b>МІСТО: {city.upper()}</b>\n"
        f"👤 Клієнт: {full_name} (@{username})\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📦 <b>Товари:</b>\n{items_lines}\n"
        f"💰 <b>РАЗОМ: {total_price:g}€</b>\n"
    )
    return order_text, total_price, items_lines


# ================== USER FLOW ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_carts.setdefault(user_id, [])

    keyboard = [[InlineKeyboardButton("📦 Каталог", callback_data="catalog")]]
    await update.message.reply_text(
        "👋 Вітаємо в ELF FOX!\nНатисніть кнопку нижче, щоб почати.",
        reply_markup=make_markup(keyboard)
    )


async def catalog_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()

    user_id = update.effective_user.id

    if user_id not in user_cities or user_cities[user_id] == "Не вказано":
        text = "🏘 <b>Будь ласка, оберіть ваше місто для замовлення:</b>"
        keyboard = [
            [InlineKeyboardButton("📍 Берлін", callback_data="set_city:Берлін")],
            [InlineKeyboardButton("📍 Дрезден", callback_data="set_city:Дрезден")],
            [InlineKeyboardButton("📍 Лейпциг", callback_data="set_city:Лейпциг")],
            [InlineKeyboardButton("🌍 Інше місто", callback_data="set_city:other")],
        ]
    else:
        current_city = user_cities[user_id]
        text = f"📍 Ваше місто: <b>{current_city}</b>\n\n📦 <b>Оберіть категорію:</b>"

        keyboard = []
        for cat_key, cat_data in CATALOG.get("categories", {}).items():
            name = cat_data.get("name", cat_key)
            keyboard.append([InlineKeyboardButton(name, callback_data=f"cat:{cat_key}")])

        keyboard.append([InlineKeyboardButton("🛒 Кошик", callback_data="cart")])
        keyboard.append([InlineKeyboardButton("🏙 Змінити місто", callback_data="change_city")])

    await smart_edit_or_send(update, context, text, make_markup(keyboard))


async def category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    _, cat_key = q.data.split(":", 1)
    category = CATALOG.get("categories", {}).get(cat_key)

    if not category:
        await q.message.reply_text("❌ Помилка: категорію не знайдено.")
        return

    keyboard = []
    for b_key, b_data in category.get("brands", {}).items():
        keyboard.append([InlineKeyboardButton(b_data.get("name", b_key), callback_data=f"brand:{cat_key}:{b_key}")])

    keyboard.append([InlineKeyboardButton("⬅ Назад до категорій", callback_data="catalog")])

    text = f"<b>{category.get('name', cat_key)}</b>\n\nОберіть бренд зі списку нижче:"
    image = category.get("image")

    await smart_edit_or_send(update, context, text, make_markup(keyboard), image=image)


async def brand_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    parts = q.data.split(":")
    if len(parts) < 3:
        await q.answer("❌ Некоректні дані", show_alert=True)
        return

    cat_key, brand_key = parts[1], parts[2]

    category = CATALOG.get("categories", {}).get(cat_key)
    brand = category.get("brands", {}).get(brand_key) if category else None

    if not brand:
        await q.message.reply_text("❌ Бренд не знайдено.")
        return

    keyboard = []
    for idx, parent_item in enumerate(brand.get("items", [])):
        if _has_nicotine_levels(parent_item):
            cb_data = f"nic:{cat_key}:{brand_key}:{idx}"
        else:
            cb_data = f"flavors:{cat_key}:{brand_key}:{idx}"

        keyboard.append([
            InlineKeyboardButton(parent_item.get("name", f"Позиція {idx+1}"), callback_data=cb_data)
        ])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"cat:{cat_key}")])

    text = f"<b>{brand.get('name', brand_key)}</b>\n\nОберіть позицію:"
    brand_image = brand.get("image") or category.get("image")

    await smart_edit_or_send(update, context, text, make_markup(keyboard), image=brand_image)


async def nicotine_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    parts = q.data.split(":")
    if len(parts) != 4:
        await q.answer("❌ Некоректні дані", show_alert=True)
        return

    _, cat_key, brand_key, p_idx_raw = parts
    p_idx = int(p_idx_raw)

    category = CATALOG["categories"][cat_key]
    brand = category["brands"][brand_key]
    parent = brand["items"][p_idx]

    nic_levels = parent.get("nicotine_levels", [])
    if not nic_levels:
        q.data = f"flavors:{cat_key}:{brand_key}:{p_idx}"
        await flavors_handler(update, context)
        return

    keyboard = []
    for nic in nic_levels:
        keyboard.append([
            InlineKeyboardButton(
                f"⚡ {nic}",
                callback_data=f"flavors:{cat_key}:{brand_key}:{p_idx}:{nic}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"brand:{cat_key}:{brand_key}")])

    text = f"Виберіть міцність для <b>{parent.get('name', 'товару')}</b>:"
    image = parent.get("image") or brand.get("image") or category.get("image")

    await smart_edit_or_send(update, context, text, make_markup(keyboard), image=image)


async def flavors_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    parts = q.data.split(":")
    if len(parts) < 4:
        await q.answer("❌ Некоректні дані", show_alert=True)
        return

    cat_key = parts[1]
    brand_key = parts[2]
    p_idx = int(parts[3])
    selected_nic = parts[4] if len(parts) > 4 else None

    category = CATALOG.get("categories", {}).get(cat_key)
    brand = category.get("brands", {}).get(brand_key) if category else None
    parent = brand["items"][p_idx] if brand else None

    if not parent:
        await q.answer("❌ Товар не знайдено", show_alert=True)
        return

    flavor_list = parent.get("items", [])
    keyboard = []

    if flavor_list:
        for f_idx, fl in enumerate(flavor_list):
            flavor_name = _extract_flavor_name(fl)
            if selected_nic:
                cb_data = f"show_flv:{cat_key}:{brand_key}:{p_idx}:{f_idx}:{selected_nic}"
            else:
                cb_data = f"show_flv:{cat_key}:{brand_key}:{p_idx}:{f_idx}"

            keyboard.append([InlineKeyboardButton(flavor_name, callback_data=cb_data)])
        back_cb = f"nic:{cat_key}:{brand_key}:{p_idx}" if _has_nicotine_levels(parent) else f"brand:{cat_key}:{brand_key}"
    else:
        # якщо смаків немає — показуємо сам товар
        if selected_nic:
            q.data = f"show_flv:{cat_key}:{brand_key}:{p_idx}:-1:{selected_nic}"
        else:
            q.data = f"show_flv:{cat_key}:{brand_key}:{p_idx}:-1"
        await show_item_before_add(update, context)
        return

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=back_cb)])

    title = parent.get("name", "Товар")
    if selected_nic:
        title += f" | {selected_nic}"

    text = f"<b>{title}</b>\n\nОберіть смак/колір:"
    image = parent.get("image") or brand.get("image") or category.get("image")

    await smart_edit_or_send(update, context, text, make_markup(keyboard), image=image)


async def show_item_before_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    parts = q.data.split(":")
    if len(parts) < 5:
        await q.answer("❌ Некоректні дані", show_alert=True)
        return

    cat_key = parts[1]
    brand_key = parts[2]
    item_idx = int(parts[3])
    f_idx = int(parts[4]) if parts[4] != "" else -1
    selected_nic = parts[5] if len(parts) > 5 else None

    category = CATALOG.get("categories", {}).get(cat_key, {})
    brand = category.get("brands", {}).get(brand_key, {})
    products = brand.get("items", [])

    if item_idx >= len(products):
        await q.message.reply_text("❌ Помилка: товар не знайдено.")
        return

    item = products[item_idx]
    selected_flavor = ""

    if f_idx >= 0 and "items" in item and f_idx < len(item["items"]):
        fl_obj = item["items"][f_idx]
        selected_flavor = fl_obj.get("name") if isinstance(fl_obj, dict) else str(fl_obj)

    title_parts = [item.get("name", "Товар")]
    if selected_nic:
        title_parts.append(selected_nic)
    if selected_flavor:
        title_parts.append(selected_flavor)

    title = " — ".join(title_parts)
    price = float(item.get("price", 0))

    text = (
        f"<b>{title}</b>\n\n"
        f"📝 {item.get('description', 'Опис відсутній')}\n"
        f"💰 Ціна: <b>{price:g}€</b>\n"
    )

    if selected_nic:
        add_cb = f"add_confirm:{cat_key}:{brand_key}:{item_idx}:{f_idx}:{selected_nic}"
    else:
        add_cb = f"add_confirm:{cat_key}:{brand_key}:{item_idx}:{f_idx}"

    if _has_nicotine_levels(item):
        back_cb = f"flavors:{cat_key}:{brand_key}:{item_idx}:{selected_nic}" if selected_nic else f"nic:{cat_key}:{brand_key}:{item_idx}"
    else:
        back_cb = f"flavors:{cat_key}:{brand_key}:{item_idx}"

    keyboard = [
        [InlineKeyboardButton("➕ Додати в кошик", callback_data=add_cb)],
        [InlineKeyboardButton("⬅ Назад", callback_data=back_cb)]
    ]

    image = item.get("image") or category.get("image") or brand.get("image")
    await smart_edit_or_send(update, context, text, make_markup(keyboard), image=image)


async def add_to_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    parts = q.data.split(":")
    if len(parts) < 5:
        await q.answer("❌ Сталася помилка даних", show_alert=True)
        return

    cat_key = parts[1]
    brand_key = parts[2]
    item_idx = int(parts[3])
    f_idx = int(parts[4]) if parts[4] != "" else -1
    selected_nic = parts[5] if len(parts) > 5 else None

    try:
        base_item = CATALOG["categories"][cat_key]["brands"][brand_key]["items"][item_idx]

        cart_name_parts = [base_item.get("name", "Товар")]

        if selected_nic:
            cart_name_parts.append(selected_nic)

        if f_idx >= 0 and "items" in base_item and f_idx < len(base_item["items"]):
            fl_obj = base_item["items"][f_idx]
            flavor_name = fl_obj.get("name") if isinstance(fl_obj, dict) else str(fl_obj)
            cart_name_parts.append(flavor_name)

        cart_item = {
            "name": " | ".join(cart_name_parts),
            "price": float(base_item.get("price", 0)),
        }

        user_id = update.effective_user.id
        user_carts.setdefault(user_id, [])
        user_carts[user_id].append(cart_item)

        await q.answer("🛒 Додано в кошик!")
        await catalog_menu(update, context)

    except Exception as e:
        logger.exception(f"Помилка додавання в кошик: {e}")
        await q.answer("❌ Помилка при додаванні!", show_alert=True)


async def cart_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = update.effective_user.id
    cart = user_carts.get(user_id, [])

    if not cart:
        text = "🛒 <b>Ваш кошик порожній</b>"
        keyboard = [[InlineKeyboardButton("⬅ Назад до каталогу", callback_data="catalog")]]
    else:
        items_text, total = _format_cart_items(cart)
        text = f"🛒 <b>Ваш кошик:</b>\n\n{items_text}\n💰 Разом: <b>{total:g}€</b>"
        keyboard = [
            [InlineKeyboardButton("✅ Оформити замовлення", callback_data="checkout")],
            [InlineKeyboardButton("🗑 Очистити кошик", callback_data="clear_cart")],
            [InlineKeyboardButton("⬅ Назад", callback_data="catalog")],
        ]

    await smart_edit_or_send(update, context, text, make_markup(keyboard))


async def clear_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_carts[user_id] = []
    await update.callback_query.answer("🧹 Кошик очищено")
    await cart_view_handler(update, context)


async def remove_one_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = update.effective_user.id

    if user_id in user_carts and user_carts[user_id]:
        removed_item = user_carts[user_id].pop()
        await q.answer(f"❌ Видалено: {removed_item['name']}")
    else:
        await q.answer("Кошик уже порожній")

    await cart_view_handler(update, context)


async def reserve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("⏳ Функція резерву в розробці", show_alert=True)


async def checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id

    cart = user_carts.get(user_id, [])
    if not cart:
        keyboard = [[InlineKeyboardButton("📦 Перейти до каталогу", callback_data="catalog")]]
        await smart_edit_or_send(
            update,
            context,
            "🛒 <b>Ваша корзина порожня!</b>\n\nДодайте щось, щоб зробити замовлення.",
            make_markup(keyboard)
        )
        return

    try:
        user_city = user_cities.get(user_id, "Не вказано")
        username = q.from_user.username or "приховано"
        order_id = datetime.now().strftime("%H%M%S")

        order_text, total_price, items_lines = _build_order_message(
            order_id=order_id,
            city=user_city,
            user_id=user_id,
            full_name=q.from_user.full_name,
            username=username,
            cart=cart
        )

        # 1. Відправка адмінам
        admin_errors = []
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=order_text,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Не вдалося надіслати замовлення адміну {admin_id}: {e}")
                admin_errors.append(admin_id)

        # 2. Відправка потрібному кур'єру
        courier = get_courier_for_city(user_city)
        courier_sent = False
        courier_name = "Кур'єр"

        if courier:
            courier_name = courier.get("name", "Кур'єр")
            courier_chat_id = courier["chat_id"]

            try:
                await context.bot.send_message(
                    chat_id=courier_chat_id,
                    text=order_text,
                    parse_mode="HTML"
                )
                courier_sent = True
            except Exception as e:
                logger.error(
                    f"Не вдалося надіслати замовлення кур'єру '{courier_name}' "
                    f"для міста '{user_city}' (chat_id={courier_chat_id}): {e}"
                )
        else:
            logger.warning(f"Для міста '{user_city}' не знайдено маршруту кур'єра.")

        # 3. Очищення кошика
        user_carts[user_id] = []

        try:
            await q.message.delete()
        except Exception:
            pass

        # 4. Підтвердження користувачу
        if courier_sent:
            courier_status = (
                f"{courier_name} вже отримав ваше замовлення для міста <b>{user_city}</b>."
            )
        else:
            courier_status = (
                f"Замовлення отримано, але кур'єра для міста <b>{user_city}</b> "
                f"не вдалося сповістити автоматично. Адміністратор уже отримав заявку."
            )

        final_confirm = (
            f"✅ <b>Замовлення №{order_id} прийнято!</b>\n\n"
            f"{courier_status}\n"
            f"Очікуйте, з вами зв'яжуться найближчим часом.\n\n"
            f"<b>Ваш чек:</b>\n{items_lines}\n"
            f"💰 Сума: {total_price:g}€"
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=final_confirm,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.exception(f"Помилка в checkout_handler: {e}")
        await q.message.reply_text(
            "❌ Сталася помилка при оформленні замовлення. "
            "Перевір, чи бот доданий у чати кур'єрів і чи правильні chat_id."
        )


# ================== NAVIGATION ==================
async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data.split(":")
    target = data[1] if len(data) > 1 else "catalog"

    if target == "main":
        if q.message:
            try:
                await q.message.delete()
            except Exception:
                pass

        keyboard = [[InlineKeyboardButton("📦 Каталог", callback_data="catalog")]]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="👋 Вітаємо в ELF FOX!\nНатисніть кнопку нижче, щоб почати.",
            reply_markup=make_markup(keyboard)
        )

    elif target == "catalog":
        await catalog_menu(update, context)

    elif target == "cat":
        if len(data) > 2:
            q.data = f"cat:{data[2]}"
            await category_handler(update, context)
        else:
            await catalog_menu(update, context)

    elif target == "brand":
        if len(data) > 3:
            q.data = f"brand:{data[2]}:{data[3]}"
            await brand_handler(update, context)
        else:
            await catalog_menu(update, context)


# ================== ADMIN ==================
async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        return

    try:
        await update.message.delete()
    except Exception:
        pass

    keyboard = [
        [InlineKeyboardButton("📁 Керування категоріями", callback_data="admin_cat:list")],
        [InlineKeyboardButton("🏠 Головне меню", callback_data="back:main")]
    ]

    await context.bot.send_message(
        chat_id=user_id,
        text="⚡ <b>Адмін-панель ELF FOX</b>\nОберіть розділ для редагування:",
        reply_markup=make_markup(keyboard),
        parse_mode="HTML"
    )


async def admin_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("🛠 Функція керування категоріями в розробці.")


async def admin_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def admin_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    keyboard = [
        [InlineKeyboardButton("📁 Керування категоріями", callback_data="admin_cat:list")],
        [InlineKeyboardButton("🏠 Головне меню", callback_data="back:main")]
    ]
    await smart_edit_or_send(
        update,
        context,
        "⚡ <b>Адмін-панель ELF FOX</b>\nОберіть розділ для редагування:",
        make_markup(keyboard)
    )


# ================== TEXT INPUT ==================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # якщо місто ще не встановлене — вважаємо, що це ввід міста
    if user_id not in user_cities or user_cities[user_id] == "Не вказано":
        user_cities[user_id] = text
        keyboard = [[InlineKeyboardButton("📦 Перейти до каталогу", callback_data="catalog")]]

        await update.message.reply_text(
            f"✅ Місто <b>{text}</b> встановлено!\nТепер ви можете відкрити каталог.",
            reply_markup=make_markup(keyboard),
            parse_mode="HTML"
        )

        # опціонально видаляємо повідомлення користувача
        try:
            await update.message.delete()
        except Exception:
            pass
    else:
        temp_msg = await update.message.reply_text("🦊 Використовуйте кнопки меню для навігації")
        await asyncio.sleep(2)
        try:
            await temp_msg.delete()
        except Exception:
            pass


async def set_city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = update.effective_user.id
    city_name = q.data.split(":")[1]

    if city_name == "other":
        try:
            await q.message.delete()
        except Exception:
            pass

        await context.bot.send_message(
            chat_id=user_id,
            text="✍️ <b>Будь ласка, напишіть назву вашого міста прямо сюди в чат:</b>",
            parse_mode="HTML"
        )
        return

    user_cities[user_id] = city_name
    await catalog_menu(update, context)


async def change_city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_cities[user_id] = "Не вказано"
    await catalog_menu(update, context)


# ================== MAIN ==================
def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_start))

    # Navigation
    app.add_handler(CallbackQueryHandler(catalog_menu, pattern=r"^catalog$"))
    app.add_handler(CallbackQueryHandler(category_handler, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(brand_handler, pattern=r"^brand:"))
    app.add_handler(CallbackQueryHandler(set_city_handler, pattern=r"^set_city:"))
    app.add_handler(CallbackQueryHandler(change_city_handler, pattern=r"^change_city$"))

    # Product flow
    app.add_handler(CallbackQueryHandler(nicotine_handler, pattern=r"^nic:"))
    app.add_handler(CallbackQueryHandler(flavors_handler, pattern=r"^flavors:"))
    app.add_handler(CallbackQueryHandler(show_item_before_add, pattern=r"^show_flv:"))

    # Cart & orders
    app.add_handler(CallbackQueryHandler(add_to_cart_handler, pattern=r"^add_confirm:"))
    app.add_handler(CallbackQueryHandler(cart_view_handler, pattern=r"^cart$"))
    app.add_handler(CallbackQueryHandler(checkout_handler, pattern=r"^checkout$"))
    app.add_handler(CallbackQueryHandler(clear_cart_handler, pattern=r"^clear_cart$"))
    app.add_handler(CallbackQueryHandler(remove_one_handler, pattern=r"^remove_one$"))
    app.add_handler(CallbackQueryHandler(reserve_handler, pattern=r"^reserve:"))

    # Back
    app.add_handler(CallbackQueryHandler(back_handler, pattern=r"^back:"))

    # Admin
    app.add_handler(CallbackQueryHandler(admin_cat, pattern=r"^admin_cat:"))
    app.add_handler(CallbackQueryHandler(admin_brand, pattern=r"^admin_brand:"))
    app.add_handler(CallbackQueryHandler(admin_block, pattern=r"^admin_block:"))
    app.add_handler(CallbackQueryHandler(admin_toggle, pattern=r"^admin_toggle:"))
    app.add_handler(CallbackQueryHandler(admin_back, pattern=r"^admin_back$"))

    # Text messages must be last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 ELF FOX BOT успішно запущений!")
    app.run_polling()


if __name__ == "__main__":
    main()
