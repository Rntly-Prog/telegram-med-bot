import io
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image
import re
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Регистрация шрифта Times New Roman
pdfmetrics.registerFont(TTFont('TimesNewRoman', 'times.ttf'))

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TOKEN')
if not TOKEN:
    raise ValueError("Необходимо указать токен бота в переменной окружения TOKEN")

# Возможные причины
REASONS = [
    "Болезнь",
    "Семейные обстоятельства",
    "Отпуск",
    "Поездка",
    "Другое"
]

# Хранилище данных пользователей
user_data = {}

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("📝 Начать создание справки", callback_data='create_doc')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! Я помогу тебе создать справку для школы.", reply_markup=reply_markup)

# --- Команда /help ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📌 *Помощь*\n\n"
        "Я помогу тебе сгенерировать справку для школы.\n\n"
        "🔹 Введи ФИО, дату рождения, период отсутствия и причину.\n"
        "🔹 Я создам PDF со справкой, подписью и печатью.\n\n"
        "Кнопки:\n"
        "• /start — начать\n"
        "• /cancel — отменить процесс"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

# --- Команда /cancel ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_data:
        del user_data[user_id]
        await update.message.reply_text("❌ Процесс отменён.", reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("У вас нет активного процесса.")

# --- Обработчик кнопок ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == 'create_doc':
        user_data[user_id] = {'step': 'fio'}
        await query.edit_message_text("Введите ФИО:")
    elif query.data.startswith('reason_'):
        reason = query.data.replace('reason_', '')
        user_data[user_id]['reason'] = reason
        await query.edit_message_text(f"Выбрана причина: {reason}. Генерирую справку...")
        pdf_file = generate_pdf(user_data[user_id])
        await query.message.reply_document(document=pdf_file, filename="spravka.pdf")
        del user_data[user_id]
    elif query.data == 'back_fio':
        user_data[user_id]['step'] = 'fio'
        await query.edit_message_text("Введите ФИО:")
    elif query.data == 'back_dob':
        user_data[user_id]['step'] = 'dob'
        await query.edit_message_text("Введите дату рождения (ДД.ММ.ГГГГ):")
    elif query.data == 'back_dates':
        user_data[user_id]['step'] = 'dates'
        await query.edit_message_text("Укажите даты отсутствия (например, 01.11.2025 - 03.11.2025):")

# --- Обработчик текстовых сообщений ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in user_data:
        await update.message.reply_text("Начните с команды /start")
        return
    step = user_data[user_id].get('step')

    # Кнопка "Назад"
    back_button = None
    if step == 'dob':
        back_button = [[InlineKeyboardButton("⬅️ Назад", callback_data='back_fio')]]
    elif step == 'dates':
        back_button = [[InlineKeyboardButton("⬅️ Назад", callback_data='back_dob')]]
    elif step == 'reason_selection':
        back_button = [[InlineKeyboardButton("⬅️ Назад", callback_data='back_dates')]]

    if step == 'fio':
        if not is_valid_name(text):
            await update.message.reply_text("ФИО введено некорректно. Попробуйте снова.")
            return
        user_data[user_id]['fio'] = text
        reply_markup = InlineKeyboardMarkup(back_button) if back_button else None
        await update.message.reply_text("Введите дату рождения (ДД.ММ.ГГГГ):", reply_markup=reply_markup)
        user_data[user_id]['step'] = 'dob'

    elif step == 'dob':
        if not is_valid_date(text):
            await update.message.reply_text("Дата введена некорректно. Формат: ДД.ММ.ГГГГ")
            return
        user_data[user_id]['dob'] = text
        reply_markup = InlineKeyboardMarkup(back_button) if back_button else None
        await update.message.reply_text("Укажите даты отсутствия (например, 01.11.2025 - 03.11.2025):", reply_markup=reply_markup)
        user_data[user_id]['step'] = 'dates'

    elif step == 'dates':
        if not is_valid_date_range(text):
            await update.message.reply_text("Диапазон дат введен некорректно. Формат: ДД.ММ.ГГГГ - ДД.ММ.ГГГГ")
            return
        user_data[user_id]['dates'] = text
        keyboard = [[InlineKeyboardButton(r, callback_data=f"reason_{r}")] for r in REASONS]
        if back_button:
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='back_dates')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Выберите причину отсутствия:", reply_markup=reply_markup)
        user_data[user_id]['step'] = 'reason_selection'
        
        if update.message.text:  # Только если это текстовое сообщение
        webhook_url = "https://your-n8n-instance.n8n.cloud/webhook/telegram/data"  # 👈 Замените на ваш URL
        payload = {
            "user_id": user_id,
            "username": update.effective_user.username,
            "full_name": update.effective_user.full_name,
            "message": text,
            "step": step,
            "timestamp": update.message.date.isoformat() if update.message.date else ""
        }
        try:
            response = requests.post(webhook_url, json=payload, timeout=5)
            if response.status_code != 200:
                logger.warning(f"Не удалось отправить данные в n8n: {response.status_code}")
        except Exception as e:
            logger.warning(f"Ошибка отправки в n8n: {e}")

def is_valid_name(name):
    return bool(re.match(r"^[A-Za-zА-Яа-яЁё\s\-']+$", name))

def is_valid_date(date_str):
    return bool(re.match(r"^\d{2}\.\d{2}\.\d{4}$", date_str))

def is_valid_date_range(range_str):
    parts = range_str.split(" - ")
    if len(parts) != 2:
        return False
    return all(is_valid_date(p) for p in parts)

def generate_pdf(data):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    c.setFont("TimesNewRoman", 14)
    c.drawCentredString(width / 2, height - 100, "СПРАВКА")

    c.setFont("TimesNewRoman", 12)
    c.drawString(50, height - 140, f"ФИО: {data['fio']}")
    c.drawString(50, height - 160, f"Дата рождения: {data['dob']}")
    c.drawString(50, height - 180, f"Отсутствовал(а) в школе: {data['dates']}")
    c.drawString(50, height - 200, f"Причина: {data['reason']}")

    # Подпись и печать
    c.drawString(50, height - 300, "_______________________")
    c.drawString(50, height - 320, "Подпись")
    c.drawString(50, height - 340, "Печать")

    # Изображения подписи и печати
    try:
        c.drawImage("signature.png", width - 200, height - 320, width=100, height=50)
        c.drawImage("stamp.png", width - 200, height - 360, width=80, height=80)
    except Exception as e:
        logger.warning(f"Не удалось добавить изображения: {e}")

    c.save()
    buffer.seek(0)
    return buffer

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Бот запущен и готов к работе...")
    app.run_polling()

if __name__ == '__main__':

    main()

