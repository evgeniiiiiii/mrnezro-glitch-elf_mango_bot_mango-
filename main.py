import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

import os

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = list(map(int, os.environ.get("ADMIN_ID", "").split(",")))
ADMIN_ID1 = list(map(int, os.environ.get("ADMIN_ID1", "").split(",")))
GENERAL_COURIER_CHAT_ID = int(os.environ.get("GENERAL_COURIER_CHAT_ID"))
user_carts = {}
user_cities = {}

def load_catalog():
    try:
        with open("catalog.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Catalog error: {e}")
        return {"categories": {}}

CATALOG = load_catalog()

# ================== ФУНКЦІЇ ОБРОБКИ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_carts: user_carts[user_id] = []
    
    keyboard = [[InlineKeyboardButton("📦 Каталог", callback_data="catalog")]]
    await update.message.reply_text(
        "👋 Вітаємо в ELF FOX!\nНатисніть кнопку нижче, щоб почати.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def catalog_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    try:
        await query.message.delete()
    except:
        pass

    # 1. ПЕРЕВІРКА МІСТА: якщо міста немає в базі, просимо обрати
    if user_id not in user_cities or user_cities[user_id] == "Не вказано":
        keyboard = [
            [InlineKeyboardButton("📍 Берлін", callback_data="set_city:Берлін")],
            [InlineKeyboardButton("📍 Дрезден", callback_data="set_city:Дрезден")],
            [InlineKeyboardButton("📍 Лейпциг", callback_data="set_city:Лейпциг")]
        ]
        await context.bot.send_message(
            chat_id=user_id,
            text="🏘 <b>Будь ласка, оберіть ваше місто для замовлення:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # 2. Якщо місто вже вибрано — показуємо звичайний каталог
    keyboard = []
    for cat_key, cat_data in CATALOG["categories"].items():
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
    await query.answer()
    
    try:
        await query.message.delete()
    except:
        pass

    _, cat_key = query.data.split(":", 1)
    category = CATALOG["categories"].get(cat_key)

    if not category:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Категорію не знайдено.")
        return

    keyboard = []
    for brand_key, brand_data in category["brands"].items():
        keyboard.append([InlineKeyboardButton(brand_data["name"], callback_data=f"brand:{cat_key}:{brand_key}")])
    
    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data="catalog")])

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📂 Категорія: <b>{category['name']}</b>\nОберіть бренд:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )



async def brand_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    parts = q.data.split(":")
    cat_key, brand_key = parts[1], parts[2]
    
    cat = CATALOG.get("categories", {}).get(cat_key)
    brand = cat.get("brands", {}).get(brand_key) if cat else None
    
    if not brand:
        await q.message.reply_text("❌ Бренд не знайдено.")
        return

    keyboard = []
    for idx, parent_item in enumerate(brand.get("items", [])):
        cb_data = f"flavors:{cat_key}:{brand_key}:{idx}"
        keyboard.append([InlineKeyboardButton(parent_item['name'], callback_data=cb_data)])

    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"cat:{cat_key}")])
    
    try:
        await q.message.edit_text(
            text=f"<b>{brand['name']}</b>\n\nОберіть позицію:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Brand error: {e}")



async def nicotine_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    try:
        await q.message.delete()
    except:
        pass

    # Дані: nic:cat_key:brand_key:p_idx
    _, cat_key, b_key, p_idx = q.data.split(":", 3)
    p_idx = int(p_idx)
    
    brand = CATALOG["categories"][cat_key]["brands"][b_key]
    parent = brand["items"][p_idx]
    
    # Якщо у товара є список nicotine_levels, виводимо кнопки
    # Якщо ні - відразу переходимо до смаків (flavors)
    nic_levels = parent.get("nicotine_levels", [])
    
    if not nic_levels:
        # Якщо нікотину немає в базі, пропускаємо цей крок і шлемо до смаків
        new_data = f"flavors:{cat_key}:{b_key}:{p_idx}"
        # Створюємо фейковий об'єкт query для переходу
        q.data = new_data
        await flavors_handler(update, context)
        return

    keyboard = []
    for nic in nic_levels:
        keyboard.append([InlineKeyboardButton(f"⚡ {nic}", callback_data=f"flavors:{cat_key}:{b_key}:{p_idx}")])
    
    keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"brand:{cat_key}:{b_key}")])

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f" Выберіть міцність для <b>{parent['name']}</b>:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

# ================== NEW: FLAVORS MENU ==================
def _extract_flavor_name(fl):
    if isinstance(fl, dict):
        return fl.get("name", "Невідомий смак")
    return str(fl)



async def flavors_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer() # Це прибирає завантаження на кнопці відразу

    try:
        _, cat_key, brand_key, parent_idx = q.data.split(":", 3)
        parent_idx_i = int(parent_idx)

        brand = CATALOG["categories"][cat_key]["brands"][brand_key]
        parent = brand["items"][parent_idx_i]
        flavors = parent.get("items", [])

        if not flavors:
            await q.message.reply_text("❌ Смаків не знайдено.")
            return

        keyboard = []
        for fidx, fl in enumerate(flavors):
            fl_name = _extract_flavor_name(fl)
            cb = f"show_flv:{cat_key}:{brand_key}:{parent_idx_i}:{fidx}"
            keyboard.append([InlineKeyboardButton(f"{fl_name} ✅", callback_data=cb)])

        keyboard.append([InlineKeyboardButton("⬅ Назад", callback_data=f"brand:{cat_key}:{brand_key}")])
        
        await q.message.edit_text(
            text=f"📌 <b>{parent['name']}</b>\nОберіть смак:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Error in flavors: {e}")
        await q.message.reply_text("❌ Помилка завантаження смаків.")

async def show_item_before_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    # 1. ВІДРАЗУ ВІДПОВІДАЄМО, ЩОБ ПРИБРАТИ ГОДИННИК
    await q.answer()
    
    print(f"DEBUG: Натиснуто кнопку з даними: {q.data}") # Дивись це в терміналі!

    try:
        parts = q.data.split(":")
        if len(parts) < 5:
            print("DEBUG: Помилка формату даних")
            return

        _, cat_key, b_key, p_idx, f_idx = parts
        p_idx, f_idx = int(p_idx), int(f_idx)

        category = CATALOG.get("categories", {}).get(cat_key)
        brand = category.get("brands", {}).get(b_key)
        parent = brand["items"][p_idx]
        flavor_list = parent.get("items", [])
        
        flavor_raw = flavor_list[f_idx]
        f_name = flavor_raw["name"] if isinstance(flavor_raw, dict) else str(flavor_raw)
        
        item_to_confirm = {
            "name": f"{parent['name']} ({f_name})",
            "price": parent.get("price", 0),
            "description": parent.get("description", "Оберіть кількість"),
            "image": parent.get("image")
        }

        await send_item_confirmation(update, context, item_to_confirm, f"flavors:{cat_key}:{b_key}:{p_idx}")
        
    except Exception as e:
        print(f"DEBUG ERROR: {e}")
        await q.message.reply_text("❌ Помилка завантаження товару")

async def send_item_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, item: dict, back_data: str):
    q = update.callback_query
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

    try:
        await q.message.delete() # Видаляємо старе меню
    except:
        pass

    # Відправка
    if item.get("image"):
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=item["image"],
            caption=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )


async def add_to_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("🛒 Додано в кошик!")
    
    user_id = update.effective_user.id
    item = context.user_data.get("last_selected_item")
    
    if item:
        if user_id not in user_carts:
            user_carts[user_id] = []
        user_carts[user_id].append(item)
    
    await catalog_menu(update, context)

async def cart_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
        try:
            await q.message.delete()
        except:
            pass

    user_id = update.effective_user.id
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

    # Формуємо список товарів у кошику
    text = "🛒 <b>Ваш кошик:</b>\n\n"
    total_price = 0
    for idx, item in enumerate(cart):
        text += f"{idx + 1}. {item['name']} — <b>{item['price']}€</b>\n"
        total_price += item['price']

    text += f"\n💰 Загалом до сплати: <b>{total_price}€</b>"

    keyboard = [
        [InlineKeyboardButton("✅ Оформити замовлення", callback_data="checkout")],
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
    user_carts[user_id] = []
    await update.callback_query.answer("🧹 Кошик очищено")
    # Оновлюємо вигляд кошика (він буде порожнім)
    await cart_view_handler(update, context)

async def remove_one_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Видаляє останній доданий товар з кошика"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if user_id in user_carts and user_carts[user_id]:
        removed_item = user_carts[user_id].pop()
        await query.answer(f"❌ Видалено: {removed_item['name']}")
    else:
        await query.answer("Кошик уже порожній")
        
    # Оновлюємо вигляд кошика після видалення
    await cart_view_handler(update, context)

async def reserve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Заглушка для кнопки резерву"""
    await update.callback_query.answer("⏳ Функція резерву в розробці", show_alert=True)


async def checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global user_carts, user_cities
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    # 1. Отримуємо кошик
    cart = user_carts.get(user_id, [])
    
    # Якщо кошик порожній — даємо кнопку повернення
    if not cart:
        keyboard = [[InlineKeyboardButton("📦 Перейти до каталогу", callback_data="catalog")]]
        await query.message.edit_text(
            text="🛒 <b>Ваша корзина порожня!</b>\n\nДодайте щось, щоб зробити замовлення.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    try:
        # 2. Збираємо дані для замовлення
        user_city = user_cities.get(user_id, "Не вказано")
        username = query.from_user.username or "приховано"
        order_id = datetime.now().strftime("%H%M%S") # Номер замовлення за часом

        total_price = 0
        items_text = ""
        for item in cart:
            items_text += f"• <b>{item['name']}</b> — {item['price']}€\n"
            total_price += item['price']

        # 3. Формуємо текст для ГРУПИ КУР'ЄРІВ
        order_to_group = (
            f"🛍️ <b>НОВЕ ЗАМОВЛЕННЯ №{order_id}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📍 <b>МІСТО: {user_city.upper()}</b>\n"
            f"👤 Клієнт: {query.from_user.full_name} (@{username})\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📦 <b>Товари:</b>\n{items_text}\n"
            f"💰 <b>РАЗОМ: {total_price}€</b>\n"
        )

        await context.bot.send_message(
            chat_id=GENERAL_COURIER_CHAT_ID,
            text=order_to_group,
            parse_mode="HTML"
        )

        # 4. ОЧИЩЕННЯ ЧАТУ ТА КОРЗИНИ
        user_carts[user_id] = [] # Очищуємо кошик у пам'яті
        
        # Видаляємо повідомлення з кнопками "Оформити/Очистити", щоб чат був чистим
        try:
            await query.message.delete()
        except Exception:
            pass

        # 5. Надсилаємо фінальне підтвердження (ЧЕК)
        # Це єдине повідомлення, що залишиться в чаті після покупки
        final_confirm = (
            f"✅ <b>Замовлення №{order_id} прийнято!</b>\n\n"
            f"Кур'єри в місті <b>{user_city}</b> вже отримали ваше повідомлення.\n"
            f"Очікуйте, з вами зв'яжуться найближчим часом! 🦊\n\n"
            f"<b>Ваш чек:</b>\n{items_text}\n"
            f"💰 Сума: {total_price}€"
        )
        
        await context.bot.send_message(
            chat_id=user_id,
            text=final_confirm,
            parse_mode="HTML"
        )

    except Exception as e:
        logging.error(f"Помилка в checkout_handler: {e}")
        await query.message.reply_text("❌ Сталася помилка. Перевірте, чи бот є адміном у групі кур'єрів.")

async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Видаляємо поточне повідомлення, щоб чат був чистим
    try:
        await query.message.delete()
    except:
        pass

    data = query.data.split(":")
    # Якщо просто "back", то йдемо в каталог, якщо "back:main" — на старт
    target = data[1] if len(data) > 1 else "catalog"

    if target == "main":
        await start(update, context)
    elif target == "catalog":
        await catalog_menu(update, context)
    elif target == "cat":
        # Повернення до вибору бренду всередині категорії
        # Очікуємо формат back:cat:category_key
        if len(data) > 2:
            query.data = f"cat:{data[2]}"
            await category_handler(update, context)
        else:
            await catalog_menu(update, context)
    elif target == "brand":
        # Повернення до списку товарів бренду
        # Очікуємо формат back:brand:cat_key:brand_key
        if len(data) > 3:
            query.data = f"brand:{data[2]}:{data[3]}"
            await brand_handler(update, context)
        else:
            await catalog_menu(update, context)


async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Перевірка, чи є користувач у списку адмінів
    if user_id not in ADMIN_IDS:
        # Можна просто ігнорувати або видати помилку
        return 

    # Видаляємо команду /admin з чату для чистоти
    try:
        await update.message.delete()
    except:
        pass

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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Видаляємо повідомлення користувача для чистоти чату
    try:
        await update.message.delete()
    except:
        pass

    # Якщо місто ще не вказано
    if user_id not in user_cities or user_cities[user_id] == "Не вказано":
        user_cities[user_id] = text
        keyboard = [[InlineKeyboardButton("📦 Перейти до каталогу", callback_data="catalog")]]
        await update.message.reply_text(
            f"✅ Місто <b>{text}</b> встановлено!\nТепер ви можете відкрити каталог.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        # Якщо місто вже є, шлемо підказку, яка зникне через 2 секунди
        temp_msg = await update.message.reply_text("🦊 Використовуйте кнопки меню для навігації")
        await asyncio.sleep(2)
        try:
            await temp_msg.delete()
        except:
            pass

async def set_city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    # Отримуємо назву міста з callback_data (наприклад, "set_city:Київ")
    city_name = query.data.split(":")[1]
    
    user_cities[user_id] = city_name
    
    # Після вибору міста знову викликаємо каталог, але тепер він пропустить перевірку
    await catalog_menu(update, context)

async def change_city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_cities[user_id] = "Не вказано"
    await catalog_menu(update, context)


# ================== ГОЛОВНИЙ ЗАПУСК ==================
def main():
    # 1. Ініціалізація додатка з токеном та тайм-аутами для стабільності
    app = ApplicationBuilder().token(BOT_TOKEN).connect_timeout(30).read_timeout(30).build()

    # 2. КОМАНДИ (Commands)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_start))

    # 3. КНОПКИ НАВІГАЦІЇ (Callback Queries)
    # Головне меню та категорії
    app.add_handler(CallbackQueryHandler(catalog_menu, pattern="^catalog$"))
    app.add_handler(CallbackQueryHandler(category_handler, pattern="^cat:"))
    app.add_handler(CallbackQueryHandler(brand_handler, pattern="^brand:"))
    app.add_handler(CallbackQueryHandler(set_city_handler, pattern="^set_city:"))
    app.add_handler(CallbackQueryHandler(change_city_handler, pattern="^change_city$"))

    
    # Вибір нікотину та смаків
    app.add_handler(CallbackQueryHandler(nicotine_handler, pattern="^nic:"))
    app.add_handler(CallbackQueryHandler(flavors_handler, pattern="^flavors:"))
    
    # Показ картки товару та кнопка "Назад"
    app.add_handler(CallbackQueryHandler(show_item_before_add, pattern="show"))
    app.add_handler(CallbackQueryHandler(back_handler, pattern="^back:"))

    # 4. КОШИК ТА ЗАМОВЛЕННЯ
    app.add_handler(CallbackQueryHandler(add_to_cart_handler, pattern="^add_confirm:"))
    app.add_handler(CallbackQueryHandler(cart_view_handler, pattern="^cart$"))
    app.add_handler(CallbackQueryHandler(checkout_handler, pattern="^checkout$"))
    app.add_handler(CallbackQueryHandler(clear_cart_handler, pattern="^clear_cart$"))
    app.add_handler(CallbackQueryHandler(remove_one_handler, pattern="^remove_one$"))
    app.add_handler(CallbackQueryHandler(reserve_handler, pattern="^reserve:"))

    # 5. АДМІН-ПАНЕЛЬ
    app.add_handler(CallbackQueryHandler(admin_cat, pattern="^admin_cat:"))
    app.add_handler(CallbackQueryHandler(admin_brand, pattern="^admin_brand:"))
    app.add_handler(CallbackQueryHandler(admin_block, pattern="^admin_block:"))
    app.add_handler(CallbackQueryHandler(admin_toggle, pattern="^admin_toggle:"))
    app.add_handler(CallbackQueryHandler(admin_back, pattern="^admin_back$"))

    # 6. ОБРОБКА ТЕКСТУ (MessageHandler)
    # Важливо: цей хендлер МАЄ бути останнім у списку.
    # Він обробляє введення міста та інший текст, не заважаючи кнопкам.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Запуск бота
    print("🚀 ELF FOX BOT успішно запущений!")
    app.run_polling()





if __name__ == "__main__":
    main()
