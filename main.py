import os
import asyncio
import time
import threading
import re
import hashlib
from pyrogram import Client, filters
from pyrogram.enums import ParseMode, ChatType
from pyrogram.errors import MessageNotModified, FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from dotenv import load_dotenv
from flask import Flask, render_template_string
import requests

# --- Load Environment Variables ---
# .env ফাইল থেকে পরিবেশ ভেরিয়েবল লোড করে।
load_dotenv()

# --- Bot Configuration ---
# বটের জন্য প্রয়োজনীয় API আইডি, হ্যাশ, টোকেন ইত্যাদি।
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
PORT = int(os.environ.get("PORT"))

CHANNEL_ID = -1003094281207 # আপনার ফাইল স্টোর করার চ্যানেল আইডি
LOG_CHANNEL_ID = -1002623880704 # লগ মেসেজ পাঠানোর চ্যানেল আইডি

# --- MongoDB Configuration ---
# MongoDB ডেটাবেসের সাথে সংযোগ স্থাপনের জন্য
MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = "TA_HD_Anime"
COLLECTION_NAME = "bot_data"

# --- In-memory data structures ---
# বটের বর্তমান অবস্থা সংরক্ষণ করার জন্য ডিকশনারি
filters_dict = {}
user_list = set()
last_filter = None
banned_users = set()
restrict_status = False
autodelete_time = 0
user_states = {}
start_message_data = {} # New: Stores the custom start message and buttons
global_files = {'up': [], 'down': []} # New: Global files for all filters
temp_files = {} # Transitory dictionary for storing forwarded messages
saved_send_channels = [] # New: Stores channels added via /add_channel
admin_powers = {'filter_message': True, 'auto_delete': True, 'admin_restrict': False} # New: Admin powers state

# --- Join Channels Configuration ---
# ব্যবহারকারীদের বাধ্যতামূলকভাবে জয়েন করতে হবে এমন চ্যানেল
CHANNEL_ID_2 = -1003049936443
CHANNEL_LINK = "https://t.me/TA_HD_Anime"
CHANNEL_ID_3 = -1002345422475
CHANNEL_LINK_2 = "https://t.me/TA_Videos_Hot_Videos"
CHANNEL_ID_4 = -1002518558782
CHANNEL_LINK_3 = "https://t.me/+WxpHFf_PExY1NzQ1"

join_channels = [
    {"id": CHANNEL_ID_2, "name": "TA HD Anime Hindi Official Dubbed", "link": CHANNEL_LINK},
    {"id": CHANNEL_ID_3, "name": "TA Xvideos", "link": CHANNEL_LINK_2},
    {"id": CHANNEL_ID_4, "name": "TA Anime", "link": CHANNEL_LINK_3}
]

# --- Database Client and Collection ---
mongo_client = None
db = None
collection = None

# --- Flask Web Server ---
# @বটকে সচল রাখার জন্য একটি ছোট ওয়েব সার্ভার
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    html_content = """
    <!DOCTYPE html>
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

# Render সার্ভারকে সচল রাখার জন্য একটি পিং পরিষেবা
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
# MongoDB-র সাথে সংযোগ স্থাপন
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

# ডেটাবেসে ডেটা সংরক্ষণ
def save_data():
    global filters_dict, user_list, last_filter, banned_users, restrict_status, autodelete_time, user_states, start_message_data, global_files, saved_send_channels, admin_powers
    str_user_states = {str(uid): state for uid, state in user_states.items()}
    data = {
        "filters_dict": filters_dict,
        "user_list": list(user_list),
        "last_filter": last_filter,
        "banned_users": list(banned_users),
        "restrict_status": restrict_status,
        "autodelete_time": autodelete_time,
        "user_states": str_user_states,
        "start_message_data": start_message_data, # New: Save start message data
        "global_files": global_files, # New: Save global files
        "saved_send_channels": saved_send_channels, # New: Save send channels
        "admin_powers": admin_powers # New: Save admin powers
    }
    collection.update_one({"_id": "bot_data"}, {"$set": data}, upsert=True)
    print("Data saved successfully to MongoDB.")

# ডেটাবেস থেকে ডেটা লোড
def load_data():
    global filters_dict, user_list, last_filter, banned_users, restrict_status, autodelete_time, user_states, start_message_data, global_files, saved_send_channels, admin_powers
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
        start_message_data = data.get("start_message_data", {}) # New: Load start message data
        global_files = data.get("global_files", {'up': [], 'down': []}) # Load global files
        saved_send_channels = data.get("saved_send_channels", []) # Load saved send channels
        admin_powers = data.get("admin_powers", {'filter_message': True, 'auto_delete': True, 'admin_restrict': False}) # Load admin powers
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
# Admin Power Keyboard Helper
def get_admin_power_keyboard():
    fm_status = "✅ ON" if admin_powers.get('filter_message', True) else "❌ OFF"
    ad_status = "✅ ON" if admin_powers.get('auto_delete', True) else "❌ OFF"
    ar_status = "✅ ON" if admin_powers.get('admin_restrict', False) else "❌ OFF"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Filter Message: {fm_status}", callback_data="ap_toggle_filter_msg")],
        [InlineKeyboardButton(f"Auto Delete: {ad_status}", callback_data="ap_toggle_auto_del")],
        [InlineKeyboardButton(f"Admin Restrict: {ar_status}", callback_data="ap_toggle_restrict")]
    ])
    return keyboard

# একটি সংক্ষিপ্ত হ্যাশ আইডি তৈরি করা
def get_short_id(keyword):
    return hashlib.sha256(keyword.encode('utf-8')).hexdigest()[:8]

# ব্যবহারকারী চ্যানেলের সদস্য কিনা তা পরীক্ষা করা
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

# নির্দিষ্ট সময় পর মেসেজ ডিলিট করা
async def delete_messages_later(chat_id, message_ids, delay_seconds):
    await asyncio.sleep(delay_seconds)
    try:
        await app.delete_messages(chat_id, message_ids)
        print(f"Successfully deleted messages {message_ids} in chat {chat_id}.")
    except Exception as e:
        print(f"Error deleting messages {message_ids} in chat {chat_id}: {e}")

# পেজিনেশন সহ বোতাম তৈরি করা (পরিবর্তিত - এখন লিস্ট মেসেজ এবং নেভিগেশন রিটার্ন করে)
def create_paged_buttons(keyword, button_list, page, page_size=10):
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    current_page_buttons = button_list[start_index:end_index]
    
    text = ""
    for i, button_data in enumerate(current_page_buttons, start=start_index + 1):
        if 'link' in button_data and button_data['link']:
            text += f"**{i}.** [**{button_data['text']}**]({button_data['link']})\n"
        else:
            text += f"**{i}.** **{button_data['text']}**\n"
        if i < end_index and i < len(button_list):
            text += "<----->\n"
    
    keyboard = []
    total_pages = max(1, (len(button_list) + page_size - 1) // page_size)
    nav_row = []
    
    if page > 1:
        nav_row.append(InlineKeyboardButton("⏪ Previous", callback_data=f"page_{keyword}_{page - 1}"))
    
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="ignore"))
    
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ⏩", callback_data=f"page_{keyword}_{page + 1}"))
    
    if len(nav_row) > 1 or total_pages == 1:
        keyboard.append(nav_row)

    return text, InlineKeyboardMarkup(keyboard)

# টেক্সট থেকে ইনলাইন বোতামের ডেটা পার্স করা (নতুন লজিক এবং Validation সহ)
def parse_inline_buttons_from_text(text):
    button_data = []
    button_pairs = text.split(',')
    url_pattern = re.compile(r'^(https?://|t\.me/|tg://|www\.)', re.IGNORECASE)
    
    for pair in button_pairs:
        pair = pair.strip()
        # Check for the new [Button Name] format
        if pair.startswith('[') and pair.endswith(']'):
            button_text = pair[1:-1].strip()
            button_data.append({'text': f"🎬 {button_text} 🎬", 'link': None})
        else:
            parts = pair.split(' = ', 1)
            if len(parts) == 2:
                button_text = parts[0].strip()
                button_link = parts[1].strip()
                # URL Validation Check
                if not url_pattern.match(button_link):
                    return None
                button_data.append({'text': button_text, 'link': button_link})
            else:
                return None
            
    return button_data

# Start message buttons parser (New)
def parse_start_message_buttons_from_text(text):
    button_rows = []
    # Split by ,, for vertical buttons
    rows = text.split(',,')
    for row in rows:
        button_row = []
        # Split by , for horizontal buttons
        buttons = row.split(',')
        for button_pair in buttons:
            button_pair = button_pair.strip()
            if not button_pair:
                continue
            parts = button_pair.split(' = ', 1)
            if len(parts) == 2:
                button_text = parts[0].strip()
                button_link = parts[1].strip()
                button_row.append(InlineKeyboardButton(button_text, url=button_link))
        if button_row:
            button_rows.append(button_row)
    return InlineKeyboardMarkup(button_rows)

# Create buttons with pagination for editing (NEW - এখন লিস্ট মেসেজ এবং নেভিগেশন রিটার্ন করে)
def create_paged_edit_buttons(keyword, button_list, page, page_size=10):
    short_id = get_short_id(keyword)
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    current_page_buttons = button_list[start_index:end_index]
    
    text = ""
    for i, button_data in enumerate(current_page_buttons, start=start_index + 1):
        text += f"**#{i}** {button_data['text']}\n"
    
    keyboard = []
    total_pages = max(1, (len(button_list) + page_size - 1) // page_size)
    nav_row = []
    
    if page > 1:
        nav_row.append(InlineKeyboardButton("⏪ Previous", callback_data=f"editpage_{short_id}_{page - 1}"))
    
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="ignore"))
    
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ⏩", callback_data=f"editpage_{short_id}_{page + 1}"))
    
    if len(nav_row) > 1 or total_pages == 1:
        keyboard.append(nav_row)

    edit_row = [
        InlineKeyboardButton("➕ Add", callback_data=f"edit_add_{short_id}"),
        InlineKeyboardButton("🗑️ Delete", callback_data=f"edit_delete_{short_id}"),
        InlineKeyboardButton("🔄 Set", callback_data=f"edit_set_{short_id}")
    ]
    keyboard.append(edit_row)
    
    return text, InlineKeyboardMarkup(keyboard)

# Create pagination for standard file editing (NEW - এখন লিস্ট মেসেজ এবং নেভিগেশন রিটার্ন করে)
def create_paged_file_edit_buttons(keyword, file_list, page, page_size=30):
    short_id = get_short_id(keyword)
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    current_page_files = file_list[start_index:end_index]
    
    text = ""
    for i, file_id in enumerate(current_page_files, start=start_index + 1):
        text += f"**#{i}** (ID: {file_id})\n"
    
    keyboard = []
    total_pages = max(1, (len(file_list) + page_size - 1) // page_size)
    nav_row = []
    
    if page > 1:
        nav_row.append(InlineKeyboardButton("⏪ Previous", callback_data=f"editfilepage_{short_id}_{page - 1}"))
    
    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="ignore"))
    
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ⏩", callback_data=f"editfilepage_{short_id}_{page + 1}"))
    
    if len(nav_row) > 1 or total_pages == 1:
        keyboard.append(nav_row)

    edit_row = [
        InlineKeyboardButton("➕ Add", callback_data=f"editfile_add_{short_id}"),
        InlineKeyboardButton("🗑️ Delete", callback_data=f"editfile_delete_{short_id}"),
        InlineKeyboardButton("🔄 Set", callback_data=f"editfile_set_{short_id}")
    ]
    keyboard.append(edit_row)
    
    return text, InlineKeyboardMarkup(keyboard)

# Parse button numbers from a string (e.g., '2, 4, 5, 7-10') (NEW)
def parse_button_numbers(text, max_index):
    numbers = set()
    parts = re.split(r',\s*', text)
    for part in parts:
        if '-' in part:
            start, end = map(int, part.split('-'))
            numbers.update(range(start, end + 1))
        else:
            numbers.add(int(part))
    
    # Validate indices
    for num in numbers:
        if not (1 <= num <= max_index):
            raise ValueError(f"Number {num} is out of range.")
            
    return sorted(list(numbers))

# Parse swap pairs from a string (e.g., '1-5, 3-8, 6u-4') (MODIFIED - Now returns sequential actions)
def parse_swap_pairs(text, max_index):
    actions = []
    parts = re.split(r',\s*', text)
    for part in parts:
        part = part.strip()
        if '-' in part:
            if 'u' in part.lower():
                try:
                    i_str, j_str = part.lower().split('u-')
                    i, j = int(i_str), int(j_str)
                    if not (1 <= i <= max_index and 1 <= j <= max_index):
                        raise ValueError(f"Invalid move numbers {i} or {j}.")
                    actions.append(('move', i, j))
                except (ValueError, IndexError):
                    raise ValueError("Invalid single move format. Use `iu-j`.")
            else:
                try:
                    i, j = map(int, part.split('-'))
                    if not (1 <= i <= max_index and 1 <= j <= max_index):
                        raise ValueError(f"Invalid swap numbers {i} or {j}.")
                    actions.append(('swap', i, j))
                except (ValueError, IndexError):
                    raise ValueError("Invalid swap format. Use `i-j`.")
        else:
            raise ValueError("Invalid pair format. Use `i-j` or `iu-j`.")
    return actions

# --- Message Handlers (Pyrogram) ---
# /start কমান্ড হ্যান্ডলার (পরিবর্তিত)
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
            list_text, reply_markup = create_paged_buttons(deep_link_keyword, filter_data['button_data'], 1)
            await message.reply_text(f"{reply_text}\n\n{list_text}", reply_markup=reply_markup, disable_web_page_preview=True)
        
        elif 'file_ids' in filter_data and filter_data['file_ids']:
            is_admin = (user_id == ADMIN_ID)
            show_filter_msg = not (is_admin and not admin_powers.get('filter_message', True))
            apply_auto_del = autodelete_time > 0 and not (is_admin and not admin_powers.get('auto_delete', True))
            apply_restrict = restrict_status and not (is_admin and not admin_powers.get('admin_restrict', False))

            if show_filter_msg:
                if apply_auto_del:
                    minutes = autodelete_time // 60
                    hours = autodelete_time // 3600
                    if hours > 0:
                        delete_time_str = f"{hours} hour{'s' if hours > 1 else ''}"
                    else:
                        delete_time_str = f"{minutes} minute{'s' if minutes > 1 else ''}"
                    await message.reply_text(f"✅ **Files found!** Sending now. Please note, these files will be automatically deleted in **{delete_time_str}**.", parse_mode=ParseMode.MARKDOWN)
                else:
                    await message.reply_text(f"✅ **Files found!** Sending now...")
            
            # Combine global up files, filter files, and global down files
            file_ids_to_send = []
            if show_filter_msg:
                if 'up' in global_files and global_files['up']:
                    file_ids_to_send.extend(global_files['up'])
            
            file_ids_to_send.extend(filter_data['file_ids'])
            
            if show_filter_msg:
                if 'down' in global_files and global_files['down']:
                    file_ids_to_send.extend(global_files['down'])

            sent_message_ids = []
            for file_id in file_ids_to_send:
                try:
                    sent_msg = await app.copy_message(message.chat.id, CHANNEL_ID, file_id, protect_content=apply_restrict)
                    sent_message_ids.append(sent_msg.id)
                    await asyncio.sleep(0)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    sent_msg = await app.copy_message(message.chat.id, CHANNEL_ID, file_id, protect_content=apply_restrict)
                    sent_message_ids.append(sent_msg.id)
                except Exception as e:
                    print(f"Error copying message {file_id}: {e}")
            
            if show_filter_msg:
                await message.reply_text("🎉 **All files sent!**")
                
            if apply_auto_del:
                asyncio.create_task(delete_messages_later(message.chat.id, sent_message_ids, autodelete_time))
        else:
            await message.reply_text("❌ **No files or buttons found for this keyword.**")
        
        return
    
    if user_id == ADMIN_ID:
        admin_commands = (
            "🌟 **Welcome, Admin! Here are your commands:**\n\n"
            "**/button** - Start the interactive process to create a button filter.\n"
            "**/editbutton** - Edit an existing button filter.\n"
            "**/filter_data** - Get the raw button data for a button filter.\n"
            "**/change_filter_name** - Change the name of a saved filter.\n"
            "**/merge_filter** - Merge multiple file filters into one.\n"
            "**/edit_filter** - Edit standard file filters (Add/Delete/Set).\n"
            "**/global_files** - Manage Global Up/Down files for all filters.\n"
            "**/start_message** - Manage the custom start message.\n"
            "**/admin_power** - Manage Admin privileges and bypasses.\n"
            "**/broadcast** - Reply to a message with this command to broadcast it.\n"
            "**/delete <keyword>** - Delete a filter and its associated files.\n"
            "**/restrict** - Toggle message forwarding restriction (ON/OFF).\n"
            "**/ban <user_id>** - Ban a user.\n"
            "**/unban <user_id>** - Unban a user.\n"
            "**/auto_delete <time>** - Set auto-delete time for files (e.g., 30m, 1h, off).\n"
            "**/channel_id** - Get the ID of a channel or user.\n"
            "**/add_channel** - Manage multiple channels to forward files to.\n"
            "**/send <filter_name>** - Send all files of a filter to saved channels."
        )
        await message.reply_text(admin_commands, parse_mode=ParseMode.MARKDOWN)
    else:
        # User is not an admin and no deep link was provided
        if start_message_data:
            # Send custom start message with buttons
            try:
                text = start_message_data['text']
                buttons = parse_start_message_buttons_from_text(start_message_data['buttons'])
                await message.reply_text(text, reply_markup=buttons, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                print(f"Error sending custom start message: {e}")
                await message.reply_text("👋 **Welcome!** You can access files via special links.")
        else:
            await message.reply_text("👋 **Welcome!** You can access files via special links.")

# /admin_power কমান্ড হ্যান্ডলার (New)
@app.on_message(filters.command("admin_power") & filters.private & filters.user(ADMIN_ID))
async def admin_power_cmd(client, message):
    keyboard = get_admin_power_keyboard()
    await message.reply_text("⚙️ **Admin Power Settings:**", reply_markup=keyboard)

# /add_channel কমান্ড হ্যান্ডলার (New)
@app.on_message(filters.command("add_channel") & filters.private & filters.user(ADMIN_ID))
async def add_channel_cmd(client, message):
    keyboard = []
    for chan in saved_send_channels:
        keyboard.append([
            InlineKeyboardButton(chan['name'], callback_data="ignore"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"del_schannel_{chan['id']}")
        ])
    keyboard.append([InlineKeyboardButton("➕ Add Channel", callback_data="add_schannel")])
    await message.reply_text("➡️ **Manage Channels for /send command:**", reply_markup=InlineKeyboardMarkup(keyboard))

# /send কমান্ড হ্যান্ডলার (New)
@app.on_message(filters.command("send") & filters.private & filters.user(ADMIN_ID))
async def send_cmd(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **Usage:** `/send <filter_name>`")
    
    keyword = args[1].lower().strip()
    
    if keyword == "letter":
        if not saved_send_channels:
            return await message.reply_text("❌ **No channels have been added yet. Use /add_channel first.**")
            
        user_states[message.from_user.id] = {"command": "sending_filter_letters", "channels": ["all"]}
        save_data()
        
        keyboard = []
        for chan in saved_send_channels:
            keyboard.append([
                InlineKeyboardButton(chan['name'], callback_data=f"send_letchan_{chan['id']}"),
                InlineKeyboardButton("✅ Select", callback_data=f"send_letchan_{chan['id']}")
            ])
        keyboard.append([InlineKeyboardButton("📢 All Channel Send", callback_data="send_letchan_all")])
        return await message.reply_text("➡️ **Select channel(s) to send A-Z letter messages:**", reply_markup=InlineKeyboardMarkup(keyboard))

    if keyword not in filters_dict or not filters_dict[keyword].get('file_ids'):
        return await message.reply_text("❌ **Filter not found or it has no files.**")
    
    if not saved_send_channels:
        return await message.reply_text("❌ **No channels have been added yet. Use /add_channel first.**")
        
    user_states[message.from_user.id] = {"command": "sending_filter", "keyword": keyword}
    save_data()
    
    keyboard = []
    for chan in saved_send_channels:
        keyboard.append([
            InlineKeyboardButton(chan['name'], callback_data=f"send_chan_{chan['id']}"),
            InlineKeyboardButton("✅ Select", callback_data=f"send_chan_{chan['id']}")
        ])
    keyboard.append([InlineKeyboardButton("📢 All Channel Send", callback_data="send_chan_all")])
    
    await message.reply_text(f"➡️ **Select channel(s) to send filter '{keyword}':**", reply_markup=InlineKeyboardMarkup(keyboard))

# /button কমান্ড হ্যান্ডলার (New)
@app.on_message(filters.command("button") & filters.private & filters.user(ADMIN_ID))
async def button_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "button_awaiting_name"}
    save_data()
    await message.reply_text("➡️ **ফিল্টারের জন্য একটি নাম দিন:**")

# /edit_filter কমান্ড হ্যান্ডলার (NEW)
@app.on_message(filters.command("edit_filter") & filters.private & filters.user(ADMIN_ID))
async def edit_filter_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "edit_file_awaiting_name"}
    save_data()
    await message.reply_text("➡️ **Please provide the name of the file filter you want to edit:**")

# /global_files কমান্ড হ্যান্ডলার (NEW)
@app.on_message(filters.command("global_files") & filters.private & filters.user(ADMIN_ID))
async def global_files_cmd(client, message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬆️ Up Files", callback_data="gf_action_up"),
         InlineKeyboardButton("⬇️ Down Files", callback_data="gf_action_down")],
        [InlineKeyboardButton("🗑️ Delete Up", callback_data="gf_del_up"),
         InlineKeyboardButton("🗑️ Delete Down", callback_data="gf_del_down")]
    ])
    await message.reply_text("➡️ **Manage Global Files (Files sent above or below all filters):**", reply_markup=keyboard)

# /editbutton কমান্ড হ্যান্ডলার (NEW)
@app.on_message(filters.command("editbutton") & filters.private & filters.user(ADMIN_ID))
async def edit_button_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "edit_awaiting_name"}
    save_data()
    await message.reply_text("➡️ **Please provide the name of the button filter you want to edit.**")

# /change_filter_name কমান্ড হ্যান্ডলার (NEW)
@app.on_message(filters.command("change_filter_name") & filters.private & filters.user(ADMIN_ID))
async def change_filter_name_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "change_name_awaiting_old_name"}
    save_data()
    await message.reply_text("➡️ **Please provide the current name of the filter you want to change.**")

# /merge_filter কমান্ড হ্যান্ডলার
@app.on_message(filters.command("merge_filter") & filters.private & filters.user(ADMIN_ID))
async def merge_filter_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "merge_awaiting_target_name"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে নতুন মার্জ করা ফিল্টারের জন্য একটি নাম দিন:**")

# /filter_data কমান্ড হ্যান্ডলার (NEW)
@app.on_message(filters.command("filter_data") & filters.private & filters.user(ADMIN_ID))
async def filter_data_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "filter_data_awaiting_name"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে যে বোতাম ফিল্টারের ডেটা চান তার নাম দিন:**")

# /start_message কমান্ড হ্যান্ডলার (New)
@app.on_message(filters.command("start_message") & filters.private & filters.user(ADMIN_ID))
async def start_message_cmd(client, message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Start Message", callback_data="add_start_message")],
        [InlineKeyboardButton("👀 View Start Message", callback_data="view_start_message")]
    ])
    await message.reply_text(
        "➡️ **Here you can manage the bot's custom start message.**",
        reply_markup=keyboard
    )

# /channel_id কমান্ড হ্যান্ডলার (Updated)
@app.on_message(filters.command("channel_id") & filters.private & filters.user(ADMIN_ID))
async def channel_id_cmd(client, message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Channel and Group ID", callback_data="cid_channel")],
        [InlineKeyboardButton("File ID", callback_data="cid_file")],
        [InlineKeyboardButton("Owner ID", callback_data="cid_owner")]
    ])
    await message.reply_text("➡️ **Select an option to get the ID:**", reply_markup=keyboard)


# সাধারণ মেসেজ এবং মিডিয়া হ্যান্ডলার (নতুন লজিক সহ)
@app.on_message(filters.private & filters.user(ADMIN_ID) & ~filters.command(["start", "button", "broadcast", "delete", "restrict", "ban", "unban", "auto_delete", "channel_id", "editbutton", "change_filter_name", "merge_filter", "filter_data", "start_message", "global_files", "edit_filter", "add_channel", "send", "admin_power"]))
async def message_handler(client, message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    
    if not state:
        return
        
    text_only_states = ["button_awaiting_name", "button_awaiting_buttons", "edit_awaiting_name", "edit_add_buttons", "edit_delete_buttons", "edit_set_buttons", "change_name_awaiting_old_name", "change_name_awaiting_new_name", "merge_awaiting_target_name", "merge_awaiting_source_names", "filter_data_awaiting_name", "awaiting_start_message_text", "awaiting_start_message_buttons", "edit_file_awaiting_name", "edit_file_delete", "edit_file_set"]
    
    if state["command"] in text_only_states:
        if not message.text:
            return await message.reply_text("❌ **অনুগ্রহ করে টেক্সট মেসেজ দিন।**")
    
    if state["command"] == "button_awaiting_name":
        keyword = message.text.lower().strip()
        if len(keyword) == 1 and keyword.isalpha():
            keyword = f"start_from_letter_{keyword}"
        if keyword in filters_dict:
            return await message.reply_text("⚠️ **এই নামে একটি ফিল্টার ইতিমধ্যে আছে।** অনুগ্রহ করে অন্য একটি নাম দিন:")

        user_states[user_id] = {"command": "button_awaiting_buttons", "keyword": keyword}
        save_data()
        await message.reply_text("➡️ **বোতামের কোড দিন (যেমন: Button 01 = link1, Button 02 = link2, [Button Name]):**")

    elif state["command"] == "button_awaiting_buttons":
        keyword = state["keyword"]
        button_text = message.text.strip()
        button_data = parse_inline_buttons_from_text(button_text)
        
        if button_data is None:
            return await message.reply_text("❌ **ভুল বোতাম ফরম্যাট বা অবৈধ লিংক।** অনুগ্রহ করে সঠিক URL দিন (http/https/t.me/www):")

        filters_dict[keyword] = {
            'message_text': "Select a button from the list below:",
            'button_data': button_data,
            'file_ids': [],
            'type': 'button_filter'
        }

        try:
            sent_msg = await app.send_message(
                CHANNEL_ID,
                f"#{keyword}\n[button (বোতাম ফিল্টার)]"
            )
            await app.pin_chat_message(CHANNEL_ID, sent_msg.id) # Auto pin button filters
        except Exception as e:
            await message.reply_text(f"❌ **চ্যানেলে সেভ করতে সমস্যা হয়েছে:** {e}")

        await message.reply_text(
            f"✅ **বোতাম ফিল্টার '{keyword}' সফলভাবে তৈরি হয়েছে।**\n🔗 শেয়ার লিংক: `https://t.me/{(await client.get_me()).username}?start={keyword}`",
            parse_mode=ParseMode.MARKDOWN
        )

        del user_states[user_id]
        save_data()
        
    elif state["command"] == "edit_awaiting_name":
        keyword = message.text.lower().strip()
        if len(keyword) == 1 and keyword.isalpha():
            keyword = f"start_from_letter_{keyword}"
        if keyword not in filters_dict or filters_dict[keyword].get('type') != 'button_filter':
            return await message.reply_text("❌ **Filter not found or it is not a button filter.** Please provide a valid button filter name:")
        
        user_states[user_id] = {"command": "edit_button_menu", "keyword": keyword, "page": 1}
        save_data()
        
        filter_data = filters_dict[keyword]
        list_text, keyboard = create_paged_edit_buttons(keyword, filter_data['button_data'], 1)
        await message.reply_text(f"✅ **You are now editing the buttons for this filter.**\n\n**Select an option below:**\n\n{list_text}", reply_markup=keyboard, disable_web_page_preview=True)

    elif state["command"] == "edit_add_buttons":
        # Handle adding new buttons
        keyword = state.get("keyword")
        if not keyword or keyword not in filters_dict:
            return await message.reply_text("❌ **Filter not found.** Please start the process again with /editbutton.")
            
        button_text = message.text.strip()
        new_buttons = parse_inline_buttons_from_text(button_text)
        
        if new_buttons is None:
            return await message.reply_text("❌ **ভুল বোতাম ফরম্যাট বা অবৈধ লিংক।** অনুগ্রহ করে সঠিক URL দিন:")
            
        filters_dict[keyword]['button_data'].extend(new_buttons)
        save_data()
        
        # Reset state and show the updated menu
        user_states[user_id] = {"command": "edit_button_menu", "keyword": keyword, "page": 1}
        save_data()
        
        filter_data = filters_dict[keyword]
        list_text, keyboard = create_paged_edit_buttons(keyword, filter_data['button_data'], 1)
        await message.reply_text(f"✅ **Buttons have been added.**\n\n**Select an option below:**\n\n{list_text}", reply_markup=keyboard, disable_web_page_preview=True)

    elif state["command"] == "edit_delete_buttons":
        # Handle deleting buttons by number
        keyword = state.get("keyword")
        if not keyword or keyword not in filters_dict:
            return await message.reply_text("❌ **Filter not found.** Please start the process again with /editbutton.")
            
        input_text = message.text.strip()
        try:
            delete_indices = parse_button_numbers(input_text, len(filters_dict[keyword]['button_data']))
            filters_dict[keyword]['button_data'] = [
                button for i, button in enumerate(filters_dict[keyword]['button_data'])
                if i + 1 not in delete_indices
            ]
            save_data()
            
            user_states[user_id] = {"command": "edit_button_menu", "keyword": keyword, "page": 1}
            save_data()
            
            filter_data = filters_dict[keyword]
            list_text, keyboard = create_paged_edit_buttons(keyword, filter_data['button_data'], 1)
            await message.reply_text(f"🗑️ **Buttons have been deleted.**\n\n**Select an option below:**\n\n{list_text}", reply_markup=keyboard, disable_web_page_preview=True)
        except ValueError:
            await message.reply_text("❌ **Invalid format.** Please provide numbers separated by commas, or ranges like `7-10`.")

    elif state["command"] == "edit_set_buttons":
        # Handle setting/rearranging buttons
        keyword = state.get("keyword")
        if not keyword or keyword not in filters_dict:
            return await message.reply_text("❌ **Filter not found.** Please start the process again with /editbutton.")
            
        input_text = message.text.strip()
        try:
            button_list = filters_dict[keyword]['button_data']
            max_index = len(button_list)
            
            if input_text.lower().startswith("a "):
                idx_str = input_text[2:].strip()
                indices = []
                for x in idx_str.split(','):
                    x = x.strip()
                    if x.isdigit():
                        idx = int(x)
                        if 1 <= idx <= max_index:
                            indices.append(idx)
                        else:
                            raise ValueError(f"Index {idx} is out of range.")
                    else:
                        raise ValueError(f"Invalid index format: {x}")
                
                seen = set()
                ordered_indices = []
                for idx in indices:
                    if idx not in seen:
                        ordered_indices.append(idx)
                        seen.add(idx)
                
                new_list = []
                for idx in ordered_indices:
                    new_list.append(button_list[idx - 1])
                for idx in range(1, max_index + 1):
                    if idx not in seen:
                        new_list.append(button_list[idx - 1])
                        
                filters_dict[keyword]['button_data'] = new_list
            else:
                actions = parse_swap_pairs(input_text, max_index)
                for action in actions:
                    if action[0] == 'swap':
                        i, j = action[1], action[2]
                        button_list[i-1], button_list[j-1] = button_list[j-1], button_list[i-1]
                    elif action[0] == 'move':
                        i, j = action[1], action[2]
                        button_to_move = button_list.pop(i - 1)
                        button_list.insert(j - 1, button_to_move)
            
            save_data()
            user_states[user_id] = {"command": "edit_button_menu", "keyword": keyword, "page": 1}
            save_data()
            
            filter_data = filters_dict[keyword]
            list_text, keyboard = create_paged_edit_buttons(keyword, filter_data['button_data'], 1)
            await message.reply_text(f"🔄 **Buttons have been rearranged.**\n\n**Select an option below:**\n\n{list_text}", reply_markup=keyboard, disable_web_page_preview=True)
        except ValueError as e:
            await message.reply_text(f"❌ **Invalid format:** {e}")

    elif state["command"] == "edit_file_awaiting_name":
        keyword = message.text.lower().strip()
        if keyword not in filters_dict or filters_dict[keyword].get('type') == 'button_filter':
            return await message.reply_text("❌ **Filter not found or it is a button filter.** Please provide a valid file filter name:")
        
        user_states[user_id] = {"command": "edit_file_menu", "keyword": keyword, "page": 1}
        save_data()
        
        filter_data = filters_dict[keyword]
        list_text, keyboard = create_paged_file_edit_buttons(keyword, filter_data['file_ids'], 1)
        await message.reply_text(f"✅ **You are now editing the files for this filter.**\n\n**Select an option below:**\n\n{list_text}", reply_markup=keyboard)

    elif state["command"] == "edit_file_delete":
        keyword = state.get("keyword")
        if not keyword or keyword not in filters_dict:
            return await message.reply_text("❌ **Filter not found.** Please start the process again with /edit_filter.")
            
        input_text = message.text.strip()
        try:
            delete_indices = parse_button_numbers(input_text, len(filters_dict[keyword]['file_ids']))
            filters_dict[keyword]['file_ids'] = [
                fid for i, fid in enumerate(filters_dict[keyword]['file_ids'])
                if i + 1 not in delete_indices
            ]
            save_data()
            
            user_states[user_id] = {"command": "edit_file_menu", "keyword": keyword, "page": 1}
            save_data()
            
            filter_data = filters_dict[keyword]
            list_text, keyboard = create_paged_file_edit_buttons(keyword, filter_data['file_ids'], 1)
            await message.reply_text(f"🗑 hemisphere **Files have been deleted.**\n\n**Select an option below:**\n\n{list_text}", reply_markup=keyboard)
        except ValueError:
            await message.reply_text("❌ **Invalid format.** Please provide numbers separated by commas, or ranges like `1-5`.")

    elif state["command"] == "edit_file_set":
        keyword = state.get("keyword")
        if not keyword or keyword not in filters_dict:
            return await message.reply_text("❌ **Filter not found.** Please start the process again with /edit_filter.")
            
        input_text = message.text.strip()
        try:
            file_list = filters_dict[keyword]['file_ids']
            max_index = len(file_list)
            
            if input_text.lower().startswith("a "):
                idx_str = input_text[2:].strip()
                indices = []
                for x in idx_str.split(','):
                    x = x.strip()
                    if x.isdigit():
                        idx = int(x)
                        if 1 <= idx <= max_index:
                            indices.append(idx)
                        else:
                            raise ValueError(f"Index {idx} is out of range.")
                    else:
                        raise ValueError(f"Invalid index format: {x}")
                
                seen = set()
                ordered_indices = []
                for idx in indices:
                    if idx not in seen:
                        ordered_indices.append(idx)
                        seen.add(idx)
                
                new_list = []
                for idx in ordered_indices:
                    new_list.append(file_list[idx - 1])
                for idx in range(1, max_index + 1):
                    if idx not in seen:
                        new_list.append(file_list[idx - 1])
                        
                filters_dict[keyword]['file_ids'] = new_list
            else:
                actions = parse_swap_pairs(input_text, max_index)
                for action in actions:
                    if action[0] == 'swap':
                        i, j = action[1], action[2]
                        file_list[i-1], file_list[j-1] = file_list[j-1], file_list[i-1]
                    elif action[0] == 'move':
                        i, j = action[1], action[2]
                        file_to_move = file_list.pop(i - 1)
                        file_list.insert(j - 1, file_to_move)
            
            save_data()
            user_states[user_id] = {"command": "edit_file_menu", "keyword": keyword, "page": 1}
            save_data()
            
            filter_data = filters_dict[keyword]
            list_text, keyboard = create_paged_file_edit_buttons(keyword, filter_data['file_ids'], 1)
            await message.reply_text(f"🔄 **Files have been rearranged.**\n\n**Select an option below:**\n\n{list_text}", reply_markup=keyboard)
        except ValueError as e:
            await message.reply_text(f"❌ **Invalid format:** {e}")

    elif state["command"] == "change_name_awaiting_old_name":
        old_name = message.text.lower().strip()
        if old_name not in filters_dict:
            return await message.reply_text("❌ **Filter not found.** Please enter a valid existing filter name:")
            
        user_states[user_id] = {"command": "change_name_awaiting_new_name", "old_name": old_name}
        save_data()
        await message.reply_text(f"➡️ **Now provide the new name for the filter '{old_name}':**")

    elif state["command"] == "change_name_awaiting_new_name":
        old_name = state["old_name"]
        new_name = message.text.lower().strip()
        
        if new_name in filters_dict:
            return await message.reply_text("⚠️ **This name already exists.** Please choose another name:")
            
        # Move data to new key
        filters_dict[new_name] = filters_dict.pop(old_name)
        save_data()
        
        await message.reply_text(f"✅ **Filter successfully renamed from '{old_name}' to '{new_name}'.**")
        del user_states[user_id]
        save_data()

    elif state["command"] == "merge_awaiting_target_name":
        target_name = message.text.lower().strip()
        user_states[user_id] = {"command": "merge_awaiting_source_names", "target_name": target_name}
        save_data()
        await message.reply_text("➡️ **মার্জ করার জন্য সোর্স ফিল্টার নামগুলো দিন (যেমন: filter1, filter2, filter3):**")

    elif state["command"] == "merge_awaiting_source_names":
        target_name = state["target_name"]
        source_names = [name.strip().lower() for name in message.text.split(',')]
        
        valid_sources = []
        for name in source_names:
            if name in filters_dict:
                if filters_dict[name].get('type') == 'button_filter':
                    return await message.reply_text(f"❌ **'{name}' একটি বোতাম ফিল্টার।** মার্জ করার জন্য শুধু ফাইল ফিল্টার ব্যবহার করা যাবে। আবার দিন:")
                valid_sources.append(name)
            else:
                return await message.reply_text(f"❌ **ফিল্টার '{name}' খুঁজে পাওয়া যায়নি।** অনুগ্রহ করে সঠিক নামগুলো আবার দিন:")
        
        merged_file_ids = []
        if target_name in filters_dict:
            merged_file_ids.extend(filters_dict[target_name].get('file_ids', []))
            
        for name in valid_sources:
            merged_file_ids.extend(filters_dict[name].get('file_ids', []))
            
        # Remove duplicate file IDs while keeping order
        seen_files = set()
        unique_file_ids = []
        for fid in merged_file_ids:
            if fid not in seen_files:
                unique_file_ids.append(fid)
                seen_files.add(fid)
                
        filters_dict[target_name] = {
            'file_ids': unique_file_ids,
            'type': 'file_filter'
        }
        
        save_data()
        await message.reply_text(f"✅ **সফলভাবে ফাইলগুলো মার্জ করে '{target_name}' ফিল্টারে যুক্ত করা হয়েছে।** মোট ফাইল সংখ্যা: {len(unique_file_ids)}")
        del user_states[user_id]
        save_data()

    elif state["command"] == "filter_data_awaiting_name":
        keyword = message.text.lower().strip()
        if keyword not in filters_dict or filters_dict[keyword].get('type') != 'button_filter':
            return await message.reply_text("❌ **বোতাম ফিল্টারটি পাওয়া যায়নি।** সঠিক নাম আবার দিন:")
            
        button_data = filters_dict[keyword]['button_data']
        data_strings = []
        for btn in button_data:
            if btn.get('link') is None:
                # Format for headers [Name]
                # Extract clean name from 🎬 name 🎬
                clean_text = btn['text'].replace("🎬", "").strip()
                data_strings.append(f"[{clean_text}]")
            else:
                data_strings.append(f"{btn['text']} = {btn['link']}")
                
        raw_data_text = ", ".join(data_strings)
        await message.reply_text(f"📋 **Raw data for '{keyword}':**\n\n`{raw_data_text}`")
        del user_states[user_id]
        save_data()

    elif state["command"] == "awaiting_start_message_text":
        start_message_text = message.text.strip()
        user_states[user_id] = {"command": "awaiting_start_message_buttons", "start_text": start_message_text}
        save_data()
        await message.reply_text("➡️ **Now provide buttons for the start message (Format: Name = link, Name2 = link2 ,, Name3 = link3):**")

    elif state["command"] == "awaiting_start_message_buttons":
        start_text = state["start_text"]
        buttons_text = message.text.strip()
        
        # Test if it parses correctly
        try:
            parse_start_message_buttons_from_text(buttons_text)
        except Exception:
            return await message.reply_text("❌ **Invalid buttons format.** Please check your layout and try again:")
            
        start_message_data = {
            "text": start_text,
            "buttons": buttons_text
        }
        save_data()
        await message.reply_text("✅ **Custom start message has been saved successfully.**")
        del user_states[user_id]
        save_data()

    elif state["command"] == "awaiting_schannel_id":
        try:
            chan_id = int(message.text.strip())
            chat = await app.get_chat(chan_id)
            
            # Check for duplicates
            if any(c['id'] == chan_id for c in saved_send_channels):
                return await message.reply_text("⚠️ **This channel has already been added.**")
                
            saved_send_channels.append({"id": chan_id, "name": chat.title})
            save_data()
            await message.reply_text(f"✅ **Channel '{chat.title}' added successfully!**")
            del user_states[user_id]
            save_data()
        except Exception as e:
            await message.reply_text(f"❌ **Invalid Channel ID or Bot is not admin in that channel.** Error: {e}\n\nPlease try again:")

    elif state["command"] == "edit_file_add":
        # Interactive File adding state
        keyword = state.get("keyword")
        if not keyword or keyword not in filters_dict:
            return await message.reply_text("❌ **Filter session expired.**")
            
        # Standard file uploading triggers copy to channel logic
        # We don't interfere with the forward handler, but we instruct the user
        await message.reply_text("➡️ **Please forward or send files now. When done, type /done to exit adding files.**")
        # Change state command to accept forwarded messages
        user_states[user_id]["command"] = "edit_file_adding_active"
        save_data()

    # --- File/Forward Receiver Mode for Filter Creation ---
    if state["command"] == "awaiting_files" or state["command"] == "edit_file_adding_active":
        # Check if user typed /done to complete standard file add session
        if message.text and message.text.strip() == "/done":
            keyword = state["keyword"]
            del user_states[user_id]
            save_data()
            return await message.reply_text(f"✅ **File collection complete for filter '{keyword}'.** Session closed.")

        file_id = None
        if message.document:
            file_id = message.document.file_id
        elif message.video:
            file_id = message.video.file_id
        elif message.audio:
            file_id = message.audio.file_id
        elif message.photo:
            file_id = message.photo[-1].file_id
        
        if not file_id:
            # If active adding files, don't throw an error for normal texts, just ignore unless it's /done
            if state["command"] == "edit_file_adding_active":
                return
            return await message.reply_text("❌ **অনুগ্রহ করে একটি ফাইল অথবা মিডিয়া ফরওয়ার্ড করুন।**")
            
        # Copy file to storage channel
        try:
            stored_msg = await app.copy_message(CHANNEL_ID, message.chat.id, message.id)
            keyword = state["keyword"]
            
            if state["command"] == "awaiting_files":
                if keyword not in filters_dict:
                    filters_dict[keyword] = {'file_ids': [], 'type': 'file_filter'}
                filters_dict[keyword]['file_ids'].append(stored_msg.id)
                last_filter = keyword
                save_data()
                await message.reply_text(f"✅ ফাইল যুক্ত হয়েছে! এই ফিল্টারে আরও ফাইল দিতে থাকুন। শেষ হলে `/done` লিখুন।")
            elif state["command"] == "edit_file_adding_active":
                filters_dict[keyword]['file_ids'].append(stored_msg.id)
                save_data()
                await message.reply_text(f"✅ New File added to '{keyword}' filter! Type /done to finalize.")
        except Exception as e:
            await message.reply_text(f"❌ **ফাইলটি সংরক্ষণ করতে সমস্যা হয়েছে:** {e}")

    elif state["command"] in ["gf_awaiting_up", "gf_awaiting_down"]:
        file_id = None
        if message.document:
            file_id = message.document.file_id
        elif message.video:
            file_id = message.video.file_id
        elif message.audio:
            file_id = message.audio.file_id
        elif message.photo:
            file_id = message.photo[-1].file_id
            
        if message.text and message.text.strip() == "/done":
            action_type = "Up" if state["command"] == "gf_awaiting_up" else "Down"
            del user_states[user_id]
            save_data()
            return await message.reply_text(f"✅ **Global {action_type} file accumulation closed.**")

        if not file_id:
            return await message.reply_text("❌ **Please forward/send a file or write /done to finish.**")

        try:
            stored_msg = await app.copy_message(CHANNEL_ID, message.chat.id, message.id)
            key = "up" if state["command"] == "gf_awaiting_up" else "down"
            if key not in global_files:
                global_files[key] = []
            global_files[key].append(stored_msg.id)
            save_data()
            await message.reply_text(f"✅ Global File recorded! Add more or type /done.")
        except Exception as e:
            await message.reply_text(f"❌ **Error storing global file:** {e}")


# --- Callback Query Handlers (Pyrogram) ---
@app.on_callback_query()
async def callback_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    # Ignore callbacks
    if data == "ignore":
        await callback_query.answer()
        return

    # Public Navigation for Button Filters
    if data.startswith("page_"):
        parts = data.split('_', 2)
        keyword = parts[1]
        page = int(parts[2])
        
        if keyword in filters_dict:
            filter_data = filters_dict[keyword]
            reply_text = filter_data.get('message_text', "Select an option:")
            list_text, reply_markup = create_paged_buttons(keyword, filter_data['button_data'], page)
            try:
                await callback_query.message.edit_text(f"{reply_text}\n\n{list_text}", reply_markup=reply_markup, disable_web_page_preview=True)
            except MessageNotModified:
                pass
        await callback_query.answer()
        return

    # Admin Power Configurations Callback Toggles
    if data.startswith("ap_toggle_") and user_id == ADMIN_ID:
        action = data.split('_', 2)[2]
        if action == "filter_msg":
            admin_powers['filter_message'] = not admin_powers.get('filter_message', True)
        elif action == "auto_del":
            admin_powers['auto_delete'] = not admin_powers.get('auto_delete', True)
        elif action == "restrict":
            admin_powers['admin_restrict'] = not admin_powers.get('admin_restrict', False)
        save_data()
        try:
            await callback_query.message.edit_reply_markup(reply_markup=get_admin_power_keyboard())
        except MessageNotModified:
            pass
        await callback_query.answer("Settings updated.")
        return

    # Admin Channel Add Callback
    if data == "add_schannel" and user_id == ADMIN_ID:
        user_states[user_id] = {"command": "awaiting_schannel_id"}
        save_data()
        await callback_query.message.reply_text("➡️ **Please send the unique numerical ID of the Telegram channel:**")
        await callback_query.answer()
        return
        
    # Admin Channel Delete Callback
    if data.startswith("del_schannel_") and user_id == ADMIN_ID:
        chan_id = int(data.split('_', 2)[2])
        global saved_send_channels
        saved_send_channels = [c for c in saved_send_channels if c['id'] != chan_id]
        save_data()
        
        # Rebuild keyboard
        keyboard = []
        for chan in saved_send_channels:
            keyboard.append([
                InlineKeyboardButton(chan['name'], callback_data="ignore"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"del_schannel_{chan['id']}")
            ])
        keyboard.append([InlineKeyboardButton("➕ Add Channel", callback_data="add_schannel")])
        try:
            await callback_query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        except MessageNotModified:
            pass
        await callback_query.answer("Channel removed.")
        return

    # Admin /send Callback Action Execution
    if data.startswith("send_chan_") and user_id == ADMIN_ID:
        target = data.split('_', 2)[2]
        state = user_states.get(user_id)
        if not state or state["command"] != "sending_filter":
            return await callback_query.answer("❌ Session expired.", show_alert=True)
            
        keyword = state["keyword"]
        file_ids = filters_dict[keyword]['file_ids']
        
        channels_to_send = []
        if target == "all":
            channels_to_send = saved_send_channels
        else:
            chan_id = int(target)
            channels_to_send = [c for c in saved_send_channels if c['id'] == chan_id]
            
        await callback_query.answer("🚀 Sending files to channel(s)...")
        
        success_count = 0
        for chan in channels_to_send:
            for fid in file_ids:
                try:
                    await app.copy_message(chan['id'], CHANNEL_ID, fid)
                    success_count += 1
                    await asyncio.sleep(0.5)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await app.copy_message(chan['id'], CHANNEL_ID, fid)
                    success_count += 1
                except Exception as e:
                    print(f"Error executing /send copying: {e}")
                    
        await callback_query.message.reply_text(f"✅ **Successfully forwarded {success_count} files to target destination channel(s).**")
        del user_states[user_id]
        save_data()
        return

    if data.startswith("send_letchan_") and user_id == ADMIN_ID:
        target = data.split('_', 3)[2]
        state = user_states.get(user_id)
        if not state or state["command"] != "sending_filter_letters":
            return await callback_query.answer("❌ Session expired.", show_alert=True)
            
        channels_to_send = []
        if target == "all":
            channels_to_send = saved_send_channels
        else:
            chan_id = int(target)
            channels_to_send = [c for c in saved_send_channels if c['id'] == chan_id]
            
        await callback_query.answer("🚀 A-Z Letter messages পাঠাচ্ছি...")
        
        import string
        letters = list(string.ascii_uppercase)
        
        for chan in channels_to_send:
            for letter in letters:
                quotes = []
                for num in range(1, 21):
                    num_str = f"{num:02d}"
                    quote_text = f"**{{{num_str}}}** \n\n**Season 01**\n\n\n**Coming Soon...**"
                    quotes.append(quote_text)
                
                joined_quotes = "\n\n".join(quotes)
                msg_body = f"**Letter = {letter}**\n\n{joined_quotes}"
                
                try:
                    await app.send_message(chan['id'], msg_body, parse_mode=ParseMode.MARKDOWN)
                    await asyncio.sleep(0.5)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await app.send_message(chan['id'], msg_body, parse_mode=ParseMode.MARKDOWN)
                except Exception as e:
                    print(f"Error sending letter message: {e}")
                    
        await callback_query.message.reply_text("✅ **২৬টি Letter মেসেজ সফলভাবে পাঠানো হয়েছে!**")
        del user_states[user_id]
        save_data()
        return

    # Admin Button Edit Navigation Callbacks
    if data.startswith("editpage_") and user_id == ADMIN_ID:
        short_id = data.split('_')[1]
        page = int(data.split('_')[2])
        
        # Find keyword by short_id
        keyword = None
        for k in filters_dict.keys():
            if get_short_id(k) == short_id:
                keyword = k
                break
                
        if keyword:
            filter_data = filters_dict[keyword]
            list_text, keyboard = create_paged_edit_buttons(keyword, filter_data['button_data'], page)
            user_states[user_id]["page"] = page
            save_data()
            try:
                await callback_query.message.edit_text(f"✅ **You are now editing the buttons for this filter.**\n\n**Select an option below:**\n\n{list_text}", reply_markup=keyboard, disable_web_page_preview=True)
            except MessageNotModified:
                pass
        await callback_query.answer()
        return

    # Admin Button List Modifier Callbacks (Add/Delete/Set)
    if (data.startswith("edit_add_") or data.startswith("edit_delete_") or data.startswith("edit_set_")) and user_id == ADMIN_ID:
        action = "add" if "_add_" in data else "delete" if "_delete_" in data else "set"
        short_id = data.split('_')[-1]
        
        keyword = None
        for k in filters_dict.keys():
            if get_short_id(k) == short_id:
                keyword = k
                break
                
        if not keyword:
            return await callback_query.answer("Filter not found.", show_alert=True)
            
        if action == "add":
            user_states[user_id] = {"command": "edit_add_buttons", "keyword": keyword}
            save_data()
            await callback_query.message.reply_text("➡️ **অনুগ্রহ করে যে বোতামগুলো যুক্ত করতে চান তা দিন (Format: Button = Link):**")
        elif action == "delete":
            user_states[user_id] = {"command": "edit_delete_buttons", "keyword": keyword}
            save_data()
            await callback_query.message.reply_text("➡️ **অনুগ্রহ করে ডিলিট করার জন্য বোতাম নম্বরের ইনপুট দিন (যেমন: 2, 4, 5, 7-10):**")
        elif action == "set":
            user_states[user_id] = {"command": "edit_set_buttons", "keyword": keyword}
            save_data()
            await callback_query.message.reply_text("➡️ **Rearrange buttons using swap/move rules:**\n\n• `1-5, 3-8` - Swaps items.\n• `6u-2` - Moves item 6 to index 2 position.\n• `a 5,1,3,2` - Absolute order starting index lineup.")
            
        await callback_query.answer()
        return

    # Admin File Edit Navigation Callbacks
    if data.startswith("editfilepage_") and user_id == ADMIN_ID:
        short_id = data.split('_')[1]
        page = int(data.split('_')[2])
        
        keyword = None
        for k in filters_dict.keys():
            if get_short_id(k) == short_id:
                keyword = k
                break
                
        if keyword:
            filter_data = filters_dict[keyword]
            list_text, keyboard = create_paged_file_edit_buttons(keyword, filter_data['file_ids'], page)
            user_states[user_id]["page"] = page
            save_data()
            try:
                await callback_query.message.edit_text(f"✅ **You are now editing the files for this filter.**\n\n**Select an option below:**\n\n{list_text}", reply_markup=keyboard)
            except MessageNotModified:
                pass
        await callback_query.answer()
        return

    # Admin File Modifier Action triggers
    if (data.startswith("editfile_add_") or data.startswith("editfile_delete_") or data.startswith("editfile_set_")) and user_id == ADMIN_ID:
        action = "add" if "_add_" in data else "delete" if "_delete_" in data else "set"
        short_id = data.split('_')[-1]
        
        keyword = None
        for k in filters_dict.keys():
            if get_short_id(k) == short_id:
                keyword = k
                break
                
        if not keyword:
            return await callback_query.answer("Filter context lost.", show_alert=True)
            
        if action == "add":
            user_states[user_id] = {"command": "edit_file_add", "keyword": keyword}
            save_data()
            await callback_query.message.reply_text("➡️ **Forward files to add now. Type /done when completely finished.**")
        elif action == "delete":
            user_states[user_id] = {"command": "edit_file_delete", "keyword": keyword}
            save_data()
            await callback_query.message.reply_text("➡️ **Provide numerical file indices to clear out (e.g., 1,3, 5-9):**")
        elif action == "set":
            user_states[user_id] = {"command": "edit_file_set", "keyword": keyword}
            save_data()
            await callback_query.message.reply_text("➡️ **Provide ordering layout for files (e.g., `1-4`, `5u-1` or `a 3,2,1`):**")
            
        await callback_query.answer()
        return

    # Global Files Action Callbacks
    if data.startswith("gf_action_") and user_id == ADMIN_ID:
        target = data.split('_')[-1]
        cmd = "gf_awaiting_up" if target == "up" else "gf_awaiting_down"
        user_states[user_id] = {"command": cmd}
        save_data()
        await callback_query.message.reply_text(f"➡️ **Forward/Send files to add as Global {target.upper()}. Enter /done to save and close context.**")
        await callback_query.answer()
        return
        
    if data.startswith("gf_del_") and user_id == ADMIN_ID:
        target = data.split('_')[-1]
        if target in global_files:
            global_files[target] = []
            save_data()
            await callback_query.message.reply_text(f"🗑️ **Global {target.upper()} records completely flushed.**")
        await callback_query.answer()
        return

    # Custom Start Message callbacks
    if data == "add_start_message" and user_id == ADMIN_ID:
        user_states[user_id] = {"command": "awaiting_start_message_text"}
        save_data()
        await callback_query.message.reply_text("➡️ **Please send the main markdown text layout body of your custom start welcome message:**")
        await callback_query.answer()
        return
        
    if data == "view_start_message" and user_id == ADMIN_ID:
        if start_message_data:
            try:
                t = start_message_data['text']
                b = parse_start_message_buttons_from_text(start_message_data['buttons'])
                # Send sample inline validation replica
                await callback_query.message.reply_text(f"📝 **Current Custom Start message Configuration Preview:**\n\n{t}", reply_markup=b, parse_mode=ParseMode.MARKDOWN)
                
                # Append delete inline shortcut operational option button
                del_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ Delete Start Message", callback_data="delete_start_message")]])
                await callback_query.message.reply_text("⚙️ **Actions:**", reply_markup=del_kb)
            except Exception as e:
                await callback_query.message.reply_text(f"❌ Preview generation failure: {e}")
        else:
            await callback_query.message.reply_text("❌ **No custom welcome start message layout active.**")
        await callback_query.answer()
        return

    if data == "delete_start_message" and user_id == ADMIN_ID:
        global start_message_data
        start_message_data = {}
        save_data()
        await callback_query.message.edit_text("🗑️ **Start message configuration cleared successfully.**")
        await callback_query.answer()
        return

    # Channel ID retrieval internal navigation helpers
    if data.startswith("cid_") and user_id == ADMIN_ID:
        target = data.split('_')[1]
        if target == "channel":
            await callback_query.message.reply_text("ℹ️ **To fetch Channel/Group numerical Chat ID:** Forward any message natively from that target channel into this chat.")
        elif target == "file":
            await callback_query.message.reply_text("ℹ️ **To retrieve raw file id identifiers:** Send or forward any document type right inside this session context.")
        elif target == "owner":
            await callback_query.message.reply_text(f"ℹ️ **Your numerical context ID:** `{user_id}`")
        await callback_query.answer()
        return

# /broadcast কমান্ড হ্যান্ডলার (পরিবর্তিত)
@app.on_message(filters.command("broadcast") & filters.private & filters.user(ADMIN_ID))
async def broadcast_cmd(client, message):
    if not message.reply_to_message:
        return await message.reply_text("❌ **অনুগ্রহ করে যে মেসেজটি ব্রডকাস্ট করতে চান সেটি রিপ্লাই করে এই কমান্ডটি দিন।**")
    
    msg = await message.reply_text("🚀 **ব্রডকাস্ট শুরু হচ্ছে...**")
    success = 0
    failed = 0
    
    for u_id in list(user_list):
        try:
            await message.reply_to_message.copy(u_id)
            success += 1
            await asyncio.sleep(0.1)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await message.reply_to_message.copy(u_id)
            success += 1
        except Exception:
            failed += 1
            
    await msg.edit_text(f"✅ **ব্রডকাস্ট সম্পন্ন হয়েছে!**\n\n• সফল: `{success}`\n• ব্যর্থ: `{failed}`")

# /delete কমান্ড হ্যান্ডলার
@app.on_message(filters.command("delete") & filters.private & filters.user(ADMIN_ID))
async def delete_cmd(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **ব্যবহার:** `/delete <ফিল্টার_নাম>`")
        
    keyword = args[1].lower().strip()
    if keyword in filters_dict:
        del filters_dict[keyword]
        save_data()
        await message.reply_text(f"✅ **ফিল্টার '{keyword}' এবং তার সমস্ত ফাইল সফলভাবে ডিলিট করা হয়েছে।**")
    else:
        await message.reply_text("❌ **এই নামে কোনো ফিল্টার পাওয়া যায়নি।**")

# /restrict কমান্ড হ্যান্ডলার
@app.on_message(filters.command("restrict") & filters.private & filters.user(ADMIN_ID))
async def restrict_cmd(client, message):
    global restrict_status
    restrict_status = not restrict_status
    save_data()
    status = "ON (মেসেজ ফরওয়ার্ড করা যাবে না)" if restrict_status else "OFF (মেসেজ ফরওয়ার্ড করা যাবে)"
    await message.reply_text(f"🔐 **প্রটেক্ট কনটেন্ট এখন:** `{status}`")

# /ban কমান্ড হ্যান্ডলার
@app.on_message(filters.command("ban") & filters.private & filters.user(ADMIN_ID))
async def ban_cmd(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **ব্যবহার:** `/ban <ইউজার_আইডি>`")
        
    try:
        b_user_id = int(args[1].strip())
        banned_users.add(b_user_id)
        save_data()
        await message.reply_text(f"✅ **ইউজার `{b_user_id}` কে সফলভাবে ব্যান করা হয়েছে।**")
    except ValueError:
        await message.reply_text("❌ **সঠিক ইউজার আইডি দিন।**")

# /unban  কমান্ড হ্যান্ডলার
@app.on_message(filters.command("unban") & filters.private & filters.user(ADMIN_ID))
async def unban_cmd(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **ব্যবহার:** `/unban <ইউজার_আইডি>`")
        
    try:
        u_user_id = int(args[1].strip())
        if u_user_id in banned_users:
            banned_users.remove(u_user_id)
            save_data()
            await message.reply_text(f"✅ **ইউজার `{u_user_id}` কে সফলভাবে আনব্যান করা হয়েছে।**")
        else:
            await message.reply_text("❌ **এই ইউজারটি ব্যান লিস্টে নেই।**")
    except ValueError:
        await message.reply_text("❌ **সঠিক ইউজার আইডি দিন।**")

# /auto_delete কমান্ড হ্যান্ডলার
@app.on_message(filters.command("auto_delete") & filters.private & filters.user(ADMIN_ID))
async def auto_delete_cmd(client, message):
    global autodelete_time
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **ব্যবহার:** `/auto_delete <সময়>`\nউদাহরণ: `30m`, `1h`, `2h`, `off`")
        
    time_str = args[1].lower().strip()
    
    if time_str == "off":
        autodelete_time = 0
        save_data()
        return await message.reply_text("❌ **অটো-ডিলিট ফিচার বন্ধ করা হয়েছে।**")
        
    match = re.match(r"(\d+)(m|h)", time_str)
    if not match:
        return await message.reply_text("❌ **ভুল ফরম্যাট!** অনুগ্রহ করে `30m` অথবা `1h` এর মত সময় দিন।")
        
    val = int(match.group(1))
    unit = match.group(2)
    
    if unit == "m":
        autodelete_time = val * 60
    elif unit == "h":
        autodelete_time = val * 3600
        
    save_data()
    await message.reply_text(f"⏱️ **অটো-ডিলিট সময় সেট করা হয়েছে:** `{time_str}` (ফাইল পাঠানোর পর এই সময় শেষে ডিলিট হবে)")

# ফরওয়ার্ড করা মেসেজ থেকে আইডি নেওয়ার চ্যানেল হ্যান্ডলার (নিউ)
@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.forwarded)
async def forwarded_id_handler(client, message):
    # Channel target ID extractor handler
    if message.forward_from_chat:
        await message.reply_text(f"📢 **Forwarded Chat/Channel Info:**\n\n• **Title:** `{message.forward_from_chat.title}`\n• **ID:** `{message.forward_from_chat.id}`")
    elif message.forward_from:
        await message.reply_text(f"👤 **Forwarded User Info:**\n\n• **Name:** `{message.forward_from.first_name}`\n• **ID:** `{message.forward_from.id}`")

# Catch manual configuration fallback delete query regex pattern
@app.on_callback_query(filters.regex(r"^delete_start_message$"))
async def delete_start_message_callback(client, callback_query):
    global start_message_data
    await callback_query.answer("Deleting start message...", show_alert=True)
    start_message_data = {}
    save_data()
    await callback_query.edit_message_text("🗑️ **Start message has been successfully deleted.**")
    
# Callbacks for Admin Power Settings (NEW)
@app.on_callback_query(filters.regex(r"^ap_toggle_(.+)$") & filters.user(ADMIN_ID))
async def ap_toggle_callback(client, callback_query):
    action = callback_query.data.split('_', 2)[2]
    
    if action == "filter_msg":
        admin_powers['filter_message'] = not admin_powers.get('filter_message', True)
    elif action == "auto_del":
        admin_powers['auto_delete'] = not admin_powers.get('auto_delete', True)
    elif action == "restrict":
        admin_powers['admin_restrict'] = not admin_powers.get('admin_restrict', False)
        
    save_data()
    await callback_query.message.edit_reply_markup(reply_markup=get_admin_power_keyboard())

# --- Run Services ---
def run_flask_and_pyrogram():
    connect_to_mongodb()
    load_data()
    flask_thread = threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=PORT, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    
    ping_thread = threading.Thread(target=ping_service)
    ping_thread.daemon = True
    ping_thread.start()
    
    print("Starting Pyrogram Bot...")
    app.run()

if __name__ == "__main__":
    run_flask_and_pyrogram()
