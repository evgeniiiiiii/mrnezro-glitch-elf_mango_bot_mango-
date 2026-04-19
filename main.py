import os
import json
import html
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
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("elf_fox_bot")

# =========================================================
# PATHS
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
CATALOG_FILE = BASE_DIR / "catalog.json"
STATE_FILE = BASE_DIR / "runtime_state.json"

# =========================================================
# ENV CONFIG
# =========================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "").strip()

COURIER_ROUTES1_RAW = os.environ.get("COURIER_ROUTES1", "").strip()
COURIER_ROUTES2_RAW = os.environ.get("COURIER_ROUTES2", "").strip()
COURIER_ROUTES3_RAW = os.environ.get("COURIER_ROUTES3", "").strip()
COURIER_ROUTES4_RAW = os.environ.get("COURIER_ROUTES4", "").strip()

# =========================================================
# RUNTIME MEMORY
# =========================================================
user_carts: Dict[int, List[Dict[str, Any]]] = {}
user_cities: Dict[int, str] = {}

# =========================================================
# CONFIG HELPERS
# =========================================================
def parse_int_env(name: str, value: str, required: bool = True) -> Optional[int]:
    if not value:
        if required:
            raise ValueError(f"Не задано {name} у змінних середовища.")
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} має бути числом, наприклад -1001234567890")


def parse_admin_ids(raw_value: str) -> List[int]:
    if not raw_value:
        return []
    result = []
    for part in raw_value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            raise ValueError(f"ADMIN_IDS містить некоректне значення: {part}")
    return result


if not BOT_TOKEN:
    raise ValueError("Не задано BOT_TOKEN у змінних середовища.")

ADMIN_IDS = parse_admin_ids(ADMIN_IDS_RAW)
COURIER_ROUTES1 = parse_int_env("COURIER_ROUTES1", COURIER_ROUTES1_RAW, required=True)
COURIER_ROUTES2 = parse_int_env("COURIER_ROUTES2", COURIER_ROUTES2_RAW, required=True)
COURIER_ROUTES3 = parse_int_env("COURIER_ROUTES3", COURIER_ROUTES3_RAW, required=True)
COURIER_ROUTES4 = parse_int_env("COURIER_ROUTES4", COURIER_ROUTES4_RAW, required=False)

# =========================================================
# CATALOG
# =========================================================
def load_catalog() -> Dict[str, Any]:
    if not CATALOG_FILE.exists():
        raise FileNotFoundError(f"Файл каталогу не знайдено: {CATALOG_FILE}")

    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Помилка JSON у catalog.json: {e}")

    if not isinstance(data, dict):
        raise ValueError("catalog.json має бути JSON-об'єктом.")

    categories = data.get("categories")
    if not isinstance(categories, dict):
        raise ValueError("У catalog.json має бути ключ 'categories' типу object.")

    return data


CATALOG = load_catalog()

# =========================================================
# STATE PERSISTENCE
# =========================================================
def save_runtime_state() -> None:
    try:
        payload = {
            "user_carts": {str(k): v for k, v in user_carts.items()},
            "user_cities": {str(k): v for k, v in user_cities.items()},
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Не вдалося зберегти runtime_state.json: {e}")


def load_runtime_state() -> None:
    global user_carts, user_cities

    if not STATE_FILE.exists():
        user_carts = {}
        user_cities = {}
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        loaded_carts = data.get("user_carts", {})
        loaded_cities = data.get("user_cities", {})

        user_carts = {int(k): v for k, v in loaded_carts.items()}
        user_cities = {int(k): v for k, v in loaded_cities.items()}

        logger.info("runtime_state.json успішно завантажений")
    except Exception as e:
        logger.error(f"Не вдалося завантажити runtime_state.json: {e}")
        user_carts = {}
        user_cities = {}


load_runtime_state()

# =========================================================
# GENERAL HELPERS
# =========================================================
def normalize_city_name(city_name: str) -> str:
    return (city_name or "").strip().lower()


def make_markup(button_rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(button_rows)


def escape_html(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def format_price(value: Any) -> str:
    try:
        number = float(value)
        return f"{number:g}"
    except Exception:
        return "0"


def get_courier_for_city(city_name: str) -> Dict[str, Any]:
    """
    Логіка за твоїм запитом:
    Берлін  -> COURIER_ROUTES1
    Дрезден -> COURIER_ROUTES3
    Лейпциг -> COURIER_ROUTES2
    Інше    -> COURIER_ROUTES1
    """
    normalized = normalize_city_name(city_name)

    if normalized in ("берлін", "berlin"):
        return {"chat_id": COURIER_ROUTES1, "name": "Courier Route 1"}

    if normalized in ("дрезден", "dresden"):
        return {"chat_id": COURIER_ROUTES3, "name": "Courier Route 3"}

    if normalized in ("лейпциг", "leipzig"):
        return {"chat_id": COURIER_ROUTES2, "name": "Courier Route 2"}

    return {"chat_id": COURIER_ROUTES1, "name": "Courier Route 1"}


def ensure_user_state(user_id: int) -> None:
    if user_id not in user_carts:
        user_carts[user_id] = []
    if user_id not in user_cities:
        user_cities[user_id] = "Не вказано"


def _extract_flavor_name(flavor_obj: Any) -> str:
    if isinstance(flavor_obj, dict):
        return str(flavor_obj.get("name", "Невідомий смак"))
    return str(flavor_obj)


def _has_nicotine_levels(item: Dict[str, Any]) -> bool:
    return isinstance(item.get("nicotine_levels"), list) and len(item.get("nicotine_levels")) > 0


def _get_category(cat_key: str) -> Optional[Dict[str, Any]]:
    return CATALOG.get("categories", {}).get(cat_key)


def _get_brand(cat_key: str, brand_key: str) -> Optional[Dict[str, Any]]:
    category = _get_category(cat_key)
    if not category:
        return None
    return category.get("brands", {}).get(brand_key)


def _get_parent_item(cat_key: str, brand_key: str, item_idx: int) -> Optional[Dict[str, Any]]:
    brand = _get_brand(cat_key, brand_key)
    if not brand:
        return None

    items = brand.get("items", [])
    if not isinstance(items, list):
        return None

    if item_idx < 0 or item_idx >= len(items):
        return None

    return items[item_idx]


def _format_cart_items(cart: List[Dict[str, Any]]) -> Tuple[str, float]:
    items_text = ""
    total_price = 0.0

    for idx, item in enumerate(cart, start=1):
        price = float(item.get("price", 0))
        total_price += price
        item_name = escape_html(item.get("name", "Товар"))
        items_text += f"{idx}. {item_name} — {price:g}€\n"

    return items_text, total_price


def _build_order_message(
    order_id: str,
    city: str,
    user_id: int,
    full_name: str,
    username: str,
    cart: List[Dict[str, Any]]
) -> Tuple[str, float, str]:
    items_lines = ""
    total_price = 0.0

    for item in cart:
        price = float(item.get("price", 0))
        total_price += price
        item_name = escape_html(item.get("name", "Товар"))
        items_lines += f"• <b>{item_name}</b> — {price:g}€\n"

    safe_city = escape_html(city)
    safe_full_name = escape_html(full_name)
    safe_username = escape_html(username)

    order_text = (
        f"🛍️ <b>НОВЕ ЗАМОВЛЕННЯ №{escape_html(order_id)}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📍 <b>МІСТО: {safe_city.upper()}</b>\n"
        f"👤 Клієнт: {safe_full_name} (@{safe_username})\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📦 <b>Товари:</b>\n{items_lines}\n"
        f"💰 <b>РАЗОМ: {total_price:g}€</b>\n"
    )
    return order_text, total_price, items_lines


async def safe_delete_message(message) -> None:
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        pass


async def safe_answer_callback(query, text: Optional[str] = None, show_alert: bool = False) -> None:
    try:
        if text is None:
            await query.answer()
        else:
            await query.answer(text=text, show_alert=show_alert)
    except Exception:
        pass


async def safe_send_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "HTML"
):
    return await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode
    )


async def safe_send_photo(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    photo: str,
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "HTML"
):
    return await context.bot.send_photo(
        chat_id=chat_id,
        photo=photo,
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=parse_mode
    )


async def smart_edit_or_send(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    image: Optional[str] = None,
):
    q = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None

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

                await safe_send_photo(
                    context=context,
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

                await safe_send_message(
                    context=context,
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                return

    if image:
        await safe_send_photo(
            context=context,
            chat_id=chat_id,
            photo=image,
            caption=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    else:
        await safe_send_message(
            context=context,
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )


def build_start_keyboard() -> InlineKeyboardMarkup:
    return make_markup([[InlineKeyboardButton("📦 Каталог", callback_data="catalog")]])


def build_city_keyboard() -> InlineKeyboardMarkup:
    return make_markup([
        [InlineKeyboardButton("📍 Берлін", callback_data="set_city:Берлін")],
        [InlineKeyboardButton("📍 Дрезден", callback_data="set_city:Дрезден")],
        [InlineKeyboardButton("📍 Лейпциг", callback_data="set_city:Лейпциг")],
        [InlineKeyboardButton("🌍 Інше місто", callback_data="set_city:other")],
    ])


def build_catalog_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    for cat_key, cat_data in CATALOG.get("categories", {}).items():
        category_name = cat_data.get("name", cat_key)
        keyboard.append([InlineKeyboardButton(category_name, callback_data=f"cat:{cat_key}")])

    keyboard.append([InlineKeyboardButton("🛒 Кошик", callback_data="cart")])
    keyboard.append([InlineKeyboardButton("🏙 Змінити місто", callback_data="change_city")])
    return make_markup(keyboard)


# =========================================================
# COMMANDS
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user_state(user_id)
    save_runtime_state()

    await update.message.reply_text(
        "👋 Вітаємо в ELF FOX!\nНатисніть кнопку нижче, щоб почати.",
        reply_markup=build_start_keyboard()
    )


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user:
        return

    text = (
        f"<b>Chat info</b>\n"
        f"Назва: {escape_html(chat.title or user.full_name)}\n"
        f"Chat ID: <code>{chat.id}</code>\n"
        f"Chat type: <code>{chat.type}</code>\n"
        f"User ID: <code>{user.id}</code>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


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

# =========================================================
# MENUS
# =========================================================
async def catalog_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await safe_answer_callback(q)

    user_id = update.effective_user.id
    ensure_user_state(user_id)

    if user_cities.get(user_id, "Не вказано") == "Не вказано":
        text = "🏘 <b>Будь ласка, оберіть ваше місто для замовлення:</b>"
        markup = build_city_keyboard()
    else:
        current_city = escape_html(user_cities[user_id])
        text = f"📍 Ваше місто: <b>{current_city}</b>\n\n📦 <b>Оберіть категорію:</b>"
        markup = build_catalog_keyboard()

    await smart_edit_or_send(update, context, text, markup)


async def category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    parts = q.data.split(":", 1)
    if len(parts) != 2:
        await safe_answer_callback(q, "❌ Некоректна категорія", True)
        return

    cat_key = parts[1]
    category = _get_category(cat_key)

    if not category:
        await q.message.reply_text("❌ Помилка: категорію не знайдено.")
        return

    keyboard = []
    for b_key, b_data in category.get("brands", {}).items():
        keyboard.append([
            InlineKeyboardButton(
                b_data.get("name", b_key),
                callback_data=f"brand:{cat_key}:{b_key}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅ Назад до категорій", callback_data="catalog")])

    text = f"<b>{escape_html(category.get('name', cat_key))}</b>\n\nОберіть бренд зі списку нижче:"
    image = category.get("image")

    await smart_edit_or_send(update, context, text, make_markup(keyboard), image=image)


async def brand_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    parts = q.data.split(":")
    if len(parts) < 3:
        await safe_answer_callback(q, "❌ Некоректні дані", True)
        return

    cat_key, brand_key = parts[1], parts[2]
    category = _get_category(cat_key)
    brand = _get_brand(cat_key, brand_key)

    if not category or not brand:
        await q.message.reply_text("❌ Бренд не знайдено.")
        return

    keyboard = []
    for idx, parent_item in enumerate(brand.get("items", [])):
        if _has_nicotine_levels(parent_item):
            cb_data = f"nic:{cat_key}:{brand_key}:{idx}"
        else:
            cb_data = f"flavors:{cat_key}:{brand_key}:{idx}"

        keyboard.append([
            InlineKeyboardButton(
                parent_item.get("name", f"Позиція {idx+1}"),
                callback_data=cb_data
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"cat:{cat_key}")])

    text = f"<b>{escape_html(brand.get('name', brand_key))}</b>\n\nОберіть позицію:"
    brand_image = brand.get("image") or category.get("image")

    await smart_edit_or_send(update, context, text, make_markup(keyboard), image=brand_image)


async def nicotine_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    parts = q.data.split(":")
    if len(parts) != 4:
        await safe_answer_callback(q, "❌ Некоректні дані", True)
        return

    _, cat_key, brand_key, p_idx_raw = parts
    try:
        p_idx = int(p_idx_raw)
    except ValueError:
        await safe_answer_callback(q, "❌ Некоректний індекс товару", True)
        return

    category = _get_category(cat_key)
    brand = _get_brand(cat_key, brand_key)
    parent = _get_parent_item(cat_key, brand_key, p_idx)

    if not category or not brand or not parent:
        await safe_answer_callback(q, "❌ Товар не знайдено", True)
        return

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

    text = f"Виберіть міцність для <b>{escape_html(parent.get('name', 'товару'))}</b>:"
    image = parent.get("image") or brand.get("image") or category.get("image")

    await smart_edit_or_send(update, context, text, make_markup(keyboard), image=image)


async def flavors_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    parts = q.data.split(":")
    if len(parts) < 4:
        await safe_answer_callback(q, "❌ Некоректні дані", True)
        return

    cat_key = parts[1]
    brand_key = parts[2]

    try:
        p_idx = int(parts[3])
    except ValueError:
        await safe_answer_callback(q, "❌ Некоректний індекс товару", True)
        return

    selected_nic = parts[4] if len(parts) > 4 else None

    category = _get_category(cat_key)
    brand = _get_brand(cat_key, brand_key)
    parent = _get_parent_item(cat_key, brand_key, p_idx)

    if not category or not brand or not parent:
        await safe_answer_callback(q, "❌ Товар не знайдено", True)
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

    text = f"<b>{escape_html(title)}</b>\n\nОберіть смак/колір:"
    image = parent.get("image") or brand.get("image") or category.get("image")

    await smart_edit_or_send(update, context, text, make_markup(keyboard), image=image)


async def show_item_before_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    parts = q.data.split(":")
    if len(parts) < 5:
        await safe_answer_callback(q, "❌ Некоректні дані", True)
        return

    cat_key = parts[1]
    brand_key = parts[2]

    try:
        item_idx = int(parts[3])
        f_idx = int(parts[4]) if parts[4] != "" else -1
    except ValueError:
        await safe_answer_callback(q, "❌ Помилка індексу", True)
        return

    selected_nic = parts[5] if len(parts) > 5 else None

    category = _get_category(cat_key) or {}
    brand = _get_brand(cat_key, brand_key) or {}
    item = _get_parent_item(cat_key, brand_key, item_idx)

    if not item:
        await q.message.reply_text("❌ Помилка: товар не знайдено.")
        return

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
        f"<b>{escape_html(title)}</b>\n\n"
        f"📝 {escape_html(item.get('description', 'Опис відсутній'))}\n"
        f"💰 Ціна: <b>{price:g}€</b>\n"
    )

    if selected_nic:
        add_cb = f"add_confirm:{cat_key}:{brand_key}:{item_idx}:{f_idx}:{selected_nic}"
    else:
        add_cb = f"add_confirm:{cat_key}:{brand_key}:{item_idx}:{f_idx}"

    if _has_nicotine_levels(item):
        back_cb = (
            f"flavors:{cat_key}:{brand_key}:{item_idx}:{selected_nic}"
            if selected_nic else
            f"nic:{cat_key}:{brand_key}:{item_idx}"
        )
    else:
        back_cb = f"flavors:{cat_key}:{brand_key}:{item_idx}"

    keyboard = [
        [InlineKeyboardButton("➕ Додати в кошик", callback_data=add_cb)],
        [InlineKeyboardButton("⬅ Назад", callback_data=back_cb)]
    ]

    image = item.get("image") or category.get("image") or brand.get("image")
    await smart_edit_or_send(update, context, text, make_markup(keyboard), image=image)

# =========================================================
# CART
# =========================================================
async def add_to_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    parts = q.data.split(":")
    if len(parts) < 5:
        await safe_answer_callback(q, "❌ Сталася помилка даних", True)
        return

    cat_key = parts[1]
    brand_key = parts[2]

    try:
        item_idx = int(parts[3])
        f_idx = int(parts[4]) if parts[4] != "" else -1
    except ValueError:
        await safe_answer_callback(q, "❌ Некоректний індекс товару", True)
        return

    selected_nic = parts[5] if len(parts) > 5 else None

    try:
        base_item = _get_parent_item(cat_key, brand_key, item_idx)
        if not base_item:
            await safe_answer_callback(q, "❌ Товар не знайдено", True)
            return

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
            "created_at": datetime.now().isoformat(),
        }

        user_id = update.effective_user.id
        ensure_user_state(user_id)
        user_carts[user_id].append(cart_item)
        save_runtime_state()

        await safe_answer_callback(q, "🛒 Додано в кошик!")
        await catalog_menu(update, context)

    except Exception as e:
        logger.exception(f"Помилка додавання в кошик: {e}")
        await safe_answer_callback(q, "❌ Помилка при додаванні!", True)


async def cart_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    user_id = update.effective_user.id
    ensure_user_state(user_id)
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
            [InlineKeyboardButton("➖ Видалити останній товар", callback_data="remove_one")],
            [InlineKeyboardButton("⬅ Назад", callback_data="catalog")],
        ]

    await smart_edit_or_send(update, context, text, make_markup(keyboard))


async def clear_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = update.effective_user.id
    ensure_user_state(user_id)

    user_carts[user_id] = []
    save_runtime_state()

    await safe_answer_callback(q, "🧹 Кошик очищено")
    await cart_view_handler(update, context)


async def remove_one_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = update.effective_user.id
    ensure_user_state(user_id)

    if user_carts[user_id]:
        removed_item = user_carts[user_id].pop()
        save_runtime_state()
        await safe_answer_callback(q, f"❌ Видалено: {removed_item['name']}")
    else:
        await safe_answer_callback(q, "Кошик уже порожній")

    await cart_view_handler(update, context)


async def reserve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q, "⏳ Функція резерву в розробці", True)

# =========================================================
# ORDER / CHECKOUT
# =========================================================
async def notify_admins(context: ContextTypes.DEFAULT_TYPE, order_text: str) -> List[int]:
    failed_admins = []

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=order_text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Не вдалося надіслати замовлення адміну {admin_id}: {e}")
            failed_admins.append(admin_id)

    return failed_admins


async def notify_courier(context: ContextTypes.DEFAULT_TYPE, city_name: str, order_text: str) -> Tuple[bool, str, int]:
    courier = get_courier_for_city(city_name)
    courier_name = courier.get("name", "Кур'єр")
    courier_chat_id = courier["chat_id"]

    try:
        await context.bot.send_message(
            chat_id=courier_chat_id,
            text=order_text,
            parse_mode="HTML"
        )
        return True, courier_name, courier_chat_id
    except Exception as e:
        logger.error(
            f"Не вдалося надіслати замовлення кур'єру '{courier_name}' "
            f"для міста '{city_name}' (chat_id={courier_chat_id}): {e}"
        )
        return False, courier_name, courier_chat_id


async def checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    user_id = q.from_user.id
    ensure_user_state(user_id)

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
        await context.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)

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

        failed_admins = await notify_admins(context, order_text)
        courier_sent, courier_name, courier_chat_id = await notify_courier(context, user_city, order_text)

        # очищаємо кошик тільки після спроби розсилки
        user_carts[user_id] = []
        save_runtime_state()

        await safe_delete_message(q.message)

        if courier_sent:
            courier_status = (
                f"{escape_html(courier_name)} вже отримав ваше замовлення для міста "
                f"<b>{escape_html(user_city)}</b>."
            )
        else:
            courier_status = (
                f"Замовлення отримано, але кур'єра для міста <b>{escape_html(user_city)}</b> "
                f"не вдалося сповістити автоматично. Адміністратори вже отримали заявку."
            )

        if failed_admins:
            admin_status = (
                f"\n\n⚠️ Не всім адміністраторам вдалося надіслати повідомлення "
                f"(помилка в {len(failed_admins)} чатах)."
            )
        else:
            admin_status = ""

        final_confirm = (
            f"✅ <b>Замовлення №{escape_html(order_id)} прийнято!</b>\n\n"
            f"{courier_status}\n"
            f"Очікуйте, з вами зв'яжуться найближчим часом.\n\n"
            f"<b>Ваш чек:</b>\n{items_lines}\n"
            f"💰 Сума: {total_price:g}€"
            f"{admin_status}"
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

# =========================================================
# NAVIGATION
# =========================================================
async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    data = q.data.split(":")
    target = data[1] if len(data) > 1 else "catalog"

    if target == "main":
        await safe_delete_message(q.message)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="👋 Вітаємо в ELF FOX!\nНатисніть кнопку нижче, щоб почати.",
            reply_markup=build_start_keyboard()
        )
        return

    if target == "catalog":
        await catalog_menu(update, context)
        return

    if target == "cat":
        if len(data) > 2:
            q.data = f"cat:{data[2]}"
            await category_handler(update, context)
        else:
            await catalog_menu(update, context)
        return

    if target == "brand":
        if len(data) > 3:
            q.data = f"brand:{data[2]}:{data[3]}"
            await brand_handler(update, context)
        else:
            await catalog_menu(update, context)
        return

# =========================================================
# ADMIN STUBS
# =========================================================
async def admin_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)
    await q.message.reply_text("🛠 Функція керування категоріями в розробці.")


async def admin_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)


async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)


async def admin_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)


async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

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

# =========================================================
# CITY INPUT
# =========================================================
async def set_city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    user_id = update.effective_user.id
    ensure_user_state(user_id)

    city_name = q.data.split(":")[1]

    if city_name == "other":
        await safe_delete_message(q.message)
        await context.bot.send_message(
            chat_id=user_id,
            text="✍️ <b>Будь ласка, напишіть назву вашого міста прямо сюди в чат:</b>",
            parse_mode="HTML"
        )
        return

    user_cities[user_id] = city_name
    save_runtime_state()
    await catalog_menu(update, context)


async def change_city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user_state(user_id)
    user_cities[user_id] = "Не вказано"
    save_runtime_state()
    await catalog_menu(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user_state(user_id)

    text = (update.message.text or "").strip()
    if not text:
        return

    # якщо місто ще не встановлене — вважаємо це введенням міста
    if user_cities.get(user_id, "Не вказано") == "Не вказано":
        user_cities[user_id] = text
        save_runtime_state()

        keyboard = [[InlineKeyboardButton("📦 Перейти до каталогу", callback_data="catalog")]]

        await update.message.reply_text(
            f"✅ Місто <b>{escape_html(text)}</b> встановлено!\nТепер ви можете відкрити каталог.",
            reply_markup=make_markup(keyboard),
            parse_mode="HTML"
        )

        try:
            await update.message.delete()
        except Exception:
            pass

        return

    temp_msg = await update.message.reply_text("🦊 Використовуйте кнопки меню для навігації")
    await asyncio.sleep(2)
    try:
        await temp_msg.delete()
    except Exception:
        pass

# =========================================================
# ERROR HANDLER
# =========================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception while processing update:", exc_info=context.error)

# =========================================================
# MAIN
# =========================================================
def main():
    logger.info("Запуск ELF FOX BOT...")
    logger.info(f"ADMIN_IDS count: {len(ADMIN_IDS)}")
    logger.info(f"COURIER_ROUTES1: {COURIER_ROUTES1}")
    logger.info(f"COURIER_ROUTES2: {COURIER_ROUTES2}")
    logger.info(f"COURIER_ROUTES3: {COURIER_ROUTES3}")
    logger.info(f"COURIER_ROUTES4: {COURIER_ROUTES4}")

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
    app.add_handler(CommandHandler("chatid", chatid_command))

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

    # Text handler must be last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Global error handler
    app.add_error_handler(error_handler)

    logger.info("🚀 ELF FOX BOT успішно запущений!")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
