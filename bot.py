import logging
import os
import base64
import requests
import json
from io import BytesIO
from datetime import datetime
from telegram import Update, BotCommand, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
MODEL = "google/gemini-3.1-flash-lite"
IMAGE_MODEL = "google/gemini-2.5-flash-image"  # модель для генерации картинок на OpenRouter

logging.basicConfig(level=logging.INFO)

user_histories = {}

DAILY_LIMIT = 1  # лимит генераций фото в день на пользователя
daily_usage = {}  # user_id -> {"date": "YYYY-MM-DD", "image": int}
pending_action = {}  # user_id -> "image" (ждём от пользователя описание после нажатия кнопки)

IMAGE_BUTTON_TEXT = "🎨 Сгенерировать фото"

main_keyboard = ReplyKeyboardMarkup(
    [[IMAGE_BUTTON_TEXT]],
    resize_keyboard=True
)


def has_limit_left(user_id: int, kind: str) -> bool:
    """Проверяет, есть ли ещё лимит на сегодня, не списывая его."""
    today = datetime.now().strftime("%Y-%m-%d")
    entry = daily_usage.setdefault(user_id, {"date": today, "image": 0})

    if entry["date"] != today:
        entry["date"] = today
        entry["image"] = 0

    return entry[kind] < DAILY_LIMIT


def consume_limit(user_id: int, kind: str):
    """Списывает лимит (вызывать только после успешной генерации)."""
    today = datetime.now().strftime("%Y-%m-%d")
    entry = daily_usage.setdefault(user_id, {"date": today, "image": 0})
    if entry["date"] != today:
        entry["date"] = today
        entry["image"] = 0
    entry[kind] += 1


def is_mentioned(update: Update, context) -> bool:
    message = update.effective_message
    bot_username = context.bot.username

    if message.reply_to_message and message.reply_to_message.from_user and \
       message.reply_to_message.from_user.username == bot_username:
        return True

    entities = message.entities or message.caption_entities or []
    text = message.text or message.caption or ""
    for entity in entities:
        if entity.type == "mention":
            mentioned = text[entity.offset:entity.offset + entity.length]
            if mentioned.lower() == f"@{bot_username.lower()}":
                return True

    return False


def ask_ai(user_id: int, content) -> str:
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
        json={"model": MODEL, "messages": history}
    )

    data = response.json()
    if "choices" not in data:
        raise Exception(f"Ответ OpenRouter: {data}")

    reply = data["choices"][0]["message"]["content"]
    user_histories[user_id].append({"role": "assistant", "content": reply})
    return reply


def ask_ai_with_audio(user_id: int, audio_base64: str, mime_type: str = "audio/ogg") -> str:
    if user_id not in user_histories:
        user_histories[user_id] = []

    content = [
        {
            "type": "text",
            "text": "Это голосовое сообщение. Ответь на него как ИИ-ассистент. Не пиши расшифровку, сразу отвечай по смыслу."
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{audio_base64}"}
        }
    ]

    user_histories[user_id].append({"role": "user", "content": content})
    history = user_histories[user_id][-20:]

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": MODEL, "messages": history}
    )

    data = response.json()
    if "choices" not in data:
        raise Exception(f"Ответ OpenRouter: {data}")

    reply = data["choices"][0]["message"]["content"]
    user_histories[user_id].append({"role": "assistant", "content": reply})
    return reply


def generate_image(prompt: str, retries: int = 1) -> bytes:
    """Генерирует изображение через OpenRouter (Gemini image-модель) и возвращает байты PNG/JPEG.
    Превью-модель иногда отвечает только текстом без картинки — в этом случае пробуем ещё раз."""
    last_error = None

    for attempt in range(retries):
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": IMAGE_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "modalities": ["image", "text"],
            }
        )

        data = response.json()
        if "choices" not in data:
            last_error = Exception(f"Ответ OpenRouter: {data}")
            continue

        message = data["choices"][0]["message"]
        images = message.get("images")

        if images:
            image_url = images[0]["image_url"]["url"]  # формат: data:image/png;base64,XXXXX
            header, encoded = image_url.split(",", 1)
            return base64.b64decode(encoded)

        # Модель не вернула картинку — запомним ответ и попробуем ещё раз
        text_reply = message.get("content", "")
        last_error = Exception(f"модель не вернула изображение (ответ: «{text_reply[:150]}»)")

    raise last_error


async def setup_commands(app):
    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("clear", "Очистить историю диалога"),
        BotCommand("image", "Сгенерировать изображение по описанию"),
    ]
    await app.bot.set_my_commands(commands)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я ЛядовGPT 🤖\n"
        "Пиши мне что угодно — отвечу на любой вопрос!\n\n"
        "📸 Отправь фото — опишу что на нём\n"
        "🎤 Отправь голосовое — отвечу\n"
        "🎨 /image <описание> — сгенерирую изображение (кнопка ниже тоже работает)\n\n"
        "/clear — очистить историю диалога",
        reply_markup=main_keyboard
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_type = update.effective_chat.type
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text = message.text.strip()

    # Нажатие кнопки "Сгенерировать фото"
    if text == IMAGE_BUTTON_TEXT:
        pending_action[user_id] = "image"
        await message.reply_text("🎨 Опиши, что нарисовать, и просто отправь сообщение:")
        return

    # Пользователь ранее нажал кнопку и сейчас прислал описание
    if user_id in pending_action:
        pending_action.pop(user_id)
        await do_generate_image(update, context, text)
        return

    if chat_type in ("group", "supergroup"):
        if not is_mentioned(update, context):
            return

    text = text.replace(f"@{context.bot.username}", "").strip()

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply = ask_ai(user_id, text)
    except Exception as e:
        reply = f"Ошибка: {e}"

    await message.reply_text(reply)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_type = update.effective_chat.type
    chat_id = update.effective_chat.id

    if chat_type in ("group", "supergroup"):
        if not is_mentioned(update, context):
            return

    user_id = update.effective_user.id

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        photo = message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        file_bytes = await file.download_as_bytearray()
        image_base64 = base64.b64encode(file_bytes).decode("utf-8")

        caption = message.caption or "Что на этом фото? Опиши подробно."
        content = [
            {"type": "text", "text": caption},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
        ]

        reply = ask_ai(user_id, content)
    except Exception as e:
        reply = f"Ошибка при обработке фото: {e}"

    await message.reply_text(reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_type = update.effective_chat.type
    chat_id = update.effective_chat.id

    if chat_type in ("group", "supergroup"):
        if not is_mentioned(update, context):
            return

    user_id = update.effective_user.id

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        voice = message.voice
        file = await context.bot.get_file(voice.file_id)
        file_bytes = await file.download_as_bytearray()
        audio_base64 = base64.b64encode(file_bytes).decode("utf-8")

        reply = ask_ai_with_audio(user_id, audio_base64, mime_type="audio/ogg")
    except Exception as e:
        reply = f"Ошибка при обработке голосового: {e}"

    await message.reply_text(reply)


async def do_generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    message = update.effective_message
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not has_limit_left(user_id, "image"):
        await message.reply_text(
            f"⛔ Лимит на сегодня исчерпан ({DAILY_LIMIT} фото в день). Приходи завтра!",
            reply_markup=main_keyboard
        )
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")

    try:
        image_bytes = generate_image(prompt)
        consume_limit(user_id, "image")
        await message.reply_photo(photo=BytesIO(image_bytes), caption=f"🎨 {prompt}", reply_markup=main_keyboard)
    except Exception as e:
        await message.reply_text(f"Не получилось сгенерировать изображение: {e}", reply_markup=main_keyboard)


async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.effective_message.reply_text(
            "Напиши описание после команды, например:\n/image рыжий кот в скафандре на луне"
        )
        return
    await do_generate_image(update, context, prompt)


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories.pop(user_id, None)
    await update.message.reply_text("🔄 История очищена! Начинаем заново.")


if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.post_init = setup_commands

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("image", image_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    print("ЛядовGPT запущен!")
    app.run_polling()
