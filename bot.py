from telegram import Update, ReplyKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatAction

from openai import OpenAI
from PIL import Image

import tempfile
import os
import re
import base64

# =========================================
# ENV
# =========================================

TOKEN = os.getenv("8699789330:AAErx6x530YblxPi9x_tRRhDFsZ8b6s0Wvc")
OPENROUTER_API_KEY = ("OPENROUTER_API_KEY")

BOT_USERNAME = os.getenv("@lyadovgpt_bot")
ADMIN_ID = int(os.getenv("1033698004"))

# =========================================
# OPENROUTER CLIENT
# =========================================

client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

# =========================================
# MEMORY
# =========================================

memory = {}
users = set()


def get_memory(user_id: int):
    if user_id not in memory:
        memory[user_id] = []

    return memory[user_id]


def reset_memory(user_id: int):
    memory[user_id] = []


# =========================================
# MENU
# =========================================

menu_keyboard = ReplyKeyboardMarkup(
    [
        ["/start", "/reset"],
    ],
    resize_keyboard=True
)

# =========================================
# CHECK MENTION
# =========================================

async def is_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        update.message.text
        or update.message.caption
        or ""
    )

    # mention
    if BOT_USERNAME.lower() in text.lower():
        return True

    # reply to bot
    if update.message.reply_to_message:
        try:
            me = await context.bot.get_me()

            if update.message.reply_to_message.from_user.id == me.id:
                return True

        except:
            pass

    return False


# =========================================
# START
# =========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.add(update.effective_chat.id)

    await update.message.reply_text(
        "Привет! Я ЛядовGPT 🤖\n\n"
        "🧠 Помню диалог\n"
        "📷 Умею распознавать фото\n"
        "💬 Отвечаю на сообщения",
        reply_markup=menu_keyboard
    )


# =========================================
# RESET MEMORY
# =========================================

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    reset_memory(user_id)

    await update.message.reply_text(
        "Память очищена 🧠",
        reply_markup=menu_keyboard
    )


# =========================================
# PHOTO HANDLER
# =========================================

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.add(update.effective_chat.id)

    user_id = update.effective_user.id

    # группы
    if update.effective_chat.type in ["group", "supergroup"]:
        if not await is_mentioned(update, context):
            return

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        photo = update.message.photo[-1]

        file = await context.bot.get_file(photo.file_id)

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".jpg"
        ) as tf:
            temp_path = tf.name

        await file.download_to_drive(temp_path)

        image = Image.open(temp_path)

        caption = (
            update.message.caption
            or "Опиши подробно что изображено на фото."
        )

        # =========================================
        # MEMORY
        # =========================================

        history = get_memory(user_id)

        prompt = []

        prompt.append(
            "Ты ЛядовGPT. "
            "Отвечай кратко, понятно и на русском."
        )

        for m in history[-10:]:
            prompt.append(
                f"{m['role']}: {m['content']}"
            )

        prompt.append(caption)

        # =========================================
        # IMAGE -> BASE64
        # =========================================

        with open(temp_path, "rb") as img_file:
            base64_image = base64.b64encode(
                img_file.read()
            ).decode("utf-8")

        # =========================================
        # OPENROUTER REQUEST
        # =========================================

        response = client.chat.completions.create(
            model="google/gemini-2.5-flash-lite",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "\n".join(prompt)
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ]
        )

        answer = response.choices[0].message.content

        # =========================================
        # SAVE MEMORY
        # =========================================

        history.append({
            "role": "user",
            "content": f"[Фото]: {caption}"
        })

        history.append({
            "role": "assistant",
            "content": answer
        })

        memory[user_id] = history[-10:]

        await update.message.reply_text(answer)

        os.remove(temp_path)

    except Exception as e:
        print(e)

        await update.message.reply_text(
            "Ошибка обработки фото 📷"
        )


# =========================================
# TEXT HANDLER
# =========================================

async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users.add(update.effective_chat.id)

    user_id = update.effective_user.id

    # группы
    if update.effective_chat.type in ["group", "supergroup"]:
        if not await is_mentioned(update, context):
            return

    msg = update.message.text or ""

    clean_msg = re.sub(
        rf"{re.escape(BOT_USERNAME)}\s*",
        "",
        msg,
        flags=re.IGNORECASE
    ).strip()

    history = get_memory(user_id)

    history.append({
        "role": "user",
        "content": clean_msg
    })

    memory[user_id] = history[-10:]

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        prompt = []

        prompt.append(
            "Ты ЛядовGPT. "
            "Отвечай кратко, понятно и на русском."
        )

        for m in history[-10:]:
            prompt.append(
                f"{m['role']}: {m['content']}"
            )

        response = client.chat.completions.create(
            model="google/gemini-2.5-flash-lite",
            messages=[
                {
                    "role": "user",
                    "content": "\n".join(prompt)
                }
            ]
        )

        answer = response.choices[0].message.content

        history.append({
            "role": "assistant",
            "content": answer
        })

        memory[user_id] = history[-10:]

        await update.message.reply_text(answer)

    except Exception as e:
        print(e)

        await update.message.reply_text(
            "Ошибка OpenRouter API"
        )


# =========================================
# SEND ALL
# =========================================

async def send_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    text = update.message.text

    if not text.startswith("/send "):
        return

    msg = text[6:]

    sent = 0

    for uid in users:
        try:
            await context.bot.send_message(uid, msg)
            sent += 1

        except:
            pass

    await update.message.reply_text(
        f"Отправлено: {sent}"
    )


# =========================================
# COMMANDS MENU
# =========================================

async def post_init(application):
    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("reset", "Очистить память"),
    ]

    await application.bot.set_my_commands(commands)


# =========================================
# APP
# =========================================

app = (
    ApplicationBuilder()
    .token(TOKEN)
    .post_init(post_init)
    .build()
)

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("reset", reset))
app.add_handler(CommandHandler("send", send_all))

app.add_handler(
    MessageHandler(
        filters.PHOTO,
        photo_handler
    )
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        reply
    )
)

print("Бот запущен 🚀")

app.run_polling()