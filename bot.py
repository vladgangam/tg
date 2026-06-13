import asyncio
import logging
import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


DB_NAME = "calcua_bot.db"


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def init_db() -> None:
    """Створює таблицю користувачів, якщо її ще немає."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )


def save_user(update: Update) -> None:
    """Зберігає або оновлює дані користувача Telegram."""
    user = update.effective_user
    if not user:
        return

    now = datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            """
            INSERT INTO users (
                telegram_id, username, first_name, last_name, created_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                last_seen_at = excluded.last_seen_at
            """,
            (
                user.id,
                user.username,
                user.first_name,
                user.last_name,
                now,
                now,
            ),
        )


def parse_numbers(args: list[str], count: int) -> list[float] | None:
    """Перетворює аргументи команди на числа."""
    if len(args) != count:
        return None

    try:
        return [float(arg.replace(",", ".")) for arg in args]
    except ValueError:
        return None


def money(value: float) -> str:
    """Форматує грошові значення у гривнях."""
    return f"{value:,.2f} грн".replace(",", " ")


def main_menu() -> InlineKeyboardMarkup:
    """Повертає головне меню з кнопками."""
    keyboard = [
        [
            InlineKeyboardButton("Пальне", callback_data="fuel"),
            InlineKeyboardButton("Комуналка", callback_data="utilities"),
        ],
        [
            InlineKeyboardButton("Ціна за одиницю", callback_data="unit"),
            InlineKeyboardButton("Кредит", callback_data="credit"),
        ],
        [InlineKeyboardButton("Розділити суму", callback_data="split")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    save_user(update)

    text = (
        "Вітаю! Я CalcUA Bot, простий помічник для побутових розрахунків.\n\n"
        "Оберіть потрібний розрахунок кнопкою нижче."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_menu(),
    )


async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запамʼятовує вибраний розрахунок і просить ввести числа."""
    save_user(update)
    query = update.callback_query
    await query.answer()

    if query.data == "utilities":
        keyboard = [
            [
                InlineKeyboardButton("Електроенергія", callback_data="electric"),
                InlineKeyboardButton("Вода", callback_data="water"),
            ],
            [InlineKeyboardButton("Назад", callback_data="back")],
        ]
        await query.message.reply_text(
            "Що рахуємо?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if query.data == "back":
        context.user_data.pop("mode", None)
        await query.message.reply_text("Оберіть розрахунок:", reply_markup=main_menu())
        return

    prompts = {
        "fuel": "Введіть: км розхід_л_на_100 ціна_за_літр\nНаприклад: 371 5.5 74",
        "electric": (
            "Введіть: старий_показник новий_показник тариф\n"
            "Наприклад: 1250 1320 4.32"
        ),
        "water": (
            "Введіть: старий_показник новий_показник тариф\n"
            "Наприклад: 100 112 30.38"
        ),
        "unit": "Введіть: ціна кількість\nНаприклад: 180 3.5",
        "credit": "Введіть: сума відсоток_річних місяців\nНаприклад: 50000 24 12",
        "split": "Введіть: сума кількість_людей\nНаприклад: 1200 4",
    }

    if query.data not in prompts:
        await query.message.reply_text("Оберіть команду з меню.", reply_markup=main_menu())
        return

    context.user_data["mode"] = query.data
    await query.message.reply_text(prompts[query.data])


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробляє числа, які користувач вводить після натискання кнопки."""
    save_user(update)
    mode = context.user_data.get("mode")

    if not mode:
        await update.message.reply_text(
            "Спочатку оберіть потрібний розрахунок.",
            reply_markup=main_menu(),
        )
        return

    context.args = update.message.text.split()

    if mode == "fuel":
        success = await fuel(update, context)
    elif mode == "electric":
        success = await electric(update, context)
    elif mode == "water":
        success = await water(update, context)
    elif mode == "unit":
        success = await unit(update, context)
    elif mode == "credit":
        success = await credit(update, context)
    elif mode == "split":
        success = await split(update, context)
    else:
        await update.message.reply_text("Не вдалося визначити тип розрахунку.")
        return

    if not success:
        await update.message.reply_text("Спробуйте ввести числа ще раз.")
        return

    context.user_data.pop("mode", None)
    await update.message.reply_text("Можете обрати наступний розрахунок:", reply_markup=main_menu())


async def fuel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    save_user(update)
    values = parse_numbers(context.args, 3)

    if values is None:
        await update.message.reply_text("Приклад: /fuel 371 5.5 74")
        return False

    km, consumption, price = values
    if km < 0 or consumption < 0 or price < 0:
        await update.message.reply_text("Усі значення мають бути додатними.")
        return False

    liters = km * consumption / 100
    total = liters * price

    await update.message.reply_text(
        "Розрахунок пального:\n"
        f"Відстань: {km:g} км\n"
        f"Потрібно пального: {liters:.2f} л\n"
        f"Вартість: {money(total)}"
    )
    return True


async def utility(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str) -> bool:
    save_user(update)
    values = parse_numbers(context.args, 3)

    if values is None:
        command = update.message.text.split()[0]
        await update.message.reply_text(
            f"Приклад: {command} старий_показник новий_показник тариф"
        )
        return False

    old_value, new_value, tariff = values
    used = new_value - old_value

    if old_value < 0 or new_value < 0 or tariff < 0:
        await update.message.reply_text("Показники й тариф не можуть бути відʼємними.")
        return False

    if used < 0:
        await update.message.reply_text("Новий показник має бути більшим за старий.")
        return False

    await update.message.reply_text(
        f"{title}:\n"
        f"Спожито: {used:.2f}\n"
        f"До сплати: {money(used * tariff)}"
    )
    return True


async def electric(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return await utility(update, context, "Розрахунок електроенергії")


async def water(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return await utility(update, context, "Розрахунок води")


async def unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    save_user(update)
    values = parse_numbers(context.args, 2)

    if values is None:
        await update.message.reply_text("Приклад: /unit 180 3.5")
        return False

    price, amount = values
    if price < 0 or amount <= 0:
        await update.message.reply_text("Ціна має бути додатною, а кількість більшою за 0.")
        return False

    await update.message.reply_text(
        "Ціна за одиницю:\n"
        f"{money(price / amount)} за 1 кг / 1 л / 1 шт."
    )
    return True


async def credit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    save_user(update)
    values = parse_numbers(context.args, 3)

    if values is None:
        await update.message.reply_text("Приклад: /credit 50000 24 12")
        return False

    principal, annual_rate, months = values
    months_int = int(months)

    if principal <= 0 or annual_rate < 0 or months_int <= 0 or months != months_int:
        await update.message.reply_text(
            "Сума має бути більшою за 0, відсоток не відʼємний, місяці - ціле число."
        )
        return False

    monthly_rate = annual_rate / 100 / 12
    if monthly_rate == 0:
        monthly_payment = principal / months_int
    else:
        monthly_payment = (
            principal
            * monthly_rate
            * (1 + monthly_rate) ** months_int
            / ((1 + monthly_rate) ** months_int - 1)
        )

    total = monthly_payment * months_int

    await update.message.reply_text(
        "Орієнтовний розрахунок кредиту:\n"
        f"Щомісячний платіж: {money(monthly_payment)}\n"
        f"Загальна сума виплат: {money(total)}\n"
        f"Переплата: {money(total - principal)}"
    )
    return True


async def split(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    save_user(update)
    values = parse_numbers(context.args, 2)

    if values is None:
        await update.message.reply_text("Приклад: /split 1200 4")
        return False

    total, people = values
    people_int = int(people)

    if total < 0 or people_int <= 0 or people != people_int:
        await update.message.reply_text(
            "Сума не може бути відʼємною, кількість людей має бути цілим числом більше 0."
        )
        return False

    await update.message.reply_text(
        "Розподіл суми:\n"
        f"Кількість людей: {people_int}\n"
        f"Кожен платить: {money(total / people_int)}"
    )
    return True


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Помилка під час обробки оновлення", exc_info=context.error)


def main() -> None:
    # Токен зберігаємо тільки в .env, щоб не додавати його в код.
    load_dotenv()
    token = os.getenv("BOT_TOKEN")

    if not token:
        raise RuntimeError("Додайте BOT_TOKEN у файл .env")

    init_db()

    # Реєструємо команди бота.
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_button))
    app.add_handler(CommandHandler("fuel", fuel))
    app.add_handler(CommandHandler("electric", electric))
    app.add_handler(CommandHandler("water", water))
    app.add_handler(CommandHandler("unit", unit))
    app.add_handler(CommandHandler("credit", credit))
    app.add_handler(CommandHandler("split", split))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    logger.info("CalcUA Bot запущено")

    # У Python 3.14 event loop не створюється автоматично, тому задаємо його явно.
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling()


if __name__ == "__main__":
    main()
