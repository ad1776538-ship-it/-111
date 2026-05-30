from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatAction
from mistralai import Mistral
import re
import os

# ================== CONFIG ==================
TOKEN = os.getenv("TOKEN", "8699789330:AAErx6x530YblxPi9x_tRRhDFsZ8b6s0Wvc")
MISTRAL_KEY = os.getenv("MISTRAL_KEY", "exhmzbfqfObMXGzRWnoegszu17lseJfM")
BOT_USERNAME = os.getenv("BOT_USERNAME", "@lyadovgpt_bot")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1033698004"))

client = Mistral(api_key=MISTRAL_KEY)

# ================== MEMORY ==================
memory = {}
users = set()


def get_memory(user_id: int):
    if user_id not in memory:
        memory[user_id] = []
    return memory[user_id]


def reset_memory(user_id: int):
    memory[user_id] = []


# ================== UI MENU ==================
menu_keyboard = ReplyKeyboardMarkup(
    [["/start", "/reset"]],
    resize_keyboard=True
)


# ================== TRIGGER LOGIC ==================
def should_reply(update: Update):
    message = update.message
    if not message:
        return False

    text = message.text or ""

    if BOT_USERNAME and BOT_USERNAME.lower() in text.lower():
        return True

    if message.reply_to_message:
        try:
            if message.reply_to_message.from_user.id == update.get_bot().id:
                return True
        except:
            pass

    return False


# ================== COMMANDS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.add(update.effective_chat.id)

    await update.message.reply_text(
        "Привет! Я ЛядовGPT 🤖",
        reply_markup=menu_keyboard
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_memory(update.effective_user.id)

    await update.message.reply_text(
        "Память очищена 🧠",
        reply_markup=menu_keyboard
    )


# ================== PHOTO HANDLER ==================
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.add(update.effective_chat.id)

    if not should_reply(update):
        return

    await update.message.reply_text(
        "Я не могу рассматривать фотографии 📷"
    )


# ================== TEXT HANDLER ==================
async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.add(update.effective_chat.id)

    if update.effective_chat.type in ["group", "supergroup"]:
        if not should_reply(update):
            return

    msg = update.message.text or ""

    clean_msg = re.sub(
        rf"{re.escape(BOT_USERNAME)}\s*",
        "",
        msg,
        flags=re.IGNORECASE
    ).strip()

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        history = get_memory(update.effective_user.id)

        history.append({"role": "user", "content": clean_msg})
        history = history[-10:]
        memory[update.effective_user.id] = history

        r = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты ЛядовGPT — чат-бот. "
                        "Ты представляешься как Даниил Лядов, родился 26 ноября 2010 года. "
                        "Отвечай средне по длине: не слишком коротко и не слишком длинно. "
                        "Пиши на русском, понятно и по делу."
                    )
                },
                *history
            ]
        )

        answer = r.choices[0].message.content

        history.append({"role": "assistant", "content": answer})
        memory[update.effective_user.id] = history[-10:]

        await update.message.reply_text(answer)

    except Exception as e:
        print(e)
        await update.message.reply_text("Ошибка API")


# ================== ADMIN SEND ==================
async def send_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if not update.message.text.startswith("/send "):
        return

    msg = update.message.text[6:]

    for uid in users:
        try:
            await context.bot.send_message(uid, msg)
        except:
            pass


# ================== APP ==================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("reset", reset))
app.add_handler(CommandHandler("send", send_all))

app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))

print("Бот запущен")
app.run_polling()