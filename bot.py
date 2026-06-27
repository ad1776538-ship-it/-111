import logging
import os
import base64
import requests
import json
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))  # Ваш Telegram ID для /broadcast
MODEL = "google/gemini-3.1-flash-lite"

logging.basicConfig(level=logging.INFO)

user_histories = {}
known_users = set()  # Храним всех пользователей для рассылки

USERS_FILE = "users.json"


def load_users():
    """Загружает список пользователей из файла."""
    global known_users
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            known_users = set(json.load(f))


def save_users():
    """Сохраняет список пользователей в файл."""
    with open(USERS_FILE, "w") as f:
        json.dump(list(known_users), f)


def register_user(user_id: int):
    """Регистрирует пользователя."""
    if user_id not in known_users:
        known_users.add(user_id)
        save_users()


def is_mentioned(update: Update, context) -> bool:
    """Проверяет — бота упомянули или ответили на его сообщение."""
    message = update.effective_message
    bot_username = context.bot.username

    if message.reply_to_message and message.reply_to_message.from_user.username == bot_username:
        return True

    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                mentioned = message.text[entity.offset:entity.offset + entity.length]
                if mentioned.lower() == f"@{bot_username.lower()}":
                    return True

    return False


def ask_ai(user_id: int, content) -> str:
    """Отправляет сообщение в OpenRouter."""
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


async def setup_commands(app):
    """Устанавливает меню команд в боте."""
    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("clear", "Очистить историю диалога"),
        BotCommand("broadcast", "Рассылка всем пользователям (только админ)"),
    ]
    await app.bot.set_my_commands(commands)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)
    await update.message.reply_text(
        "Привет! Я ЛядовGPT 🤖\n"
        "Пиши мне что угодно — отвечу на любой вопрос!\n\n"
        "📸 Отправь фото — опишу что на нём\n"
        "🎤 Отправь голосовое — расшифрую и отвечу\n\n"
        "/clear — очистить историю диалога\n"
        "/broadcast — рассылка всем (только для админа)"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_type = update.effective_chat.type

    if chat_type in ("group", "supergroup"):
        if not is_mentioned(update, context):
            return

    user_id = update.effective_user.id
    register_user(user_id)

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

    if chat_type in ("group", "supergroup"):
        if not is_mentioned(update, context):
            return

    user_id = update.effective_user.id
    register_user(user_id)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        photo = message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        image_base64 = base64.b64encode(file_bytes).decode("utf-8")

        caption = message.caption or "Что на этом фото? Опиши подробно."

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
        reply = f"Ошибка при обработке фото: {e}"

    await message.reply_text(reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает голосовые сообщения через Whisper (OpenRouter)."""
    message = update.effective_message
    chat_type = update.effective_chat.type

    if chat_type in ("group", "supergroup"):
        if not is_mentioned(update, context):
            return

    user_id = update.effective_user.id
    register_user(user_id)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Скачиваем голосовое сообщение
        voice = message.voice
        file = await context.bot.get_file(voice.file_id)
        file_bytes = await file.download_as_bytearray()

        # Транскрибируем через OpenAI Whisper (доступен через OpenRouter)
        transcription_response = requests.post(
            url="https://openrouter.ai/api/v1/audio/transcriptions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            },
            files={
                "file": ("voice.ogg", bytes(file_bytes), "audio/ogg"),
            },
            data={
                "model": "openai/whisper-1",
            }
        )

        transcription_data = transcription_response.json()

        if "text" not in transcription_data:
            # Если Whisper недоступен — сообщаем пользователю текст ошибки
            await message.reply_text(
                f"Не удалось расшифровать голосовое: {transcription_data}\n"
                "Попробуй написать текстом."
            )
            return

        transcribed_text = transcription_data["text"].strip()

        if not transcribed_text:
            await message.reply_text("Не удалось распознать речь. Попробуй ещё раз.")
            return

        # Отправляем расшифровку пользователю
        await message.reply_text(f"🎤 Распознано: «{transcribed_text}»\n\n⏳ Думаю...")

        # Отправляем текст в AI
        reply = ask_ai(user_id, transcribed_text)

    except Exception as e:
        reply = f"Ошибка при обработке голосового: {e}"

    await message.reply_text(reply)


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очищает историю диалога пользователя."""
    user_id = update.effective_user.id
    user_histories.pop(user_id, None)
    await update.message.reply_text("🔄 История очищена! Начинаем заново.")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылает сообщение всем пользователям. Только для админа."""
    user_id = update.effective_user.id

    if ADMIN_ID == 0:
        await update.message.reply_text(
            "❌ ADMIN_ID не задан. Добавьте переменную окружения ADMIN_ID со своим Telegram ID."
        )
        return

    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет прав для рассылки.")
        return

    if not context.args:
        await update.message.reply_text(
            "Использование: /broadcast <текст сообщения>\n"
            "Пример: /broadcast Привет всем! Бот обновился 🚀"
        )
        return

    text = " ".join(context.args)
    sent = 0
    failed = 0

    await update.message.reply_text(f"📢 Начинаю рассылку для {len(known_users)} пользователей...")

    for uid in list(known_users):
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ Рассылка завершена!\n"
        f"Доставлено: {sent}\n"
        f"Не доставлено: {failed}"
    )


if __name__ == "__main__":
    load_users()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Устанавливаем меню команд при запуске
    app.post_init = setup_commands

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    print("ЛядовGPT запущен!")
    app.run_polling()