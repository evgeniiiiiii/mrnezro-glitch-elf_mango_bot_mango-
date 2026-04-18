import os
import json
import logging
import html
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut, NetworkError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================== LOGGING ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "").strip()
GENERAL_COURIER_CHAT_ID_RAW = os.environ.get("GENERAL_COURIER_CHAT_ID", "").strip()
CATALOG_PATH = os.environ.get("CATALOG_PATH", "catalog.json").strip()

if not BOT_TOKEN:
    raise ValueError("Не задано BOT_TOKEN у змінних середовища.")

if not GENERAL_COURIER_CHAT_ID_RAW:
    raise ValueError("Не задано GENERAL_COURIER_CHAT_ID у змінних середовища.")

try:
    GENERAL_COURIER_CHAT_ID = int(GENERAL_COURIER_CHAT_ID_RAW)
except ValueError as exc:
    raise ValueError("GENERAL_COURIER_CHAT_ID має бути числом, наприклад -1001234567890") from exc

ADMIN_IDS: List[int] = []
if ADMIN_IDS_RAW:
    try:
        ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()]
    except ValueError as exc:
        raise ValueError("ADMIN_IDS має містити числа через кому, наприклад: 123,456") from exc

# ================== IN-MEMORY STORAGE ==================
# Після перезапуску все очищується
user_carts: Dict[int, List[Dict[str, Any]]] = {}
user_cities: Dict[int, str] = {}

# ================== CATALOG ==================
def load_catalog(path: str) -> Dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Файл каталогу не знайдено: {file_path.resolve()}")

    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("catalog.json має містити JSON-об'єкт.")
    if "categories" not in data or not isinstance(data["categories"], dict):
        raise ValueError("У catalog.json має бути ключ 'categories' типу object.")

    return data


CATALOG = load_catalog(CATALOG_PATH)

# ================== HELPERS ==================
def escape_text(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""

def get_user_cart(user_id: int) -> List[Dict[str, Any]]:
    if user_id not in user_carts:
        user_carts[user_id] = []
    return user_carts[user_id]

def get_user_city(user_id: int) -> str:
    return user_cities.get(user_id, "Не вказано")

def ensure_user_defaults(user_id: int) -> None:
    get_user_cart(user_id)
    if user_id not in user_cities:
        user_cities[user_id] = "Не вказано"

def _extract_flavor_name(flavor_obj: Any) -> str:
    if isinstance(flavor_obj, dict):
        return str(flavor_obj.get("name", "Невідомий смак"))
    return str(flavor_obj)

def _extract_flavor_image(flavor_obj: Any) -> Optional[str]:
    if isinstance(flavor_obj, dict):
        image = flavor_obj.get("image")
        if image:
            return str(image)
    return None

def get_category(cat_key: str) -> Optional[Dict[str, Any]]:
    return CATALOG.get("categories", {}).get(cat_key)

def get_brand(cat_key: str, brand_key: str) -> Optional[Dict[str, Any]]:
    category = get_category(cat_key)
    if not category:
        return None
    return category.get("brands", {}).get(brand_key)

def get_parent_item(cat_key: str, brand_key: str, item_idx: int) -> Optional[Dict[str, Any]]:
    brand = get_brand(cat_key, brand_key)
    if not brand:
        return None
    items = brand.get("items", [])
    if not isinstance(items, list) or item_idx < 0 or item_idx >= len(items):
        return None
    item = items[item_idx]
    return item if isinstance(item, dict) else None

def build_back_to_item_callback(cat_key: str, brand_key: str, item_idx: int) -> str:
    return f"item:{cat_key}:{brand_key}:{item_idx}"

def build_back_to_flavors_callback(cat_key: str, brand_key: str, item_idx: int) -> str:
    return f"flavors:{cat_key}:{brand_key}:{item_idx}"

def resolve_display_image(
    category: Optional[Dict[str, Any]] = None,
    brand: Optional[Dict[str, Any]] = None,
    item: Optional[Dict[str, Any]] = None,
    flavor_obj: Optional[Any] = None,
) -> Optional[str]:
    return (
        _extract_flavor_image(flavor_obj)
        or (item.get("image") if item else None)
        or (brand.get("image") if brand else None)
        or (category.get("image") if category else None)
    )

async def safe_answer_callback(update: Update, text: Optional[str] = None, show_alert: bool = False) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer(text=text, show_alert=show_alert)
    except BadRequest as e:
        if "Query is too old" not in str(e):
            logger.warning("Не вдалося відповісти на callback query: %s", e)

async def send_or_edit_content(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    image: Optional[str] = None,
) -> None:
    """
    Якщо можна — редагуємо існуюче повідомлення.
    Якщо ні — надсилаємо нове.
    """
    query = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None

    if query:
        try:
            if image:
                await query.edit_message_media(
                    media=InputMediaPhoto(
                        media=image,
                        caption=text,
                        parse_mode=ParseMode.HTML,
                    ),
                    reply_markup=reply_markup,
                )
            else:
                await query.edit_message_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                )
            return
        except BadRequest as e:
            err = str(e).lower()
            logger.info("Не вдалося відредагувати повідомлення, буде fallback: %s", e)

            # якщо контент той самий — не падаємо
            if "message is not modified" in err:
                return

            # спроба видалити старе повідомлення
            try:
                await query.message.delete()
            except Exception as delete_err:
                logger.debug("Не вдалося видалити старе повідомлення: %s", delete_err)

        except (TimedOut, NetworkError) as e:
            logger.warning("Проблема мережі при редагуванні повідомлення: %s", e)
        except Exception as e:
            logger.exception("Невідома помилка при редагуванні повідомлення: %s", e)

    if not chat_id:
        return

    try:
        if image:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=image,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logger.exception("Не вдалося надіслати повідомлення: %s", e)

def build_catalog_keyboard() -> InlineKeyboardMarkup:
    keyboard: List[List[InlineKeyboardButton]] = []

    for cat_key, cat_data in CATALOG.get("categories", {}).items():
        cat_name = cat_data.get("name", cat_key)
        keyboard.append([InlineKeyboardButton(str(cat_name), callback_data=f"cat:{cat_key}")])

    keyboard.append([InlineKeyboardButton("🛒 Кошик", callback_data="cart")])
    keyboard.append([InlineKeyboardButton("🏙 Змінити місто", callback_data="change_city")])

    return InlineKeyboardMarkup(keyboard)

def build_city_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📍 Берлін", callback_data="set_city:Берлін")],
        [InlineKeyboardButton("📍 Дрезден", callback_data="set_city:Дрезден")],
        [InlineKeyboardButton("📍 Лейпциг", callback_data="set_city:Лейпциг")],
        [InlineKeyboardButton("🌍 Інше місто", callback_data="set_city:other")],
    ]
    return InlineKeyboardMarkup(keyboard)

def build_brand_keyboard(cat_key: str, category: Dict[str, Any]) -> InlineKeyboardMarkup:
    keyboard: List[List[InlineKeyboardButton]] = []
    brands = category.get("brands", {})

    for brand_key, brand_data in brands.items():
        brand_name = str(brand_data.get("name", brand_key))
        keyboard.append([InlineKeyboardButton(brand_name, callback_data=f"brand:{cat_key}:{brand_key}")])

    keyboard.append([InlineKeyboardButton("⬅ Назад до категорій", callback_data="catalog")])
    return InlineKeyboardMarkup(keyboard)

def build_items_keyboard(cat_key: str, brand_key: str, brand: Dict[str, Any]) -> InlineKeyboardMarkup:
    keyboard: List[List[InlineKeyboardButton]] = []
    items = brand.get("items", [])

    for idx, parent_item in enumerate(items):
        if not isinstance(parent_item, dict):
            continue
        name = str(parent_item.get("name", f"Товар {idx + 1}"))
        nicotine_levels = parent_item.get("nicotine_levels", [])
        if isinstance(nicotine_levels, list) and nicotine_levels:
            callback = f"nic:{cat_key}:{brand_key}:{idx}"
        else:
            callback = f"flavors:{cat_key}:{brand_key}:{idx}"
        keyboard.append([InlineKeyboardButton(name, callback_data=callback)])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"cat:{cat_key}")])
    return InlineKeyboardMarkup(keyboard)

def build_nicotine_keyboard(cat_key: str, brand_key: str, item_idx: int, nic_levels: List[Any]) -> InlineKeyboardMarkup:
    keyboard: List[List[InlineKeyboardButton]] = []

    for nic in nic_levels:
        nic_name = str(nic)
        keyboard.append([
            InlineKeyboardButton(
                f"⚡ {nic_name}",
                callback_data=f"flavors:{cat_key}:{brand_key}:{item_idx}:{nic_name}",
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"brand:{cat_key}:{brand_key}")])
    return InlineKeyboardMarkup(keyboard)

def build_flavors_keyboard(
    cat_key: str,
    brand_key: str,
    item_idx: int,
    flavors: List[Any],
    selected_nicotine: Optional[str] = None,
) -> InlineKeyboardMarkup:
    keyboard: List[List[InlineKeyboardButton]] = []

    for f_idx, flavor_obj in enumerate(flavors):
        flavor_name = _extract_flavor_name(flavor_obj)
        if selected_nicotine:
            callback = f"item:{cat_key}:{brand_key}:{item_idx}:{f_idx}:{selected_nicotine}"
        else:
            callback = f"item:{cat_key}:{brand_key}:{item_idx}:{f_idx}"
        keyboard.append([InlineKeyboardButton(flavor_name, callback_data=callback)])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"brand:{cat_key}:{brand_key}")])
    return InlineKeyboardMarkup(keyboard)

def build_item_keyboard(
    cat_key: str,
    brand_key: str,
    item_idx: int,
    flavor_idx: Optional[int] = None,
    selected_nicotine: Optional[str] = None,
) -> InlineKeyboardMarkup:
    if flavor_idx is not None and selected_nicotine:
        add_cb = f"add_confirm:{cat_key}:{brand_key}:{item_idx}:{flavor_idx}:{selected_nicotine}"
        back_cb = f"flavors:{cat_key}:{brand_key}:{item_idx}:{selected_nicotine}"
    elif flavor_idx is not None:
        add_cb = f"add_confirm:{cat_key}:{brand_key}:{item_idx}:{flavor_idx}"
        back_cb = f"flavors:{cat_key}:{brand_key}:{item_idx}"
    else:
        add_cb = f"add_confirm:{cat_key}:{brand_key}:{item_idx}"
        back_cb = f"brand:{cat_key}:{brand_key}"

    keyboard = [
        [InlineKeyboardButton("➕ Додати в кошик", callback_data=add_cb)],
        [InlineKeyboardButton("⬅ Назад", callback_data=back_cb)],
        [InlineKeyboardButton("🛒 Кошик", callback_data="cart")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ================== COMMANDS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    ensure_user_defaults(user_id)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📦 Каталог", callback_data="catalog")]]
    )

    await update.message.reply_text(
        "👋 Вітаємо в ELF FOX!\nНатисніть кнопку нижче, щоб почати.",
        reply_markup=keyboard,
    )

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        if update.message:
            await update.message.reply_text("⛔ У вас немає доступу до адмін-панелі.")
        return

    if update.message:
        try:
            await update.message.delete()
        except Exception as e:
            logger.debug("Не вдалося видалити /admin повідомлення: %s", e)

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📁 Керування категоріями", callback_data="admin_cat:list")],
            [InlineKeyboardButton("🏠 Головне меню", callback_data="catalog")],
        ]
    )

    await context.bot.send_message(
        chat_id=user_id,
        text="⚡ <b>Адмін-панель ELF FOX</b>\nОберіть розділ для редагування:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )

# ================== MENUS ==================
async def catalog_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    await safe_answer_callback(update)
    user_id = update.effective_user.id
    ensure_user_defaults(user_id)

    city = get_user_city(user_id)

    if city == "Не вказано":
        text = "🏘 <b>Будь ласка, оберіть ваше місто для замовлення:</b>"
        markup = build_city_keyboard()
    else:
        text = f"📍 Ваше місто: <b>{escape_text(city)}</b>\n\n📦 <b>Оберіть категорію:</b>"
        markup = build_catalog_keyboard()

    await send_or_edit_content(update, context, text=text, reply_markup=markup, image=None)

async def category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await safe_answer_callback(update)

    parts = query.data.split(":")
    if len(parts) != 2:
        return

    cat_key = parts[1]
    category = get_category(cat_key)

    if not category:
        await query.message.reply_text("❌ Помилка: категорію не знайдено.")
        return

    text = f"<b>{escape_text(category.get('name', cat_key))}</b>\n\nОберіть бренд зі списку нижче:"
    markup = build_brand_keyboard(cat_key, category)
    image = category.get("image")

    await send_or_edit_content(update, context, text=text, reply_markup=markup, image=image)

async def brand_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await safe_answer_callback(update)

    parts = query.data.split(":")
    if len(parts) != 3:
        return

    cat_key, brand_key = parts[1], parts[2]
    category = get_category(cat_key)
    brand = get_brand(cat_key, brand_key)

    if not category or not brand:
        await query.message.reply_text("❌ Бренд не знайдено.")
        return

    text = f"<b>{escape_text(brand.get('name', brand_key))}</b>\n\nОберіть позицію:"
    markup = build_items_keyboard(cat_key, brand_key, brand)
    image = brand.get("image") or category.get("image")

    await send_or_edit_content(update, context, text=text, reply_markup=markup, image=image)

async def nicotine_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await safe_answer_callback(update)

    parts = query.data.split(":")
    if len(parts) != 4:
        return

    _, cat_key, brand_key, item_idx_raw = parts

    try:
        item_idx = int(item_idx_raw)
    except ValueError:
        await query.message.reply_text("❌ Некоректний індекс товару.")
        return

    item = get_parent_item(cat_key, brand_key, item_idx)
    if not item:
        await query.message.reply_text("❌ Товар не знайдено.")
        return

    nic_levels = item.get("nicotine_levels", [])
    if not isinstance(nic_levels, list):
        nic_levels = []

    if not nic_levels:
        # якщо немає рівнів нікотину — одразу в смаки
        query.data = f"flavors:{cat_key}:{brand_key}:{item_idx}"
        await flavors_handler(update, context)
        return

    category = get_category(cat_key)
    brand = get_brand(cat_key, brand_key)
    text = f"Виберіть міцність для <b>{escape_text(item.get('name', 'Товар'))}</b>:"
    markup = build_nicotine_keyboard(cat_key, brand_key, item_idx, nic_levels)
    image = resolve_display_image(category=category, brand=brand, item=item)

    await send_or_edit_content(update, context, text=text, reply_markup=markup, image=image)

async def flavors_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await safe_answer_callback(update)

    parts = query.data.split(":")
    if len(parts) not in (4, 5):
        return

    _, cat_key, brand_key, item_idx_raw, *rest = parts

    try:
        item_idx = int(item_idx_raw)
    except ValueError:
        await query.message.reply_text("❌ Некоректний індекс товару.")
        return

    selected_nicotine = rest[0] if rest else None

    category = get_category(cat_key)
    brand = get_brand(cat_key, brand_key)
    item = get_parent_item(cat_key, brand_key, item_idx)

    if not category or not brand or not item:
        await query.message.reply_text("❌ Дані товару не знайдено.")
        return

    flavor_list = item.get("items", [])
    if not isinstance(flavor_list, list):
        flavor_list = []

    # якщо смаків немає — показуємо відразу картку товару
    if not flavor_list:
        if selected_nicotine:
            query.data = f"item:{cat_key}:{brand_key}:{item_idx}:-1:{selected_nicotine}"
        else:
            query.data = f"item:{cat_key}:{brand_key}:{item_idx}"
        await show_item_before_add(update, context)
        return

    nic_line = f"\n⚡ Міцність: <b>{escape_text(selected_nicotine)}</b>\n" if selected_nicotine else ""
    text = (
        f"<b>{escape_text(item.get('name', 'Товар'))}</b>\n"
        f"{nic_line}\n"
        f"Оберіть смак/колір:"
    )

    markup = build_flavors_keyboard(cat_key, brand_key, item_idx, flavor_list, selected_nicotine)
    image = resolve_display_image(category=category, brand=brand, item=item)

    await send_or_edit_content(update, context, text=text, reply_markup=markup, image=image)

async def show_item_before_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await safe_answer_callback(update)

    parts = query.data.split(":")
    if len(parts) not in (4, 5, 6):
        return

    # item:cat:brand:item
    # item:cat:brand:item:flavor
    # item:cat:brand:item:flavor:nicotine
    _, cat_key, brand_key, item_idx_raw, *rest = parts

    try:
        item_idx = int(item_idx_raw)
    except ValueError:
        await query.message.reply_text("❌ Некоректний індекс товару.")
        return

    flavor_idx: Optional[int] = None
    selected_nicotine: Optional[str] = None

    if len(rest) == 1:
        # або flavor_idx, або спеціальне -1
        try:
            maybe_flavor = int(rest[0])
            if maybe_flavor >= 0:
                flavor_idx = maybe_flavor
        except ValueError:
            selected_nicotine = rest[0]
    elif len(rest) == 2:
        try:
            maybe_flavor = int(rest[0])
            if maybe_flavor >= 0:
                flavor_idx = maybe_flavor
        except ValueError:
            pass
        selected_nicotine = rest[1]

    category = get_category(cat_key)
    brand = get_brand(cat_key, brand_key)
    item = get_parent_item(cat_key, brand_key, item_idx)

    if not category or not brand or not item:
        await query.message.reply_text("❌ Помилка: товар не знайдено.")
        return

    title = str(item.get("name", "Товар"))
    description = str(item.get("description", "Опис відсутній"))
    price = item.get("price", 0)

    selected_flavor_name = ""
    flavor_obj: Optional[Any] = None
    item_flavors = item.get("items", [])
    if flavor_idx is not None and isinstance(item_flavors, list) and 0 <= flavor_idx < len(item_flavors):
        flavor_obj = item_flavors[flavor_idx]
        selected_flavor_name = _extract_flavor_name(flavor_obj)

    if selected_flavor_name:
        title = f"{title} — {selected_flavor_name}"

    text_lines = [f"<b>{escape_text(title)}</b>", ""]
    if selected_nicotine:
        text_lines.append(f"⚡ Міцність: <b>{escape_text(selected_nicotine)}</b>")
    text_lines.append(f"📝 {escape_text(description)}")
    text_lines.append(f"💰 Ціна: <b>{escape_text(price)}€</b>")

    text = "\n".join(text_lines)

    markup = build_item_keyboard(
        cat_key=cat_key,
        brand_key=brand_key,
        item_idx=item_idx,
        flavor_idx=flavor_idx,
        selected_nicotine=selected_nicotine,
    )
    image = resolve_display_image(category=category, brand=brand, item=item, flavor_obj=flavor_obj)

    await send_or_edit_content(update, context, text=text, reply_markup=markup, image=image)

# ================== CART ==================
async def add_to_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return

    parts = query.data.split(":")
    if len(parts) < 4:
        await safe_answer_callback(update, "❌ Сталася помилка даних", show_alert=True)
        return

    cat_key = parts[1]
    brand_key = parts[2]

    try:
        item_idx = int(parts[3])
    except ValueError:
        await safe_answer_callback(update, "❌ Некоректний індекс товару", show_alert=True)
        return

    flavor_idx: Optional[int] = None
    selected_nicotine: Optional[str] = None

    if len(parts) >= 5:
        try:
            flavor_idx = int(parts[4])
        except ValueError:
            selected_nicotine = parts[4]

    if len(parts) >= 6:
        selected_nicotine = parts[5]

    try:
        base_item = get_parent_item(cat_key, brand_key, item_idx)
        if not base_item:
            raise ValueError("Товар не знайдено")

        cart_item: Dict[str, Any] = {
            "name": str(base_item.get("name", "Товар")),
            "price": float(base_item.get("price", 0)),
        }

        item_flavors = base_item.get("items", [])
        if flavor_idx is not None and isinstance(item_flavors, list) and 0 <= flavor_idx < len(item_flavors):
            flavor_obj = item_flavors[flavor_idx]
            flavor_name = _extract_flavor_name(flavor_obj)
            cart_item["name"] = f"{cart_item['name']} ({flavor_name})"

        if selected_nicotine:
            cart_item["name"] = f"{cart_item['name']} [{selected_nicotine}]"

        user_id = update.effective_user.id
        cart = get_user_cart(user_id)
        cart.append(cart_item)

        await safe_answer_callback(update, "🛒 Додано в кошик!")
        await cart_view_handler(update, context)

    except Exception as e:
        logger.exception("Помилка додавання в кошик: %s", e)
        await safe_answer_callback(update, "❌ Помилка при додаванні", show_alert=True)

async def cart_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    await safe_answer_callback(update)

    user_id = update.effective_user.id
    cart = get_user_cart(user_id)

    if not cart:
        text = "🛒 <b>Ваш кошик порожній</b>"
        keyboard = [[InlineKeyboardButton("⬅ Назад до каталогу", callback_data="catalog")]]
        markup = InlineKeyboardMarkup(keyboard)
        await send_or_edit_content(update, context, text=text, reply_markup=markup, image=None)
        return

    items_text = ""
    total = 0.0

    for i, item in enumerate(cart, start=1):
        name = escape_text(item.get("name", "Товар"))
        price = float(item.get("price", 0))
        items_text += f"{i}. {name} — {price:.2f}€\n"
        total += price

    text = f"🛒 <b>Ваш кошик:</b>\n\n{items_text}\n💰 Разом: <b>{total:.2f}€</b>"
    keyboard = [
        [InlineKeyboardButton("✅ Оформити замовлення", callback_data="checkout")],
        [InlineKeyboardButton("➖ Видалити останній товар", callback_data="remove_one")],
        [InlineKeyboardButton("🗑 Очистити кошик", callback_data="clear_cart")],
        [InlineKeyboardButton("⬅ Назад", callback_data="catalog")],
    ]
    markup = InlineKeyboardMarkup(keyboard)

    await send_or_edit_content(update, context, text=text, reply_markup=markup, image=None)

async def clear_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    user_carts[user_id] = []
    await safe_answer_callback(update, "🧹 Кошик очищено")
    await cart_view_handler(update, context)

async def remove_one_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    cart = get_user_cart(user_id)

    if cart:
        removed_item = cart.pop()
        await safe_answer_callback(update, f"❌ Видалено: {removed_item.get('name', 'Товар')}")
    else:
        await safe_answer_callback(update, "Кошик уже порожній")

    await cart_view_handler(update, context)

async def reserve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_answer_callback(update, "⏳ Функція резерву в розробці", show_alert=True)

# ================== CHECKOUT ==================
async def checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return

    await safe_answer_callback(update)

    user_id = query.from_user.id
    cart = get_user_cart(user_id)

    if not cart:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📦 Перейти до каталогу", callback_data="catalog")]]
        )
        await send_or_edit_content(
            update,
            context,
            text="🛒 <b>Ваш кошик порожній!</b>\n\nДодайте щось, щоб зробити замовлення.",
            reply_markup=keyboard,
            image=None,
        )
        return

    try:
        user_city = get_user_city(user_id)
        username = query.from_user.username or "приховано"
        order_id = datetime.now().strftime("%H%M%S")

        total_price = 0.0
        items_text = ""

        for item in cart:
            name = escape_text(item.get("name", "Товар"))
            price = float(item.get("price", 0))
            items_text += f"• <b>{name}</b> — {price:.2f}€\n"
            total_price += price

        order_to_group = (
            f"🛍️ <b>НОВЕ ЗАМОВЛЕННЯ №{order_id}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📍 <b>МІСТО: {escape_text(user_city).upper()}</b>\n"
            f"👤 Клієнт: {escape_text(query.from_user.full_name)} (@{escape_text(username)})\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📦 <b>Товари:</b>\n{items_text}\n"
            f"💰 <b>РАЗОМ: {total_price:.2f}€</b>\n"
        )

        await context.bot.send_message(
            chat_id=GENERAL_COURIER_CHAT_ID,
            text=order_to_group,
            parse_mode=ParseMode.HTML,
        )

        user_carts[user_id] = []

        try:
            await query.message.delete()
        except Exception as e:
            logger.debug("Не вдалося видалити повідомлення після checkout: %s", e)

        final_confirm = (
            f"✅ <b>Замовлення №{order_id} прийнято!</b>\n\n"
            f"Кур'єри в місті <b>{escape_text(user_city)}</b> вже отримали ваше повідомлення.\n"
            f"Очікуйте, з вами зв'яжуться найближчим часом.\n\n"
            f"<b>Ваш чек:</b>\n{items_text}\n"
            f"💰 Сума: {total_price:.2f}€"
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=final_confirm,
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        logger.exception("Помилка в checkout_handler: %s", e)
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Сталася помилка. Перевірте, чи бот є адміном у групі кур'єрів і чи GENERAL_COURIER_CHAT_ID правильний.",
        )

# ================== CITY / TEXT ==================
async def set_city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return

    await safe_answer_callback(update)

    user_id = update.effective_user.id
    parts = query.data.split(":", 1)
    if len(parts) != 2:
        return

    city_name = parts[1]

    if city_name == "other":
        try:
            await query.message.delete()
        except Exception as e:
            logger.debug("Не вдалося видалити повідомлення перед ручним вводом міста: %s", e)

        await context.bot.send_message(
            chat_id=user_id,
            text="✍️ <b>Будь ласка, напишіть назву вашого міста прямо сюди в чат:</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    user_cities[user_id] = city_name
    await catalog_menu(update, context)

async def change_city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return
    user_cities[update.effective_user.id] = "Не вказано"
    await catalog_menu(update, context)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return

    # якщо місто ще не встановлено — вважаємо, що користувач вводить місто
    if get_user_city(user_id) == "Не вказано":
        user_cities[user_id] = text

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📦 Перейти до каталогу", callback_data="catalog")]]
        )

        await update.message.reply_text(
            f"✅ Місто <b>{escape_text(text)}</b> встановлено!\nТепер ви можете відкрити каталог.",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("🦊 Використовуйте кнопки меню для навігації.")

# ================== ADMIN STUBS ==================
async def admin_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_answer_callback(update)
    if update.callback_query:
        await update.callback_query.message.reply_text("🛠 Функція керування категоріями в розробці.")

async def admin_brand(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_answer_callback(update)

async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_answer_callback(update)

async def admin_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_answer_callback(update)

# ================== GLOBAL ERROR HANDLER ==================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception while processing update:", exc_info=context.error)

# ================== MAIN ==================
def main() -> None:
    app: Application = (
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

    # Item flow
    app.add_handler(CallbackQueryHandler(nicotine_handler, pattern=r"^nic:"))
    app.add_handler(CallbackQueryHandler(flavors_handler, pattern=r"^flavors:"))
    app.add_handler(CallbackQueryHandler(show_item_before_add, pattern=r"^item:"))

    # Cart / order
    app.add_handler(CallbackQueryHandler(add_to_cart_handler, pattern=r"^add_confirm:"))
    app.add_handler(CallbackQueryHandler(cart_view_handler, pattern=r"^cart$"))
    app.add_handler(CallbackQueryHandler(checkout_handler, pattern=r"^checkout$"))
    app.add_handler(CallbackQueryHandler(clear_cart_handler, pattern=r"^clear_cart$"))
    app.add_handler(CallbackQueryHandler(remove_one_handler, pattern=r"^remove_one$"))
    app.add_handler(CallbackQueryHandler(reserve_handler, pattern=r"^reserve:"))

    # Admin
    app.add_handler(CallbackQueryHandler(admin_cat, pattern=r"^admin_cat:"))
    app.add_handler(CallbackQueryHandler(admin_brand, pattern=r"^admin_brand:"))
    app.add_handler(CallbackQueryHandler(admin_block, pattern=r"^admin_block:"))
    app.add_handler(CallbackQueryHandler(admin_toggle, pattern=r"^admin_toggle:"))

    # Text input must stay last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)

    logger.info("🚀 ELF FOX BOT успішно запущений")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
