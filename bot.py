import logging
import os
import base64
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
MODEL = "openai/gpt-4o-mini"

logging.basicConfig(level=logging.INFO)

user_histories = {}


def is_mentioned(update: Update, context) -> bool:
    """Проверяет — бота упомянули или ответили на его сообщение."""
    message = update.effective_message
    bot_username = context.bot.username

    # Ответ на сообщение бота
    if message.reply_to_message and message.reply_to_message.from_user.username == bot_username:
        return True

    # @упоминание бота
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                mentioned = message.text[entity.offset:entity.offset + entity.length]
                if mentioned.lower() == f"@{bot_username.lower()}":
                    return True

    return False


def ask_ai(user_id: int, content) -> str:
    """Отправляет сообщение (текст или фото) в OpenRouter."""
    if user_id not in user_histories:
        user_histories[user_id] = []

    user_histories[user_id].append({"role": "user", "content": content})

    history = user_histories[user_id][-20:]

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": history,
        }
    )

    data = response.json()

    if "choices" not in data:
        raise Exception(f"Ответ OpenRouter: {data}")

    reply = data["choices"][0]["message"]["content"]
    user_histories[user_id].append({"role": "assistant", "content": reply})

    return reply


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я ЛядовGPT 🤖\n"
        "Пиши мне что угодно, отвечу на любой вопрос!\n"
        "Можешь отправить фото — тоже отвечу 📸\n\n"
        "/reset — очистить историю диалога"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_type = update.effective_chat.type

    # В группе — только на упоминание или ответ
    if chat_type in ("group", "supergroup"):
        if not is_mentioned(update, context):
            return

    user_id = update.effective_user.id
    # Убираем @упоминание из текста если есть
    text = message.text.replace(f"@{context.bot.username}", "").strip()

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        reply = ask_ai(user_id, text)
    except Exception as e:
        reply = f"Ошибка: {e}"

    await message.reply_text(reply)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_type = update.effective_chat.type

    # В группе — только на упоминание или ответ
    if chat_type in ("group", "supergroup"):
        if not is_mentioned(update, context):
            return

    user_id = update.effective_user.id

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Скачиваем фото
        photo = message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        image_base64 = base64.b64encode(file_bytes).decode("utf-8")

        caption = message.caption or "Что на этом фото?"

        content = [
            {"type": "text", "text": caption},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{image_base64}"
                }
            }
        ]

        reply = ask_ai(user_id, content)
    except Exception as e:
        reply = f"Ошибка: {e}"

    await message.reply_text(reply)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories.pop(user_id, None)
    await update.message.reply_text("История очищена! Начинаем заново 🔄")


if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("ЛядовGPT запущен!")
    app.run_polling()
