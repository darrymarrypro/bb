import telegram
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import PyPDF2
import os
import sqlite3
from openai import OpenAI
from datetime import datetime, timedelta

# Telegram bot token (replace with your bot token)
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

# OpenAI API key (replace with your API key)
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
client = OpenAI(api_key=OPENAI_API_KEY)

# Conversation states
ASK_PDF, PROCESS_TEXT, CONTINUE = range(3)  # Removed ASK_SENTENCE state

# SQLite database setup
def init_db():
    conn = sqlite3.connect("user_data.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_books 
                 (user_id INTEGER, book_name TEXT, last_sentence INTEGER, pdf_path TEXT, text_path TEXT, 
                  PRIMARY KEY (user_id, book_name))''')
    conn.commit()
    conn.close()

# Rate limiting for GPT-4 (simplified; adjust as per actual limits)
last_request_time = None
request_count = 0
REQUEST_LIMIT = 10  # Example limit per minute

def check_rate_limit():
    global last_request_time, request_count
    now = datetime.now()
    if last_request_time is None or (now - last_request_time).seconds >= 60:
        last_request_time = now
        request_count = 0
    if request_count >= REQUEST_LIMIT:
        return False
    request_count += 1
    return True

# Extract text from PDF and save to a text file
def extract_and_save_text(pdf_path, text_path):
    with open(pdf_path, 'rb') as pdf_file:
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        full_text = ""
        for page in pdf_reader.pages:
            full_text += page.extract_text() + "\n"
    with open(text_path, 'w', encoding='utf-8') as text_file:
        text_file.write(full_text)
    return full_text

# Use GPT-4 to find the start of the book
def find_book_start(text):
    # Take the first 1000 characters (or adjust as needed) to analyze
    sample_text = text[:1000]
    prompt = (
        "You are an expert in analyzing book structures. Given the following text extracted from a PDF, "
        "identify the sentence that marks the beginning of the actual book content (e.g., ignoring title pages, "
        "table of contents, or prefaces). Return only the sentence itself without any explanation."
        f"\n\nText: {sample_text}"
    )
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    ).choices[0].message.content
    return response

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    context.user_data['user_id'] = user_id
    await update.message.reply_text(
        "سلام! میں آپ کا ٹیلیگرام بوٹ ہوں۔ براہ کرم مجھے ایک پی ڈی ایف فائل بھیجیں یا '/newbook' استعمال کریں نئی کتاب شروع کرنے کے لیے۔\n"
        "موجودہ کتابوں کی فہرست دیکھنے کے لیے '/listbooks' اور کتاب سوئچ کرنے کے لیے '/switchbook' استعمال کریں۔"
    )
    return ASK_PDF

# Handle new book
async def new_book(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("براہ کرم نئی کتاب کے لیے ایک پی ڈی ایف فائل بھیجیں۔")
    return ASK_PDF

# List books
async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = context.user_data['user_id']
    conn = sqlite3.connect("user_data.db")
    c = conn.cursor()
    c.execute("SELECT book_name, last_sentence FROM user_books WHERE user_id=?", (user_id,))
    books = c.fetchall()
    conn.close()
    if not books:
        await update.message.reply_text("آپ نے ابھی تک کوئی کتاب شروع نہیں کی۔")
    else:
        response = "آپ کی کتابوں کی فہرست:\n"
        for book_name, last_sentence in books:
            response += f"- {book_name} (آخری جملہ: {last_sentence})\n"
        await update.message.reply_text(response)
    return ASK_PDF

# Switch book
async def switch_book(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = context.user_data['user_id']
    conn = sqlite3.connect("user_data.db")
    c = conn.cursor()
    c.execute("SELECT book_name FROM user_books WHERE user_id=?", (user_id,))
    books = c.fetchall()
    conn.close()
    if not books:
        await update.message.reply_text("کوئی کتاب доступ نہیں۔ '/newbook' سے نئی شروع کریں۔")
        return ASK_PDF
    await update.message.reply_text("براہ کرم اس کتاب کا نام بتائیں جس پر سوئچ کرنا چاہتے ہیں:")
    return ASK_PDF

# Handle PDF file and auto-detect start
async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = context.user_data['user_id']
        file = update.message.document
        if not file.file_name.endswith('.pdf'):
            await update.message.reply_text("براہ کرم صرف پی ڈی ایف فائل بھیجیں۔")
            return ASK_PDF
        
        pdf_path = f"pdfs/{user_id}_{file.file_name}"
        text_path = f"texts/{user_id}_{file.file_name}.txt"
        os.makedirs("pdfs", exist_ok=True)
        os.makedirs("texts", exist_ok=True)
        
        telegram_file = await file.get_file()
        await telegram_file.download_to_drive(pdf_path)
        
        # Extract and save text
        full_text = extract_and_save_text(pdf_path, text_path)
        sentences = [s.strip() for s in full_text.split('. ') if s.strip()]
        
        # Find the start of the book using GPT-4
        if not check_rate_limit():
            await update.message.reply_text("GPT-4 کی حد تک پہنچ گئی۔ براہ کرم کچھ دیر انتظار کریں اور دوبارہ کوشش کریں۔")
            return ASK_PDF
        
        start_sentence = find_book_start(full_text)
        start_index = next((i for i, s in enumerate(sentences) if start_sentence in s), 0)  # Default to 0 if not found
        
        context.user_data['pdf_path'] = pdf_path
        context.user_data['text_path'] = text_path
        context.user_data['book_name'] = file.file_name
        context.user_data['sentences'] = sentences
        context.user_data['last_sentence'] = start_index
        
        conn = sqlite3.connect("user_data.db")
        c = conn.cursor()
        c.execute("SELECT last_sentence FROM user_books WHERE user_id=? AND book_name=?", (user_id, file.file_name))
        result = c.fetchone()
        if result:
            context.user_data['last_sentence'] = result[0]
            await update.message.reply_text(f"آپ {file.file_name} پر واپس آئے ہیں۔ آخری جملہ: {result[0]}۔ جاری رکھنے کے لیے '/continue' کہیں۔")
        else:
            c.execute("INSERT OR REPLACE INTO user_books (user_id, book_name, last_sentence, pdf_path, text_path) VALUES (?, ?, ?, ?, ?)",
                      (user_id, file.file_name, start_index, pdf_path, text_path))
            await update.message.reply_text(f"پی ڈی ایف موصول ہو گئی اور کتاب کا آغاز '{start_sentence}' سے خودکار طور پر منتخب کیا گیا۔ اب شروع کر رہا ہوں۔")
            conn.commit()
            conn.close()
            return await process_text(update, context)
        
        conn.commit()
        conn.close()
        return PROCESS_TEXT
    except Exception as e:
        await update.message.reply_text(f"معذرت، ایک خرابی ہوئی: {str(e)}۔ براہ کرم دوبارہ کوشش کریں۔")
        return ASK_PDF

# Process 10 sentences and send to GPT-4
async def process_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = context.user_data['user_id']
        sentences = context.user_data['sentences']
        last_sentence = context.user_data['last_sentence']
        book_name = context.user_data['book_name']
        
        end_index = min(last_sentence + 10, len(sentences))
        text_to_process = '. '.join(sentences[last_sentence:end_index])
        
        if not check_rate_limit():
            await update.message.reply_text("GPT-4 کی حد تک پہنچ گئی۔ براہ کرم کچھ دیر انتظار کریں۔")
            return PROCESS_TEXT
        
        prompt = f'Please ignore all previous instructions. Please respond only in the Urdu language. Do not explain what you are doing. Do not self reference. You are an expert translator. Translate the following text to the Urdu using vocabulary and expressions of a native of Pakistan. The text to be translated is "{text_to_process}"'
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        ).choices[0].message.content
        
        await update.message.reply_text(response)
        
        conn = sqlite3.connect("user_data.db")
        c = conn.cursor()
        c.execute("UPDATE user_books SET last_sentence=? WHERE user_id=? AND book_name=?", (end_index, user_id, book_name))
        conn.commit()
        conn.close()
        
        context.user_data['last_sentence'] = end_index
        await update.message.reply_text("جاری رکھنا چاہتے ہیں؟ 'ہاں' کہیں۔")
        return CONTINUE
    except Exception as e:
        await update.message.reply_text(f"معذرت، ایک خرابی ہوئی: {str(e)}۔ براہ کرم دوبارہ کوشش کریں۔")
        return PROCESS_TEXT

# Handle continue
async def handle_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        response = update.message.text.lower()
        if response == 'ہاں':
            return await process_text(update, context)
        else:
            await update.message.reply_text("ٹھیک ہے، جب چاہیں دوبارہ شروع کریں۔ '/start' استعمال کریں۔")
            return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"معذرت، ایک خرابی ہوئی: {str(e)}۔ براہ کرم دوبارہ کوشش کریں۔")
        return CONTINUE

# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"ایک غیر متوقع خرابی ہوئی: {str(context.error)}۔ براہ کرم دوبارہ کوشش کریں یا مدد کے لیے رابطہ کریں۔")

# Main function to run the bot
def main():
    init_db()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start), CommandHandler('newbook', new_book)],
        states={
            ASK_PDF: [
                MessageHandler(filters.Document.ALL, handle_pdf),
                CommandHandler('listbooks', list_books),
                CommandHandler('switchbook', switch_book)
            ],
            PROCESS_TEXT: [CommandHandler('continue', process_text)],
            CONTINUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_continue)]
        },
        fallbacks=[]
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('listbooks', list_books))
    application.add_handler(CommandHandler('switchbook', switch_book))
    application.add_error_handler(error_handler)
    
    application.run_polling()

if __name__ == '__main__':
    main()
