import os
import json
import logging
import asyncio
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
GENERAL_COURIER_CHAT_ID_RAW = os.environ.get("GENERAL_COURIER_CHAT_ID", "").strip()

if not BOT_TOKEN:
    raise ValueError("Не задано BOT_TOKEN у змінних середовища.")

if not GENERAL_COURIER_CHAT_ID_RAW:
    raise ValueError("Не задано GENERAL_COURIER_CHAT_ID у змінних середовища.")

try:
    GENERAL_COURIER_CHAT_ID = int(GENERAL_COURIER_CHAT_ID_RAW)
except ValueError:
    raise ValueError("GENERAL_COURIER_CHAT_ID має бути числом, наприклад -1001234567890")

# Тимчасове сховище в пам'яті
# Після перезапуску бота все очищається
user_carts = {}
user_cities = {}

# ================== LOAD CATALOG ==================
def load_catalog():
    try:
        with open("catalog.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Catalog error: {e}")
        return {"categories": {}}

CATALOG = load_catalog()

# ================== HELPERS ==================
def get_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Каталог", callback_data="catalog")]
    ])

def get_city_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 Берлін", callback_data="set_city:Берлін")],
        [InlineKeyboardButton("📍 Дрезден", callback_data="set_city:Дрезден")],
        [InlineKeyboardButton("📍 Лейпциг", callback_data="set_city:Лейпциг")]
    ])

def ensure_user_storage(user_id: int):
    if user_id not in user_carts:
        user_carts[user_id] = []
    if user_id not in user_cities:
        user_cities[user_id] = "Не вказано"

def _extract_flavor_name(fl):
    if isinstance(fl, dict):
        return fl.get("name", "Невідомий смак")
    return str(fl)

async def safe_delete_message(message):
    try:
        await message.delete()
    except Exception:
        pass

async def notify_admins_and_group(context: ContextTypes.DEFAULT_TYPE, text: str):
    # 1. Надсилання в групу
    try:
        await context.bot.send_message(
            chat_id=GENERAL_COURIER_CHAT_ID,
            text=text,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Помилка надсилання в групу {GENERAL_COURIER_CHAT_ID}: {e}")

    # 2. Надсилання всім адмінам
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Помилка надсилання адміну {admin_id}: {e}")

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user_storage(user_id)

    if update.message:
        await update.message.reply_text(
            "👋 Вітаємо в ELF FOX!\nНатисніть кнопку нижче, щоб почати.",
            reply_markup=get_main_menu_keyboard()
        )

# ================== CATALOG ==================
async def catalog_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    user_id = update.effective_user.id
    ensure_user_storage(user_id)

    await safe_delete_message(query.message)

    # Якщо місто не вибране — просимо обрати
    if user_cities[user_id] == "Не вказано":
        await context.bot.send_message(
            chat_id=user_id,
            text="🏘 <b>Будь ласка, оберіть ваше місто для замовлення:</b>",
            reply_markup=get_city_keyboard(),
            parse_mode="HTML"
        )
        return

    categories = CATALOG.get("categories", {})
    keyboard = []

    for cat_key, cat_data in categories.items():
        name = cat_data.get("name", cat_key)
        keyboard.append([InlineKeyboardButton(name, callback_data=f"cat:{cat_key}")])

    keyboard.append([InlineKeyboardButton("🛒 Кошик", callback_data="cart")])
    keyboard.append([InlineKeyboardButton("🏙 Змінити місто", callback_data="change_city")])

    await context.bot.send_message(
        chat_id=user_id,
        text=f"📍 Ваше місто: <b>{user_cities[user_id]}</b>\n\n📦 <b>Оберіть категорію:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    await safe_delete_message(query.message)

    try:
        _, cat_key = query.data.split(":", 1)
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Невірний формат категорії.")
        return

    category = CATALOG.get("categories", {}).get(cat_key)

    if not category:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Категорію не знайдено.")
        return

    keyboard = []
    for brand_key, brand_data in category.get("brands", {}).items():
        keyboard.append([
            InlineKeyboardButton(
                brand_data.get("name", brand_key),
                callback_data=f"brand:{cat_key}:{brand_key}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data="catalog")])

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📂 Категорія: <b>{category.get('name', cat_key)}</b>\nОберіть бренд:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def brand_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    await q.answer()

    try:
        parts = q.data.split(":")
        cat_key, brand_key = parts[1], parts[2]
    except Exception:
        await q.message.reply_text("❌ Помилка читання бренду.")
        return

    cat = CATALOG.get("categories", {}).get(cat_key)
    brand = cat.get("brands", {}).get(brand_key) if cat else None

    if not brand:
        await q.message.reply_text("❌ Бренд не знайдено.")
        return

    keyboard = []

    for idx, parent_item in enumerate(brand.get("items", [])):
        has_nicotine = bool(parent_item.get("nicotine_levels"))
        cb_data = f"nic:{cat_key}:{brand_key}:{idx}" if has_nicotine else f"flavors:{cat_key}:{brand_key}:{idx}"
        keyboard.append([InlineKeyboardButton(parent_item.get("name", f"Товар {idx+1}"), callback_data=cb_data)])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"cat:{cat_key}")])

    try:
        await q.message.edit_text(
            text=f"<b>{brand.get('name', brand_key)}</b>\n\nОберіть позицію:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Brand error: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<b>{brand.get('name', brand_key)}</b>\n\nОберіть позицію:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

# ================== NICOTINE ==================
async def nicotine_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    await q.answer()
    await safe_delete_message(q.message)

    try:
        _, cat_key, b_key, p_idx = q.data.split(":", 3)
        p_idx = int(p_idx)

        brand = CATALOG["categories"][cat_key]["brands"][b_key]
        parent = brand["items"][p_idx]
    except Exception as e:
        logger.error(f"Помилка nicotine_handler: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Помилка завантаження міцності."
        )
        return

    nic_levels = parent.get("nicotine_levels", [])

    if not nic_levels:
        q.data = f"flavors:{cat_key}:{b_key}:{p_idx}"
        await flavors_handler(update, context)
        return

    keyboard = []
    for nic in nic_levels:
        # Тут можна передавати обрану міцність окремо, але в твоїй логіці вона поки що не зберігається.
        # Тому просто ведемо на смак.
        keyboard.append([InlineKeyboardButton(f"⚡ {nic}", callback_data=f"flavors:{cat_key}:{b_key}:{p_idx}")])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"brand:{cat_key}:{b_key}")])

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"⚡ <b>Оберіть міцність для {parent.get('name', 'товару')}</b>:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

# ================== FLAVORS ==================
async def flavors_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    await q.answer()

    try:
        _, cat_key, brand_key, parent_idx = q.data.split(":", 3)
        parent_idx_i = int(parent_idx)

        brand = CATALOG["categories"][cat_key]["brands"][brand_key]
        parent = brand["items"][parent_idx_i]
        flavors = parent.get("items", [])
    except Exception as e:
        logger.error(f"Error in flavors_handler setup: {e}")
        await q.message.reply_text("❌ Помилка завантаження смаків.")
        return

    if not flavors:
        await q.message.reply_text("❌ Смаків не знайдено.")
        return

    keyboard = []
    for fidx, fl in enumerate(flavors):
        fl_name = _extract_flavor_name(fl)
        cb = f"show_flv:{cat_key}:{brand_key}:{parent_idx_i}:{fidx}"
        keyboard.append([InlineKeyboardButton(f"{fl_name} ✅", callback_data=cb)])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"brand:{cat_key}:{brand_key}")])

    try:
        await q.message.edit_text(
            text=f"📌 <b>{parent.get('name', 'Товар')}</b>\nОберіть смак:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in flavors edit_text: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"📌 <b>{parent.get('name', 'Товар')}</b>\nОберіть смак:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

async def show_item_before_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    await q.answer()

    try:
        parts = q.data.split(":")
        if len(parts) < 5:
            await q.message.reply_text("❌ Невірний формат даних товару.")
            return

        _, cat_key, b_key, p_idx, f_idx = parts
        p_idx, f_idx = int(p_idx), int(f_idx)

        category = CATALOG.get("categories", {}).get(cat_key)
        brand = category.get("brands", {}).get(b_key) if category else None
        parent = brand["items"][p_idx] if brand else None

        if not parent:
            await q.message.reply_text("❌ Товар не знайдено.")
            return

        flavor_list = parent.get("items", [])
        flavor_raw = flavor_list[f_idx]
        f_name = flavor_raw["name"] if isinstance(flavor_raw, dict) else str(flavor_raw)

        item_to_confirm = {
            "name": f"{parent.get('name', 'Товар')} ({f_name})",
            "price": float(parent.get("price", 0)),
            "description": parent.get("description", "Оберіть кількість"),
            "image": parent.get("image")
        }

        await send_item_confirmation(
            update,
            context,
            item_to_confirm,
            f"flavors:{cat_key}:{b_key}:{p_idx}"
        )

    except Exception as e:
        logger.error(f"DEBUG ERROR show_item_before_add: {e}")
        await q.message.reply_text("❌ Помилка завантаження товару.")

async def send_item_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, item: dict, back_data: str):
    q = update.callback_query
    if not q:
        return

    context.user_data["last_selected_item"] = item

    text = (
        f"<b>{item['name']}</b>\n\n"
        f"💰 Ціна: <b>{item['price']}€</b>\n"
        f"📝 {item['description']}\n\n"
        f"Додати в кошик?"
    )

    keyboard = [
        [InlineKeyboardButton("🛒 Додати в кошик", callback_data=f"add_confirm:{back_data}")],
        [InlineKeyboardButton("⬅ Назад", callback_data=back_data)]
    ]

    await safe_delete_message(q.message)

    if item.get("image"):
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=item["image"],
                caption=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return
        except Exception as e:
            logger.error(f"Помилка відправки фото: {e}")

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

# ================== CART ==================
async def add_to_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    await q.answer("🛒 Додано в кошик!")

    user_id = update.effective_user.id
    ensure_user_storage(user_id)

    item = context.user_data.get("last_selected_item")
    if item:
        user_carts[user_id].append(item)

    await catalog_menu(update, context)

async def cart_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
        await safe_delete_message(q.message)

    user_id = update.effective_user.id
    ensure_user_storage(user_id)
    cart = user_carts.get(user_id, [])

    if not cart:
        keyboard = [[InlineKeyboardButton("📦 До каталогу", callback_data="catalog")]]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🛒 <b>Ваш кошик порожній.</b>\nЧас щось обрати! 🦊",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    text = "🛒 <b>Ваш кошик:</b>\n\n"
    total_price = 0

    for idx, item in enumerate(cart):
        price = float(item.get("price", 0))
        text += f"{idx + 1}. {item.get('name', 'Товар')} — <b>{price}€</b>\n"
        total_price += price

    text += f"\n💰 Загалом до сплати: <b>{total_price}€</b>"

    keyboard = [
        [InlineKeyboardButton("✅ Оформити замовлення", callback_data="checkout")],
        [InlineKeyboardButton("➖ Видалити останній товар", callback_data="remove_one")],
        [InlineKeyboardButton("🧹 Очистити кошик", callback_data="clear_cart")],
        [InlineKeyboardButton("📦 До каталогу", callback_data="catalog")]
    ]

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def clear_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user_storage(user_id)
    user_carts[user_id] = []

    await update.callback_query.answer("🧹 Кошик очищено")
    await cart_view_handler(update, context)

async def remove_one_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    user_id = update.effective_user.id
    ensure_user_storage(user_id)

    if user_carts[user_id]:
        removed_item = user_carts[user_id].pop()
        await query.answer(f"❌ Видалено: {removed_item['name']}")
    else:
        await query.answer("Кошик уже порожній")

    await cart_view_handler(update, context)

async def reserve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("⏳ Функція резерву в розробці", show_alert=True)

# ================== CHECKOUT ==================
async def checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    user_id = query.from_user.id
    ensure_user_storage(user_id)

    cart = user_carts.get(user_id, [])

    if not cart:
        keyboard = [[InlineKeyboardButton("📦 Перейти до каталогу", callback_data="catalog")]]
        try:
            await query.message.edit_text(
                text="🛒 <b>Ваш кошик порожній!</b>\n\nДодайте щось, щоб зробити замовлення.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        except Exception:
            await context.bot.send_message(
                chat_id=user_id,
                text="🛒 <b>Ваш кошик порожній!</b>\n\nДодайте щось, щоб зробити замовлення.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        return

    try:
        user_city = user_cities.get(user_id, "Не вказано")
        username = query.from_user.username or "приховано"
        order_id = datetime.now().strftime("%H%M%S")

        total_price = 0
        items_text = ""

        for item in cart:
            price = float(item.get("price", 0))
            items_text += f"• <b>{item.get('name', 'Товар')}</b> — {price}€\n"
            total_price += price

        order_text = (
            f"🛍️ <b>НОВЕ ЗАМОВЛЕННЯ №{order_id}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📍 <b>МІСТО: {user_city.upper()}</b>\n"
            f"👤 Клієнт: {query.from_user.full_name} (@{username})\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📦 <b>Товари:</b>\n{items_text}\n"
            f"💰 <b>РАЗОМ: {total_price}€</b>\n"
        )

        # Надсилаємо замовлення в групу та адмінам
        await notify_admins_and_group(context, order_text)

        # Очищуємо кошик
        user_carts[user_id] = []

        # Видаляємо старе повідомлення кошика
        await safe_delete_message(query.message)

        final_confirm = (
            f"✅ <b>Замовлення №{order_id} прийнято!</b>\n\n"
            f"Кур'єри в місті <b>{user_city}</b> вже отримали ваше повідомлення.\n"
            f"Очікуйте, з вами зв'яжуться найближчим часом. 🦊\n\n"
            f"<b>Ваш чек:</b>\n{items_text}\n"
            f"💰 Сума: {total_price}€"
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=final_confirm,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Помилка в checkout_handler: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Сталася помилка під час оформлення замовлення. Перевірте, чи бот є в групі та чи правильні ID."
        )

# ================== BACK ==================
async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    await safe_delete_message(query.message)

    data = query.data.split(":")
    target = data[1] if len(data) > 1 else "catalog"

    if target == "main":
        user_id = update.effective_user.id
        ensure_user_storage(user_id)
        await context.bot.send_message(
            chat_id=user_id,
            text="👋 Вітаємо в ELF FOX!\nНатисніть кнопку нижче, щоб почати.",
            reply_markup=get_main_menu_keyboard()
        )
    elif target == "catalog":
        await catalog_menu(update, context)
    elif target == "cat":
        if len(data) > 2:
            query.data = f"cat:{data[2]}"
            await category_handler(update, context)
        else:
            await catalog_menu(update, context)
    elif target == "brand":
        if len(data) > 3:
            query.data = f"brand:{data[2]}:{data[3]}"
            await brand_handler(update, context)
        else:
            await catalog_menu(update, context)

# ================== ADMIN ==================
async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        if update.message:
            await update.message.reply_text("❌ У вас немає доступу до адмін-панелі.")
        return

    if update.message:
        await safe_delete_message(update.message)

    keyboard = [
        [InlineKeyboardButton("📁 Керування категоріями", callback_data="admin_cat:list")],
        [InlineKeyboardButton("🏠 Головне меню", callback_data="back:main")]
    ]

    await context.bot.send_message(
        chat_id=user_id,
        text="⚡ <b>Адмін-панель ELF FOX</b>\nОберіть розділ для редагування:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def admin_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    await query.message.reply_text("🛠 Функція керування категоріями в розробці.")

async def admin_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

async def admin_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_start(update, context)

# ================== TEXT HANDLER ==================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user_storage(user_id)

    text = (update.message.text or "").strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    if user_cities[user_id] == "Не вказано":
        user_cities[user_id] = text
        keyboard = [[InlineKeyboardButton("📦 Перейти до каталогу", callback_data="catalog")]]

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ Місто <b>{text}</b> встановлено!\nТепер ви можете відкрити каталог.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        temp_msg = await context.bot.send_message(
            chat_id=user_id,
            text="🦊 Використовуйте кнопки меню для навігації"
        )
        await asyncio.sleep(2)
        await safe_delete_message(temp_msg)

# ================== CITY ==================
async def set_city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user_id = update.effective_user.id
    ensure_user_storage(user_id)

    try:
        city_name = query.data.split(":", 1)[1]
    except Exception:
        await query.message.reply_text("❌ Помилка вибору міста.")
        return

    user_cities[user_id] = city_name
    await catalog_menu(update, context)

async def change_city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    ensure_user_storage(user_id)

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
    app.add_handler(CallbackQueryHandler(back_handler, pattern=r"^back:"))

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
    app.add_handler(CallbackQueryHandler(admin_back, pattern=r"^admin_back$"))

    # Text handler must be last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 ELF FOX BOT успішно запущений!")
    app.run_polling()

if __name__ == "__main__":
    main()
