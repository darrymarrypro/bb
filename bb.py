import os
import json
import fitz  # PyMuPDF for PDF text extraction
import logging
from telegram import Update, ForceReply
from telegram.ext import Updater, CommandHandler, MessageHandler, filters, CallbackContext
import openai

# Configure logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Set OpenAI API Key
#OPENAI_API_KEY = "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"
#BOT_TOKEN = "7637629184:AAEsa6EKWKecr3GNkaE_xxguA73AtFee7go"
USER_SESSIONS = {}
BOOK_STORAGE = "user_books.json"

# Load user session data
if os.path.exists(BOOK_STORAGE):
    with open(BOOK_STORAGE, "r") as f:
        USER_SESSIONS = json.load(f)

def save_sessions():
    with open(BOOK_STORAGE, "w") as f:
        json.dump(USER_SESSIONS, f)

def start(update: Update, context: CallbackContext) -> None:
    user_id = str(update.message.chat_id)
    if user_id not in USER_SESSIONS:
        USER_SESSIONS[user_id] = {"current_book": None, "progress": {}, "book_list": []}
        save_sessions()
    update.message.reply_text("Send me a PDF file to start.")

def handle_document(update: Update, context: CallbackContext) -> None:
    user_id = str(update.message.chat_id)
    file = update.message.document
    if file.mime_type != "application/pdf":
        update.message.reply_text("Please send a valid PDF file.")
        return
    
    file_path = f"user_{user_id}_{file.file_name}"
    pdf_file = context.bot.get_file(file.file_id)
    pdf_file.download(file_path)
    
    USER_SESSIONS[user_id]["current_book"] = file_path
    if file_path not in USER_SESSIONS[user_id]["book_list"]:
        USER_SESSIONS[user_id]["book_list"].append(file_path)
    USER_SESSIONS[user_id]["progress"][file_path] = {"last_position": 0}
    save_sessions()
    
    update.message.reply_text("PDF received. Please send a sentence to locate the starting point.")

def find_starting_point(pdf_path, sentence):
    doc = fitz.open(pdf_path)
    
    for page in doc:
        text = page.get_text("text")
        if sentence in text:
            start_pos = text.index(sentence)
            return page, start_pos, text
    return None, None, None

def extract_text(pdf_path, start_page, start_pos, sentences=10):
    doc = fitz.open(pdf_path)
    text_content = ""
    sentence_count = 0
    capture = False
    
    for page in doc:
        if capture or page == start_page:
            text = page.get_text("text")[start_pos:]
            for word in text.split(". "):
                text_content += word + ". "
                sentence_count += 1
                if sentence_count >= sentences:
                    return text_content.strip()
            capture = True
    return text_content.strip()

def translate_text(text):
    prompt = ("Please ignore all previous instructions. Please respond only in the Urdu language. "
              "Do not explain what you are doing. Do not self reference. "
              "You are an expert translator. Translate the following text to Urdu using vocabulary "
              "and expressions of a native of Pakistan. The text to be translated is \"" + text + "\"")
    
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "system", "content": prompt}]
    )
    return response["choices"][0]["message"]["content"]

def handle_sentence(update: Update, context: CallbackContext) -> None:
    user_id = str(update.message.chat_id)
    pdf_path = USER_SESSIONS[user_id]["current_book"]
    if not pdf_path:
        update.message.reply_text("Please send a PDF file first.")
        return
    
    sentence = update.message.text
    page, start_pos, text = find_starting_point(pdf_path, sentence)
    if not page:
        update.message.reply_text("Could not find the sentence in the document.")
        return
    
    extracted_text = extract_text(pdf_path, page, start_pos, sentences=10)
    translated_text = translate_text(extracted_text)
    update.message.reply_text(translated_text, parse_mode="Markdown")
    
    USER_SESSIONS[user_id]["progress"][pdf_path]["last_position"] = start_pos + len(extracted_text)
    save_sessions()
    update.message.reply_text("Type 'continue' to translate the next section or 'new' to start another book.")

def handle_continue(update: Update, context: CallbackContext) -> None:
    user_id = str(update.message.chat_id)
    pdf_path = USER_SESSIONS[user_id]["current_book"]
    last_pos = USER_SESSIONS[user_id]["progress"].get(pdf_path, {}).get("last_position", 0)
    
    extracted_text = extract_text(pdf_path, None, last_pos, sentences=10)
    if not extracted_text:
        update.message.reply_text("End of document reached.")
        return
    
    translated_text = translate_text(extracted_text)
    update.message.reply_text(translated_text, parse_mode="Markdown")
    
    USER_SESSIONS[user_id]["progress"][pdf_path]["last_position"] = last_pos + len(extracted_text)
    save_sessions()
    update.message.reply_text("Type 'continue' to translate the next section or 'new' to start another book.")

def main():
    updater = Updater(BOT_TOKEN)
    dispatcher = updater.dispatcher
    
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(MessageHandler(Filters.document, handle_document))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_sentence))
    dispatcher.add_handler(MessageHandler(Filters.regex("^(continue)$"), handle_continue))
    
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
