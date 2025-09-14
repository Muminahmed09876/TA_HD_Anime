import os
import asyncio
import time
import threading
import re
import hashlib
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified, FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pymongo import MongoClient
from dotenv import load_dotenv
from flask import Flask, render_template_string
import requests

# --- Load Environment Variables ---
load_dotenv()

# --- Bot Configuration ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
PORT = int(os.environ.get("PORT"))

CHANNEL_ID = -1003094281207 
LOG_CHANNEL_ID = -1002623880704 

# --- MongoDB Configuration ---
MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = "TA_HD_Anime"
COLLECTION_NAME = "bot_data"

# --- In-memory data structures ---
filters_dict = {}
user_list = set()
last_filter = None
banned_users = set()
restrict_status = False
autodelete_time = 0
user_states = {}

# --- Join Channels Configuration ---
CHANNEL_ID_2 = -1003049936443
CHANNEL_LINK = "https://t.me/TA_HD_Anime"
CHANNEL_ID_3 = -1003097080109
CHANNEL_LINK_2 = "https://t.me/TA_XVideos"

join_channels = [
    {"id": CHANNEL_ID_2, "name": "TA HD Anime Hindi Official Dubbed", "link": CHANNEL_LINK},
    {"id": CHANNEL_ID_3, "name": "TA Xvideos", "link": CHANNEL_LINK_2}
]

# --- Database Client and Collection ---
mongo_client = None
db = None
collection = None

# --- Flask Web Server ---
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    html_content = """
    <!DOCTYPE:html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Bot Status</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background-color: #f0f2f5;
                color: #333;
                text-align: center;
                padding-top: 50px;
            }
            .container {
                background-color: #fff;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 4px 8px rgba(0,0,0,0.1);
                display: inline-block;
            }
            h1 {
                color: #28a745;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>TA File Share Bot is running! ✅</h1>
            <p>This page confirms that the bot's web server is active.</p>
        </div>
    </body>
    </html>
    """
    return render_template_string(html_content)

def ping_service():
    if not RENDER_EXTERNAL_HOSTNAME:
        print("Render URL is not set. Ping service is disabled.")
        return

    url = f"http://{RENDER_EXTERNAL_HOSTNAME}"
    while True:
        try:
            response = requests.get(url, timeout=10)
            print(f"Pinged {url} | Status Code: {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"Error pinging {url}: {e}")
        time.sleep(600)

# --- Database Functions ---
def connect_to_mongodb():
    global mongo_client, db, collection
    try:
        mongo_client = MongoClient(MONGO_URI)
        db = mongo_client[DB_NAME]
        collection = db[COLLECTION_NAME]
        print("Successfully connected to MongoDB.")
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        exit(1)

def save_data():
    global filters_dict, user_list, last_filter, banned_users, restrict_status, autodelete_time, user_states
    str_user_states = {str(uid): state for uid, state in user_states.items()}
    data = {
        "filters_dict": filters_dict,
        "user_list": list(user_list),
        "last_filter": last_filter,
        "banned_users": list(banned_users),
        "restrict_status": restrict_status,
        "autodelete_time": autodelete_time,
        "user_states": str_user_states
    }
    collection.update_one({"_id": "bot_data"}, {"$set": data}, upsert=True)
    print("Data saved successfully to MongoDB.")

def load_data():
    global filters_dict, user_list, last_filter, banned_users, restrict_status, autodelete_time, user_states
    data = collection.find_one({"_id": "bot_data"})
    if data:
        filters_dict = data.get("filters_dict", {})
        user_list = set(data.get("user_list", []))
        banned_users = set(data.get("banned_users", []))
        last_filter = data.get("last_filter", None)
        restrict_status = data.get("restrict_status", False)
        autodelete_time = data.get("autodelete_time", 0)
        loaded_user_states = data.get("user_states", {})
        user_states = {int(uid): state for uid, state in loaded_user_states.items()}
        print("Data loaded successfully from MongoDB.")
    else:
        print("No data found in MongoDB. Starting with empty data.")
        save_data()

# --- Pyrogram Client ---
app = Client(
    "ta_file_share_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# --- Helper Functions (Pyrogram) ---
def get_short_id(keyword):
    return hashlib.sha256(keyword.encode('utf-8')).hexdigest()[:8]

async def is_user_member(client, user_id):
    try:
        for channel in join_channels:
            await client.get_chat_member(channel['id'], user_id)
        return True
    except UserNotParticipant:
        return False
    except Exception as e:
        print(f"Error checking membership: {e}")
        return False

async def delete_messages_later(chat_id, message_ids, delay_seconds):
    await asyncio.sleep(delay_seconds)
    try:
        await app.delete_messages(chat_id, message_ids)
        print(f"Successfully deleted messages {message_ids} in chat {chat_id}.")
    except Exception as e:
        print(f"Error deleting messages {message_ids} in chat {chat_id}: {e}")

def create_paged_buttons(keyword, button_list, page, page_size=10):
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    current_page_buttons = button_list[start_index:end_index]
    
    keyboard = []
    
    for button_data in current_page_buttons:
        if 'link' in button_data and button_data['link']:
            keyboard.append([InlineKeyboardButton(button_data['text'], url=button_data['link'])])
        else:
            keyboard.append([InlineKeyboardButton(button_data['text'], callback_data="ignore")])

    total_pages = (len(button_list) + page_size - 1) // page_size
    nav_row = []
    
    if page > 1:
        nav_row.append(InlineKeyboardButton("⏪ Previous", callback_data=f"page_{keyword}_{page - 1}"))
    
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="ignore"))
    
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ⏩", callback_data=f"page_{keyword}_{page + 1}"))
    
    if len(nav_row) > 1:
        keyboard.append(nav_row)

    return InlineKeyboardMarkup(keyboard)

def parse_inline_buttons_from_text(text):
    button_data = []
    button_pairs = text.split(',')
    
    for pair in button_pairs:
        pair = pair.strip()
        if pair.startswith('[') and pair.endswith(']'):
            button_text = pair[1:-1].strip()
            button_data.append({'text': f"🎬 {button_text} 🎬", 'link': None})
        else:
            parts = pair.split(' = ', 1)
            if len(parts) == 2:
                button_text = parts[0].strip()
                button_link = parts[1].strip()
                button_data.append({'text': button_text, 'link': button_link})
            
    return button_data

def create_paged_edit_buttons(keyword, button_list, page, page_size=10):
    short_id = get_short_id(keyword)
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    current_page_buttons = button_list[start_index:end_index]
    
    keyboard = []
    
    for i, button_data in enumerate(current_page_buttons, start=start_index + 1):
        keyboard.append([InlineKeyboardButton(f"#{i} {button_data['text']}", callback_data="ignore")])

    total_pages = (len(button_list) + page_size - 1) // page_size
    nav_row = []
    
    if page > 1:
        nav_row.append(InlineKeyboardButton("⏪ Previous", callback_data=f"editpage_{short_id}_{page - 1}"))
    
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="ignore"))
    
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ⏩", callback_data=f"editpage_{short_id}_{page + 1}"))
    
    if nav_row:
        keyboard.append(nav_row)

    edit_row = [
        InlineKeyboardButton("➕ Add", callback_data=f"edit_add_{short_id}"),
        InlineKeyboardButton("🗑️ Delete", callback_data=f"edit_delete_{short_id}"),
        InlineKeyboardButton("🔄 Set", callback_data=f"edit_set_{short_id}")
    ]
    keyboard.append(edit_row)
    
    return InlineKeyboardMarkup(keyboard)

def parse_button_numbers(text, max_index):
    numbers = set()
    parts = re.split(r',\s*', text)
    for part in parts:
        if '-' in part:
            start, end = map(int, part.split('-'))
            numbers.update(range(start, end + 1))
        else:
            numbers.add(int(part))
    
    for num in numbers:
        if not (1 <= num <= max_index):
            raise ValueError(f"Button number {num} is out of range.")
            
    return sorted(list(numbers))

def parse_swap_pairs(text, max_index):
    pairs = []
    parts = re.split(r',\s*', text)
    for part in parts:
        if '-' in part:
            i, j = map(int, part.split('-'))
            if not (1 <= i <= max_index and 1 <= j <= max_index):
                raise ValueError(f"Invalid swap numbers {i} or {j}.")
            pairs.append((i, j))
        else:
            raise ValueError("Invalid pair format. Use `i-j`.")
    return pairs

# --- Message Handlers (Pyrogram) ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    user_id = message.from_user.id
    user_list.add(user_id)
    save_data()
    
    if user_id in banned_users:
        return await message.reply_text("❌ **You are banned from using this bot.**")

    user = message.from_user
    log_message = (
        f"➡️ **New User**\n"
        f"🆔 User ID: `{user_id}`\n"
        f"👤 Full Name: `{user.first_name} {user.last_name or ''}`"
    )
    if user.username:
        log_message += f"\n🔗 Username: @{user.username}"
    try:
        await client.send_message(LOG_CHANNEL_ID, log_message, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"Failed to send log message: {e}")
    
    args = message.text.split(maxsplit=1)
    deep_link_keyword = args[1].lower() if len(args) > 1 else None
    
    if deep_link_keyword:
        log_link_message = (
            f"🔗 **New Deep Link Open!**\n\n"
            f"🆔 User ID: `{user.id}`\n"
            f"👤 User Name: `{user.first_name} {user.last_name or ''}`\n"
            f"🔗 Link: `https://t.me/{(await client.get_me()).username}?start={deep_link_keyword}`"
        )
        if user.username:
            log_link_message += f"\nUsername: @{user.username}"
        try:
            await client.send_message(LOG_CHANNEL_ID, log_link_message, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            print(f"Failed to log deep link message: {e}")
            
        if not await is_user_member(client, user_id):
            buttons = []
            for channel in join_channels:
                try:
                    await client.get_chat_member(channel['id'], user_id)
                except UserNotParticipant:
                    buttons.append([InlineKeyboardButton(f"✅ Join {channel['name']}", url=channel['link'])])
            bot_username = (await client.get_me()).username
            try_again_url = f"https://t.me/{bot_username}?start={deep_link_keyword}" if deep_link_keyword else f"https://t.me/{bot_username}"
            buttons.append([InlineKeyboardButton("🔄 Try Again", url=try_again_url)])
            keyboard = InlineKeyboardMarkup(buttons)
            return await message.reply_text(
                "❌ **You must join the following channels to use this bot:**",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )

        if deep_link_keyword and deep_link_keyword in filters_dict:
            filter_data = filters_dict[deep_link_keyword]
            if 'button_data' in filter_data and filter_data['button_data']:
                reply_text = filter_data.get('message_text', "Select an option:")
                reply_markup = create_paged_buttons(deep_link_keyword, filter_data['button_data'], 1)
                await message.reply_text(reply_text, reply_markup=reply_markup)
            elif 'file_ids' in filter_data and filter_data['file_ids']:
                if autodelete_time > 0:
                    minutes = autodelete_time // 60
                    hours = autodelete_time // 3600
                    if hours > 0:
                        delete_time_str = f"{hours} hour{'s' if hours > 1 else ''}"
                    else:
                        delete_time_str = f"{minutes} minute{'s' if minutes > 1 else ''}"
                    await message.reply_text(f"✅ **Files found!** Sending now. Please note, these files will be automatically deleted in **{delete_time_str}**.", parse_mode=ParseMode.MARKDOWN)
                else:
                    await message.reply_text(f"✅ **Files found!** Sending now...")
                sent_message_ids = []
                for file_id in filter_data['file_ids']:
                    try:
                        sent_msg = await app.copy_message(message.chat.id, CHANNEL_ID, file_id)
                        sent_message_ids.append(sent_msg.id)
                    except Exception as e:
                        print(f"Failed to copy file {file_id}: {e}")
                if autodelete_time > 0 and sent_message_ids:
                    asyncio.create_task(delete_messages_later(message.chat.id, sent_message_ids, autodelete_time))
            else:
                await message.reply_text("❌ **No filter data found for this keyword.**", parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text("❌ **Invalid keyword.**", parse_mode=ParseMode.MARKDOWN)
    else:
        await message.reply_text("Hello! I am a file share bot. Send me a keyword to get started.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Find Filters", callback_data="list_filters")]]))

# /set_filter কমান্ড হ্যান্ডলার
@app.on_message(filters.command("set_filter") & filters.private & filters.user(ADMIN_ID))
async def set_filter_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "set_filter"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে ফিল্টারের কিওয়ার্ড, মেসেজের টেক্সট, এবং ফাইল/বোতাম ডেটা পাঠান।**\n\n**উদাহরণ:** `keyword | message text | button1 = link1, button2 = link2`\n\n**অথবা:** `keyword | message text` (যদি আপনি শুধু ফাইল যোগ করতে চান)")

# মেসেজ হ্যান্ডলার (অন্যান্য টেক্সট)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "set_filter"))
async def process_filter_message(client, message):
    user_id = message.from_user.id
    try:
        parts = message.text.split("|")
        keyword = parts[0].strip().lower()
        if not keyword:
            raise ValueError("Keyword cannot be empty.")
            
        message_text = parts[1].strip() if len(parts) > 1 else ""
        button_text = parts[2].strip() if len(parts) > 2 else ""

        filter_data = {
            "keyword": keyword,
            "message_text": message_text,
            "file_ids": [],
            "button_data": parse_inline_buttons_from_text(button_text)
        }
        filters_dict[keyword] = filter_data
        save_data()

        await message.reply_text(
            f"✅ **Filter '{keyword}' set successfully!**\n\n"
            "➡️ **Now, forward files to me to add them to this filter, or type `/done` to finish.**"
        )
        user_states[user_id] = {"command": "add_file_to_filter", "keyword": keyword}
        save_data()
    except Exception as e:
        await message.reply_text(f"❌ **Error:** Invalid format. Please try again.\n\n`{e}`")
        del user_states[user_id]
        save_data()

# ফাইল হ্যান্ডলার (filter_set এর সময়)
@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "add_file_to_filter"))
async def add_file_to_filter_handler(client, message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    if not state or "keyword" not in state:
        return

    keyword = state["keyword"]

    if message.media and filters_dict.get(keyword):
        file_id = message.media.file_id
        if file_id not in filters_dict[keyword]["file_ids"]:
            filters_dict[keyword]["file_ids"].append(file_id)
            save_data()
            await message.reply_text(f"✅ **File added to filter '{keyword}'.**")
        else:
            await message.reply_text("⚠️ **This file is already in the filter.**")
    else:
        await message.reply_text("❌ **Please forward a file.**")

# /done কমান্ড হ্যান্ডলার
@app.on_message(filters.command("done") & filters.private & filters.user(ADMIN_ID))
async def done_cmd(client, message):
    user_id = message.from_user.id
    if user_id in user_states:
        state = user_states[user_id]
        if state.get("command") == "add_file_to_filter":
            await message.reply_text("✅ **Filter setting is complete.**")
        del user_states[user_id]
        save_data()
    else:
        await message.reply_text("⚠️ **No active command to finish.**")

# /get_filter কমান্ড হ্যান্ডলার
@app.on_message(filters.command("get_filter") & filters.private & filters.user(ADMIN_ID))
async def get_filter_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "get_filter_awaiting_keyword"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে ফিল্টারের কিওয়ার্ডটি টাইপ করুন।**")

# মেসেজ হ্যান্ডলার (get_filter এর সময়)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "get_filter_awaiting_keyword"))
async def process_get_filter_message(client, message):
    user_id = message.from_user.id
    keyword = message.text.lower().strip()
    if keyword in filters_dict:
        filter_data = filters_dict[keyword]
        if filter_data.get('button_data'):
            await message.reply_text("⚠️ **This filter has buttons. Please use /edit_filter instead.**")
        elif filter_data.get('file_ids'):
            file_ids = filter_data['file_ids']
            file_count = len(file_ids)
            await message.reply_text(f"✅ **Filter '{keyword}' has {file_count} files. Sending now.**")
            for file_id in file_ids:
                try:
                    await app.copy_message(message.chat.id, CHANNEL_ID, file_id)
                except Exception as e:
                    await message.reply_text(f"❌ **Error copying file {file_id}:** `{e}`")
        else:
            await message.reply_text(f"⚠️ **Filter '{keyword}' exists but has no files.**")
    else:
        await message.reply_text("❌ **Filter not found.**")

    del user_states[user_id]
    save_data()
    
# /edit_filter কমান্ড হ্যান্ডলার
@app.on_message(filters.command("edit_filter") & filters.private & filters.user(ADMIN_ID))
async def edit_filter_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "edit_filter_awaiting_keyword"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে ফিল্টারের কিওয়ার্ডটি টাইপ করুন।**")
    
# মেসেজ হ্যান্ডলার (edit_filter এর সময়)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "edit_filter_awaiting_keyword"))
async def process_edit_filter_message(client, message):
    user_id = message.from_user.id
    keyword = message.text.lower().strip()
    if keyword in filters_dict:
        filter_data = filters_dict[keyword]
        if filter_data.get('button_data'):
            reply_text = filter_data.get('message_text', "Select an option:")
            reply_markup = create_paged_edit_buttons(keyword, filter_data['button_data'], 1)
            await message.reply_text(reply_text, reply_markup=reply_markup)
        else:
            await message.reply_text("⚠️ **This filter has no buttons to edit.**")
    else:
        await message.reply_text("❌ **Filter not found.**")
    del user_states[user_id]
    save_data()
    
# /delete_filter কমান্ড হ্যান্ডলার
@app.on_message(filters.command("delete_filter") & filters.private & filters.user(ADMIN_ID))
async def delete_filter_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "delete_filter_awaiting_keyword"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে যে ফিল্টারটি ডিলিট করতে চান তার কিওয়ার্ডটি টাইপ করুন।**")
    
# মেসেজ হ্যান্ডলার (delete_filter এর সময়)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "delete_filter_awaiting_keyword"))
async def process_delete_filter_message(client, message):
    user_id = message.from_user.id
    keyword = message.text.lower().strip()
    if keyword in filters_dict:
        del filters_dict[keyword]
        save_data()
        await message.reply_text(f"✅ **Filter '{keyword}' deleted successfully.**")
    else:
        await message.reply_text("❌ **Filter not found.**")
    del user_states[user_id]
    save_data()

# /list_filters কমান্ড হ্যান্ডলার
@app.on_message(filters.command("list_filters") & filters.private)
async def list_filters_cmd(client, message):
    if filters_dict:
        response_text = "✅ **Available Filters:**\n\n" + "\n".join(f"`{k}`" for k in filters_dict.keys())
    else:
        response_text = "⚠️ **No filters found.**"
    await message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)

# /user_info কমান্ড হ্যান্ডলার
@app.on_message(filters.command("user_info") & filters.private & filters.user(ADMIN_ID))
async def user_info_cmd(client, message):
    await message.reply_text(f"✅ **Total users:** {len(user_list)}\n**Total banned users:** {len(banned_users)}")

# /add_ban কমান্ড হ্যান্ডলার
@app.on_message(filters.command("add_ban") & filters.private & filters.user(ADMIN_ID))
async def add_ban_cmd(client, message):
    user_id_to_ban = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if user_id_to_ban:
        banned_users.add(int(user_id_to_ban))
        save_data()
        await message.reply_text(f"✅ **User `{user_id_to_ban}` has been banned.**")
    else:
        await message.reply_text("❌ **Please provide a user ID to ban.**")
        
# /remove_ban কমান্ড হ্যান্ডলার
@app.on_message(filters.command("remove_ban") & filters.private & filters.user(ADMIN_ID))
async def remove_ban_cmd(client, message):
    user_id_to_unban = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if user_id_to_unban:
        if int(user_id_to_unban) in banned_users:
            banned_users.remove(int(user_id_to_unban))
            save_data()
            await message.reply_text(f"✅ **User `{user_id_to_unban}` has been unbanned.**")
        else:
            await message.reply_text("❌ **User is not banned.**")
    else:
        await message.reply_text("❌ **Please provide a user ID to unban.**")
        
# /set_autodelete_time কমান্ড হ্যান্ডলার
@app.on_message(filters.command("set_autodelete_time") & filters.private & filters.user(ADMIN_ID))
async def set_autodelete_time_cmd(client, message):
    try:
        minutes = int(message.text.split(maxsplit=1)[1])
        global autodelete_time
        autodelete_time = minutes * 60
        save_data()
        await message.reply_text(f"✅ **Autodelete time set to {minutes} minutes.**")
    except (IndexError, ValueError):
        await message.reply_text("❌ **Invalid format. Please use:** `/set_autodelete_time [minutes]`")

# /get_autodelete_time কমান্ড হ্যান্ডলার
@app.on_message(filters.command("get_autodelete_time") & filters.private & filters.user(ADMIN_ID))
async def get_autodelete_time_cmd(client, message):
    minutes = autodelete_time // 60
    await message.reply_text(f"✅ **Current autodelete time:** `{minutes}` minutes.")

# /add_button কমান্ড হ্যান্ডলার (edit এর জন্য)
@app.on_message(filters.command("add_button") & filters.private & filters.user(ADMIN_ID))
async def add_button_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "add_button_awaiting_keyword"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে ফিল্টারের কিওয়ার্ডটি টাইপ করুন।**")

# মেসেজ হ্যান্ডলার (add_button এর সময়)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "add_button_awaiting_keyword"))
async def process_add_button_keyword(client, message):
    user_id = message.from_user.id
    keyword = message.text.lower().strip()
    if keyword in filters_dict:
        user_states[user_id] = {"command": "add_button_awaiting_data", "keyword": keyword}
        save_data()
        await message.reply_text("➡️ **অনুগ্রহ করে নতুন বোতামের ডেটা পাঠান।**\n\n**উদাহরণ:** `button1 = link1, button2 = link2`")
    else:
        await message.reply_text("❌ **Filter not found.**")
        del user_states[user_id]
        save_data()

# মেসেজ হ্যান্ডলার (add_button_awaiting_data এর সময়)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "add_button_awaiting_data"))
async def process_add_button_data(client, message):
    user_id = message.from_user.id
    state = user_states[user_id]
    keyword = state["keyword"]
    try:
        new_buttons = parse_inline_buttons_from_text(message.text)
        filters_dict[keyword]['button_data'].extend(new_buttons)
        save_data()
        await message.reply_text("✅ **Buttons added successfully.**")
    except Exception as e:
        await message.reply_text(f"❌ **Error adding buttons:** `{e}`")
    finally:
        del user_states[user_id]
        save_data()

# /delete_button কমান্ড হ্যান্ডলার
@app.on_message(filters.command("delete_button") & filters.private & filters.user(ADMIN_ID))
async def delete_button_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "delete_button_awaiting_keyword"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে ফিল্টারের কিওয়ার্ডটি টাইপ করুন।**")
    
# মেসেজ হ্যান্ডলার (delete_button এর সময়)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "delete_button_awaiting_keyword"))
async def process_delete_button_keyword(client, message):
    user_id = message.from_user.id
    keyword = message.text.lower().strip()
    if keyword in filters_dict:
        user_states[user_id] = {"command": "delete_button_awaiting_data", "keyword": keyword}
        save_data()
        await message.reply_text("➡️ **অনুগ্রহ করে যে বোতামগুলি ডিলিট করতে চান তার নম্বর পাঠান।**\n\n**উদাহরণ:** `2, 4, 7-10`")
    else:
        await message.reply_text("❌ **Filter not found.**")
        del user_states[user_id]
        save_data()

# মেসেজ হ্যান্ডলার (delete_button_awaiting_data এর সময়)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "delete_button_awaiting_data"))
async def process_delete_button_data(client, message):
    user_id = message.from_user.id
    state = user_states[user_id]
    keyword = state["keyword"]
    
    try:
        button_list = filters_dict[keyword]['button_data']
        max_index = len(button_list)
        numbers_to_delete = parse_button_numbers(message.text, max_index)
        
        for i in sorted(numbers_to_delete, reverse=True):
            del button_list[i-1]
        
        filters_dict[keyword]['button_data'] = button_list
        save_data()
        await message.reply_text("✅ **Buttons deleted successfully.**")
        
    except ValueError as e:
        await message.reply_text(f"❌ **Error deleting buttons:** `{e}`")
    except Exception as e:
        await message.reply_text(f"❌ **An unexpected error occurred:** `{e}`")
    finally:
        del user_states[user_id]
        save_data()
        
# /swap_button কমান্ড হ্যান্ডলার
@app.on_message(filters.command("swap_button") & filters.private & filters.user(ADMIN_ID))
async def swap_button_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "swap_button_awaiting_keyword"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে ফিল্টারের কিওয়ার্ডটি টাইপ করুন।**")
    
# মেসেজ হ্যান্ডলার (swap_button এর সময়)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "swap_button_awaiting_keyword"))
async def process_swap_button_keyword(client, message):
    user_id = message.from_user.id
    keyword = message.text.lower().strip()
    if keyword in filters_dict:
        user_states[user_id] = {"command": "swap_button_awaiting_data", "keyword": keyword}
        save_data()
        await message.reply_text("➡️ **অনুগ্রহ করে যে বোতামগুলি অদলবদল করতে চান তার জোড়া পাঠান।**\n\n**উদাহরণ:** `1-5, 3-8`")
    else:
        await message.reply_text("❌ **Filter not found.**")
        del user_states[user_id]
        save_data()

# মেসেজ হ্যান্ডলার (swap_button_awaiting_data এর সময়)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "swap_button_awaiting_data"))
async def process_swap_button_data(client, message):
    user_id = message.from_user.id
    state = user_states[user_id]
    keyword = state["keyword"]
    
    try:
        button_list = filters_dict[keyword]['button_data']
        max_index = len(button_list)
        swap_pairs = parse_swap_pairs(message.text, max_index)
        
        for i, j in swap_pairs:
            button_list[i-1], button_list[j-1] = button_list[j-1], button_list[i-1]
        
        filters_dict[keyword]['button_data'] = button_list
        save_data()
        await message.reply_text("✅ **Buttons swapped successfully.**")
        
    except ValueError as e:
        await message.reply_text(f"❌ **Error swapping buttons:** `{e}`")
    except Exception as e:
        await message.reply_text(f"❌ **An unexpected error occurred:** `{e}`")
    finally:
        del user_states[user_id]
        save_data()
        
# **নতুন কোড শুরু**
# /merge_filters কমান্ড হ্যান্ডলার
@app.on_message(filters.command("merge_filters") & filters.private & filters.user(ADMIN_ID))
async def merge_filters_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "merge_filters_awaiting_keywords"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে যে ফিল্টারগুলো মার্জ করতে চান তাদের কিওয়ার্ডগুলো কমা দিয়ে আলাদা করে পাঠান।**\n\n**উদাহরণ:** `keyword1, keyword2, keyword3`")

# মেসেজ হ্যান্ডলার (merge_filters এর সময়)
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID) & filters.create(lambda _, __, m: m.from_user.id in user_states and user_states[m.from_user.id].get("command") == "merge_filters_awaiting_keywords"))
async def process_merge_filters_message(client, message):
    user_id = message.from_user.id
    keywords = [kw.strip().lower() for kw in message.text.split(',')]
    all_file_ids = []
    found_keywords = []
    not_found_keywords = []

    for keyword in keywords:
        if keyword in filters_dict and filters_dict[keyword].get('file_ids'):
            all_file_ids.extend(filters_dict[keyword]['file_ids'])
            found_keywords.append(keyword)
        else:
            not_found_keywords.append(keyword)
    
    if all_file_ids:
        await message.reply_text(f"✅ **Merging and sending files from:**\n" + "\n".join(f"`{k}`" for k in found_keywords), parse_mode=ParseMode.MARKDOWN)
        
        if not_found_keywords:
            await message.reply_text(f"⚠️ **The following filters were not found or contain no files:**\n" + "\n".join(f"`{k}`" for k in not_found_keywords), parse_mode=ParseMode.MARKDOWN)
        
        for file_id in all_file_ids:
            try:
                await app.copy_message(message.chat.id, CHANNEL_ID, file_id)
            except Exception as e:
                await message.reply_text(f"❌ **Error copying file {file_id}:** `{e}`")
    else:
        await message.reply_text("❌ **No files found in any of the provided filters.**")

    del user_states[user_id]
    save_data()
# **নতুন কোড শেষ**

# কলব্যাক কোয়েরি হ্যান্ডলার (সাধারণ পেজিনেশনের জন্য)
@app.on_callback_query(filters.regex(r"page_([a-zA-Z0-9\s-]+)_(\d+)"))
async def pagination_callback(client, query: CallbackQuery):
    keyword = query.matches[0].group(1)
    page = int(query.matches[0].group(2))
    
    if keyword in filters_dict:
        filter_data = filters_dict[keyword]
        if filter_data.get('button_data'):
            reply_markup = create_paged_buttons(keyword, filter_data['button_data'], page)
            try:
                await query.edit_message_reply_markup(reply_markup)
            except MessageNotModified:
                await query.answer("This page is already displayed.")
            except FloodWait as e:
                await query.answer(f"Flood wait: {e.value} seconds")
                await asyncio.sleep(e.value)
            await query.answer()
        else:
            await query.answer("No buttons found for this filter.")
    else:
        await query.answer("Filter not found.")

# কলব্যাক কোয়েরি হ্যান্ডলার (এডিট পেজিনেশনের জন্য)
@app.on_callback_query(filters.regex(r"editpage_([a-f0-9]+)_(\d+)"))
async def edit_pagination_callback(client, query: CallbackQuery):
    short_id = query.matches[0].group(1)
    page = int(query.matches[0].group(2))
    
    keyword = None
    for k, v in filters_dict.items():
        if get_short_id(k) == short_id:
            keyword = k
            break
            
    if keyword and 'button_data' in filters_dict[keyword]:
        button_list = filters_dict[keyword]['button_data']
        reply_markup = create_paged_edit_buttons(keyword, button_list, page)
        try:
            await query.edit_message_reply_markup(reply_markup)
        except MessageNotModified:
            await query.answer("This page is already displayed.")
        except FloodWait as e:
            await query.answer(f"Flood wait: {e.value} seconds")
            await asyncio.sleep(e.value)
        await query.answer()
    else:
        await query.answer("Filter not found.")

# কলব্যাক কোয়েরি হ্যান্ডলার (এডিট বোতামের জন্য)
@app.on_callback_query(filters.regex(r"edit_([a-z]+)_([a-f0-9]+)"))
async def edit_button_callback(client, query: CallbackQuery):
    action = query.matches[0].group(1)
    short_id = query.matches[0].group(2)
    user_id = query.from_user.id
    
    keyword = None
    for k, v in filters_dict.items():
        if get_short_id(k) == short_id:
            keyword = k
            break

    if not keyword:
        await query.answer("Filter not found.")
        return

    if action == "add":
        user_states[user_id] = {"command": "add_button_awaiting_data", "keyword": keyword}
        save_data()
        await query.message.reply_text("➡️ **অনুগ্রহ করে নতুন বোতামের ডেটা পাঠান।**\n\n**উদাহরণ:** `button1 = link1, button2 = link2`")
    elif action == "delete":
        user_states[user_id] = {"command": "delete_button_awaiting_data", "keyword": keyword}
        save_data()
        await query.message.reply_text("➡️ **অনুগ্রহ করে যে বোতামগুলি ডিলিট করতে চান তার নম্বর পাঠান।**\n\n**উদাহরণ:** `2, 4, 7-10`")
    elif action == "set":
        user_states[user_id] = {"command": "set_button_awaiting_data", "keyword": keyword}
        save_data()
        await query.message.reply_text("➡️ **অনুগ্রহ করে নতুন বোতামের সম্পূর্ণ তালিকা পাঠান।**\n\n**উদাহরণ:** `button1 = link1, button2 = link2`")
        
    await query.answer()

# কলব্যাক কোয়েরি হ্যান্ডলার (অন্যান্য সাধারণ বোতামের জন্য)
@app.on_callback_query(filters.regex("list_filters"))
async def list_filters_callback(client, query):
    if filters_dict:
        response_text = "✅ **Available Filters:**\n\n" + "\n".join(f"`{k}`" for k in filters_dict.keys())
    else:
        response_text = "⚠️ **No filters found.**"
    try:
        await query.edit_message_text(response_text, parse_mode=ParseMode.MARKDOWN)
    except MessageNotModified:
        await query.answer("This page is already displayed.")
    except Exception as e:
        await query.answer(f"Error: {e}")

# --- Run Services ---
def run_flask_and_pyrogram():
    connect_to_mongodb()
    load_data()
    flask_thread = threading.Thread(target=lambda: app_flask.run(host='0.0.0.0', port=PORT))
    flask_thread.start()

    if RENDER_EXTERNAL_HOSTNAME:
        ping_thread = threading.Thread(target=ping_service)
        ping_thread.start()

    print("Pyrogram client is running...")
    app.run()

if __name__ == "__main__":
    run_flask_and_pyrogram()
