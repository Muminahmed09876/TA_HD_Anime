import os
import asyncio
import time
import threading
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified, FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from dotenv import load_dotenv
from flask import Flask, render_template_string
import requests
import re
import math

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
deep_link_keyword = None
user_states = {}

# --- Join Channels Configuration ---
CHANNEL_ID_2 = -1003049936443
CHANNEL_LINK = "https://t.me/TA_HD_Anime"
join_channels = [{"id": CHANNEL_ID_2, "name": "TA HD Anime Hindi Official Dubbed", "link": CHANNEL_LINK}]

# --- Database Client and Collection ---
mongo_client = None
db = None
collection = None

# --- Flask Web Server ---
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

# Ping service to keep the bot alive
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

# --- Database Functions (Updated) ---
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

    savable_filters = {}
    for key, data in filters_dict.items():
        savable_filters[key] = {
            'buttons': [{'text': btn['text'], 'url': btn.get('url')} for btn in data['buttons']],
            'files': data['files']
        }

    data = {
        "filters_dict": savable_filters,
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
        loaded_filters = data.get("filters_dict", {})
        filters_dict = {
            key: {
                'buttons': [{'text': btn['text'], 'url': btn.get('url')} for btn in btn_list['buttons']],
                'files': btn_list['files']
            }
            for key, btn_list in loaded_filters.items()
        }
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
async def is_user_member(client, user_id):
    try:
        await client.get_chat_member(CHANNEL_ID_2, user_id)
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

async def create_filter_buttons_with_pagination(keyword, page=0, for_edit=False):
    filter_data = filters_dict.get(keyword, None)
    if not filter_data:
        return None

    all_buttons = filter_data['buttons']
    files = filter_data['files']

    if files:
        for file_id in files:
            all_buttons.append({'text': f"File: {file_id}", 'url': None, 'is_file': True, 'id': file_id})
    
    per_page = 10
    total_pages = math.ceil(len(all_buttons) / per_page)
    start_index = page * per_page
    end_index = start_index + per_page
    
    current_page_buttons = all_buttons[start_index:end_index]
    
    keyboard_buttons = []
    
    if for_edit:
        keyboard_buttons.append([InlineKeyboardButton(f"🎬 {keyword} 🎬", callback_data=f"edit_filter_{keyword}")])
        for i, btn_data in enumerate(current_page_buttons):
            button_text = f"{i + start_index + 1}. {btn_data['text']}"
            if btn_data.get('is_file'):
                callback_data = f"send_file_{btn_data['id']}"
                keyboard_buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
            else:
                url = btn_data.get('url', 'https://t.me/TA_HD_Anime') # default to avoid errors
                keyboard_buttons.append([InlineKeyboardButton(button_text, url=url)])
    else:
        keyboard_buttons.append([InlineKeyboardButton(f"🎬 {keyword} 🎬", callback_data=f"filter_info_{keyword}")])
        for btn_data in current_page_buttons:
            if btn_data.get('is_file'):
                keyboard_buttons.append([InlineKeyboardButton(btn_data['text'], callback_data=f"send_file_{btn_data['id']}")])
            else:
                keyboard_buttons.append([InlineKeyboardButton(btn_data['text'], url=btn_data['url'])])
    
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page - 1}_{keyword}{'_edit' if for_edit else ''}"))
        
        nav_buttons.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="ignore"))

        if page + 1 < total_pages:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page + 1}_{keyword}{'_edit' if for_edit else ''}"))
        
        keyboard_buttons.append(nav_buttons)

    if for_edit:
        edit_nav_buttons = [
            InlineKeyboardButton("➕ Add Button", callback_data=f"add_link_{keyword}"),
            InlineKeyboardButton("🗑️ Delete Button", callback_data=f"delete_link_{keyword}"),
            InlineKeyboardButton("🔄 Set Button", callback_data=f"set_link_{keyword}")
        ]
        keyboard_buttons.append(edit_nav_buttons)

    return InlineKeyboardMarkup(keyboard_buttons)


# --- Message Handlers (Pyrogram) ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    global deep_link_keyword, autodelete_time
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
    if len(args) > 1:
        deep_link_keyword = args[1].lower()
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
        bot_username = (await client.get_me()).username
        try_again_url = f"https://t.me/{bot_username}?start={deep_link_keyword}" if deep_link_keyword else f"https://t.me/{bot_username}"
        
        buttons = [[InlineKeyboardButton(f"✅ Join TA_HD_How_To_Download", url=CHANNEL_LINK)]]
        buttons.append([InlineKeyboardButton("🔄 Try Again", url=try_again_url)])
        keyboard = InlineKeyboardMarkup(buttons)
        
        return await message.reply_text(
            "❌ **You must join the following channels to use this bot:**",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

    if deep_link_keyword:
        keyword = deep_link_keyword.lower()
        
        if keyword in filters_dict:
            keyboard = await create_filter_buttons_with_pagination(keyword)
            if keyboard:
                await message.reply_text(f"🎬 **Files for '{keyword}':**", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            else:
                await message.reply_text(f"❌ **No files or links found for '{keyword}'.**")
        else:
            await message.reply_text("❌ **No files found for this keyword.**")
        deep_link_keyword = None
        return
    
    if user_id == ADMIN_ID:
        admin_commands = (
            "🌟 **Welcome, Admin! Here are your commands:**\n\n"
            "**/filter** - Create a new filter.\n"
            "**/edit** - Edit an existing filter.\n"
            "**/delete_filter** - Delete a filter.\n"
            "**/broadcast** - Reply to a message with this command to broadcast it to all users.\n"
            "**/restrict** - Toggle message forwarding restriction (ON/OFF).\n"
            "**/ban <user_id>** - Ban a user.\n"
            "**/unban <user_id>** - Unban a user.\n"
            "**/auto_delete <time>** - Set auto-delete time for files (e.g., 30m, 1h, 12h, 24h, off).\n"
            "**/channel_id** - Get the ID of a channel by forwarding a message from it."
        )
        await message.reply_text(admin_commands, parse_mode=ParseMode.MARKDOWN)
    else:
        await message.reply_text("👋 **Welcome!** You can access files via special links.")


@app.on_message(filters.command("filter") & filters.private & filters.user(ADMIN_ID))
async def create_filter_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "awaiting_filter_name"}
    save_data()
    await message.reply_text("🎬 **অনুগ্রহ করে একটি নতুন ফিল্টারের নাম দিন।**\n\n_উদাহরণ: TA HD Anime_")

@app.on_message(filters.command("edit") & filters.private & filters.user(ADMIN_ID))
async def edit_filter_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "awaiting_edit_filter_name"}
    save_data()
    await message.reply_text("✏️ **অনুগ্রহ করে সেই ফিল্টারের নাম দিন যেটি আপনি এডিট করতে চান।**")

@app.on_message(filters.command("delete_filter") & filters.private & filters.user(ADMIN_ID))
async def delete_filter_by_name(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **ব্যবহার:** `/delete_filter <filter name>`")
    
    filter_name = args[1].lower().strip()
    if filter_name in filters_dict:
        del filters_dict[filter_name]
        save_data()
        await message.reply_text(f"✅ **ফিল্টার '{filter_name}' সফলভাবে মুছে ফেলা হয়েছে।**")
    else:
        await message.reply_text(f"❌ **ফিল্টার '{filter_name}' খুঁজে পাওয়া যায়নি।**")


@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID))
async def handle_admin_text_input(client, message):
    user_id = message.from_user.id
    user_state = user_states.get(user_id, {})
    
    # --- Create Filter Logic ---
    if user_state.get("command") == "awaiting_filter_name":
        filter_name = message.text.lower().strip()
        if filter_name in filters_dict:
            return await message.reply_text("❌ **এই নামে একটি ফিল্টার ইতিমধ্যে বিদ্যমান আছে।**\n\n**অনুগ্রহ করে অন্য একটি নাম দিন।**")
        
        filters_dict[filter_name] = {'buttons': [], 'files': []}
        save_data()
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🎬 {filter_name.upper()} 🎬", callback_data="ignore")],
            [InlineKeyboardButton("➕ Add Link Button", callback_data=f"add_link_{filter_name}")]
        ])
        
        del user_states[user_id]
        save_data()
        await message.reply_text(
            f"✅ **ফিল্টার '{filter_name}' তৈরি করা হয়েছে!**\n\nএখন আপনি লিঙ্ক বাটন যোগ করতে পারেন।",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

    # --- Edit Filter Logic ---
    elif user_state.get("command") == "awaiting_edit_filter_name":
        filter_name = message.text.lower().strip()
        if filter_name not in filters_dict:
            del user_states[user_id]
            return await message.reply_text("❌ **এই নামে কোনো ফিল্টার খুঁজে পাওয়া যায়নি।**")
        
        user_states[user_id] = {"command": "editing_filter", "keyword": filter_name}
        save_data()
        keyboard = await create_filter_buttons_with_pagination(filter_name, for_edit=True)
        await message.reply_text(
            f"✏️ **ফিল্টার '{filter_name}' এডিটের জন্য বিকল্পগুলি বেছে নিন।**",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

    # --- Add Button Logic ---
    elif user_state.get("command") == "awaiting_links":
        filter_name = user_state['keyword']
        link_data = message.text
        
        links = link_data.split(',')
        for item in links:
            parts = item.strip().split(' - ', 1)
            if len(parts) == 2:
                button_text = parts[0].strip()
                link = parts[1].strip()
                if filter_name in filters_dict:
                    filters_dict[filter_name]['buttons'].append({'text': button_text, 'url': link})
        save_data()
        
        del user_states[user_id]
        save_data()
        await message.reply_text("✅ **লিঙ্ক বাটনগুলো সফলভাবে যোগ করা হয়েছে!**")

    # --- Delete Button Logic ---
    elif user_state.get("command") == "awaiting_button_numbers_to_delete":
        filter_name = user_state['keyword']
        numbers_str = message.text.split(',')
        try:
            numbers_to_delete = sorted([int(n.strip()) for n in numbers_str], reverse=True)
            filter_data = filters_dict[filter_name]
            deleted_count = 0
            for num in numbers_to_delete:
                idx = num - 1
                if 0 <= idx < len(filter_data['buttons']):
                    del filter_data['buttons'][idx]
                    deleted_count += 1
            save_data()
            del user_states[user_id]
            save_data()
            await message.reply_text(f"✅ **{deleted_count}টি বাটন সফলভাবে ডিলিট করা হয়েছে।**")
        except (ValueError, IndexError):
            del user_states[user_id]
            save_data()
            await message.reply_text("❌ **ভুল বাটন নম্বর। অনুগ্রহ করে সঠিক সংখ্যা কমা দিয়ে আলাদা করে দিন।**")

    # --- Set Button (Swap) Logic ---
    elif user_state.get("command") == "awaiting_button_swap_numbers":
        filter_name = user_state['keyword']
        swap_pairs_str = message.text.split(',')
        try:
            filter_data = filters_dict[filter_name]
            for pair_str in swap_pairs_str:
                parts = pair_str.strip().split('-')
                if len(parts) == 2:
                    idx1 = int(parts[0].strip()) - 1
                    idx2 = int(parts[1].strip()) - 1
                    if 0 <= idx1 < len(filter_data['buttons']) and 0 <= idx2 < len(filter_data['buttons']):
                        filter_data['buttons'][idx1], filter_data['buttons'][idx2] = filter_data['buttons'][idx2], filter_data['buttons'][idx1]
            save_data()
            del user_states[user_id]
            save_data()
            await message.reply_text("✅ **বাটনগুলোর ক্রম সফলভাবে পরিবর্তন করা হয়েছে।**")
        except (ValueError, IndexError):
            del user_states[user_id]
            save_data()
            await message.reply_text("❌ **ভুল ফরম্যাট। অনুগ্রহ করে '1 - 2, 3 - 5' ফরম্যাট অনুসরণ করুন।**")
    
    # --- Edit Links Logic (Re-add all) ---
    elif user_state.get("command") == "awaiting_edit_links":
        filter_name = user_state['keyword']
        link_data = message.text
        
        filters_dict[filter_name]['buttons'] = []
        
        links = link_data.split(',')
        for item in links:
            parts = item.strip().split(' - ', 1)
            if len(parts) == 2:
                button_text = parts[0].strip()
                link = parts[1].strip()
                if filter_name in filters_dict:
                    filters_dict[filter_name]['buttons'].append({'text': button_text, 'url': link})
        save_data()
        
        del user_states[user_id]
        save_data()
        await message.reply_text("✅ **লিঙ্ক বাটনগুলো সফলভাবে আপডেট করা হয়েছে!**")


@app.on_message(filters.text & filters.private)
async def handle_user_text_input(client, message):
    if message.from_user.id == ADMIN_ID:
        return
    
    user_id = message.from_user.id
    if user_id in banned_users:
        return await message.reply_text("❌ **You are banned from using this bot.**")

    keyword = message.text.lower().strip()
    if keyword in filters_dict:
        if not await is_user_member(client, user_id):
            return await message.reply_text(
                "❌ **You must join the following channels to use this bot:**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"✅ Join TA_HD_How_To_Download", url=CHANNEL_LINK)],
                    [InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{(await client.get_me()).username}?start={keyword}")]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )

        keyboard = await create_filter_buttons_with_pagination(keyword)
        if keyboard:
            await message.reply_text(f"🎬 **Files for '{keyword}':**", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text(f"❌ **No files or links found for '{keyword}'.**")
    else:
        await message.reply_text("❌ **এই নামে কোনো ফিল্টার খুঁজে পাওয়া যায়নি।**")


@app.on_message(filters.channel & filters.text & filters.chat(CHANNEL_ID))
async def channel_text_handler(client, message):
    global last_filter
    text = message.text
    if text and len(text.split()) == 1:
        keyword = text.lower().replace('#', '')
        if not keyword:
            return
        last_filter = keyword
        save_data()
        if keyword not in filters_dict:
            filters_dict[keyword] = {'buttons': [], 'files': []}
            save_data()
            await app.send_message(
                LOG_CHANNEL_ID,
                f"✅ **New filter created!**\n🔗 Share link: `https://t.me/{(await app.get_me()).username}?start={keyword}`",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await app.send_message(LOG_CHANNEL_ID, f"⚠️ **Filter '{keyword}' is already active.**")

@app.on_message(filters.channel & filters.media & filters.chat(CHANNEL_ID))
async def channel_media_handler(client, message):
    if last_filter:
        keyword = last_filter
        if keyword not in filters_dict:
            filters_dict[keyword] = {'buttons': [], 'files': []}
        filters_dict[keyword]['files'].append(message.id)
        save_data()
    else:
        await app.send_message(LOG_CHANNEL_ID, "⚠️ **No active filter found.**")

@app.on_deleted_messages(filters.channel & filters.chat(CHANNEL_ID))
async def channel_delete_handler(client, messages):
    global last_filter
    for message in messages:
        if message.text and len(message.text.split()) == 1:
            keyword = message.text.lower().replace('#', '')
            if keyword in filters_dict:
                del filters_dict[keyword]
                if keyword == last_filter:
                    last_filter = None
                save_data()
                await app.send_message(LOG_CHANNEL_ID, f"🗑️ **Filter '{keyword}' has been deleted.**")
            if last_filter == keyword:
                last_filter = None
                await app.send_message(LOG_CHANNEL_ID, "📝 **Note:** The last active filter has been cleared.")
                save_data()


@app.on_callback_query(filters.regex("^add_link_"))
async def add_link_callback(client, callback_query):
    query = callback_query
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        return await query.answer("❌ আপনি এই কমান্ড ব্যবহার করার জন্য অনুমোদিত নন।", show_alert=True)
    keyword = query.data.split('_')[-1]
    user_states[user_id] = {"command": "awaiting_links", "keyword": keyword}
    save_data()
    await query.message.edit_text(
        "🔗 **অনুগ্রহ করে লিঙ্কগুলো নিচের ফরম্যাটে দিন:**\n\n_উদাহরণ: King 01 - https://example.com/king_01, King 02 - https://example.com/king_02_\n\n**আপনি একাধিক লিঙ্ক কমা (,) দিয়ে আলাদা করতে পারেন।**"
    )

@app.on_callback_query(filters.regex("^delete_link_"))
async def delete_link_callback(client, callback_query):
    query = callback_query
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        return await query.answer("❌ আপনি এই কমান্ড ব্যবহার করার জন্য অনুমোদিত নন।", show_alert=True)
    keyword = query.data.split('_')[-1]
    user_states[user_id] = {"command": "awaiting_button_numbers_to_delete", "keyword": keyword}
    save_data()
    await query.message.edit_text(
        "🗑️ **অনুগ্রহ করে সেই বাটনের নম্বরগুলো দিন যেগুলো আপনি ডিলিট করতে চান।**\n\n_উদাহরণ: 2, 5, 8_"
    )

@app.on_callback_query(filters.regex("^set_link_"))
async def set_link_callback(client, callback_query):
    query = callback_query
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        return await query.answer("❌ আপনি এই কমান্ড ব্যবহার করার জন্য অনুমোদিত নন।", show_alert=True)
    keyword = query.data.split('_')[-1]
    user_states[user_id] = {"command": "awaiting_button_swap_numbers", "keyword": keyword}
    save_data()
    await query.message.edit_text(
        "🔄 **অনুগ্রহ করে বাটন নম্বরগুলোর নতুন ক্রম দিন।**\n\n_উদাহরণ: 1 - 2, 3 - 5_"
    )


@app.on_callback_query(filters.regex("^edit_filter_"))
async def edit_filter_callback(client, callback_query):
    query = callback_query
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        return await query.answer("❌ আপনি এই কমান্ড ব্যবহার করার জন্য অনুমোদিত নন।", show_alert=True)
    
    keyword = query.data.split('_')[-1]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ লিঙ্ক বাটন যোগ করুন", callback_data=f"add_link_{keyword}")],
        [InlineKeyboardButton("✏️ লিঙ্ক বাটন এডিট করুন", callback_data=f"edit_links_{keyword}")],
        [InlineKeyboardButton("🗑️ ফাইল যোগ করুন", callback_data=f"add_files_{keyword}")]
    ])

    await query.message.edit_text(
        f"✏️ **ফিল্টার '{keyword}' এডিটের জন্য বিকল্পগুলি বেছে নিন।**",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )

@app.on_callback_query(filters.regex("^edit_links_"))
async def edit_links_callback(client, callback_query):
    query = callback_query
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        return await query.answer("❌ আপনি এই কমান্ড ব্যবহার করার জন্য অনুমোদিত নন।", show_alert=True)
    
    keyword = query.data.split('_')[-1]
    user_states[user_id] = {"command": "awaiting_edit_links", "keyword": keyword}
    save_data()
    await query.message.edit_text(
        "✏️ **অনুগ্রহ করে নতুন লিঙ্কগুলো নিচের ফরম্যাটে দিন। এটি বিদ্যমান সব লিঙ্ক মুছে ফেলবে।**\n\n_উদাহরণ: King 01 - https://example.com/king_01, King 02 - https://example.com/king_02_\n\n**আপনি একাধিক লিঙ্ক কমা (,) দিয়ে আলাদা করতে পারেন।**"
    )

@app.on_callback_query(filters.regex("^confirm_delete_"))
async def confirm_delete_callback(client, callback_query):
    query = callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        return await query.answer("❌ আপনি এই কমান্ড ব্যবহার করার জন্য অনুমোদিত নন।", show_alert=True)
    
    keyword = query.data.split('_')[-1]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ হ্যাঁ, নিশ্চিত", callback_data=f"final_delete_{keyword}")],
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="cancel_delete")]
    ])
    await query.message.edit_text(
        f"⚠️ **আপনি কি নিশ্চিত যে আপনি '{keyword}' ফিল্টারটি মুছে ফেলতে চান?**\n\n_এই অ্যাকশনটি অপরিবর্তনীয়।_",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )

@app.on_callback_query(filters.regex("^final_delete_"))
async def final_delete_callback(client, callback_query):
    query = callback_query
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        return await query.answer("❌ আপনি এই কমান্ড ব্যবহার করার জন্য অনুমোদিত নন।", show_alert=True)
    
    keyword = query.data.split('_')[-1]
    
    if keyword in filters_dict:
        del filters_dict[keyword]
        save_data()
        await query.message.edit_text(f"✅ **ফিল্টার '{keyword}' সফলভাবে মুছে ফেলা হয়েছে।**")
    else:
        await query.message.edit_text(f"❌ **ফিল্টার '{keyword}' খুঁজে পাওয়া যায়নি।**")


@app.on_callback_query(filters.regex("^page_"))
async def pagination_callback(client, callback_query):
    query = callback_query
    data = query.data.split('_')
    page = int(data[1])
    keyword = data[2]
    for_edit = data[-1] == 'edit'
    
    keyboard = await create_filter_buttons_with_pagination(keyword, page, for_edit)
    if keyboard:
        try:
            await query.message.edit_reply_markup(reply_markup=keyboard)
        except MessageNotModified:
            pass

@app.on_callback_query(filters.regex("^send_file_"))
async def send_file_callback(client, callback_query):
    query = callback_query
    file_id = int(query.data.split('_')[-1])
    chat_id = query.message.chat.id
    
    await query.answer("Sending your file...")

    try:
        sent_msg = await app.copy_message(chat_id, CHANNEL_ID, file_id, protect_content=restrict_status)
        if autodelete_time > 0:
            await delete_messages_later(chat_id, [sent_msg.id], autodelete_time)
            
    except Exception as e:
        await query.message.reply_text("❌ **Error sending file.**")
        print(f"Error sending file {file_id}: {e}")

@app.on_callback_query(filters.regex("check_join_status"))
async def check_join_status_callback(client, callback_query):
    user_id = callback_query.from_user.id
    await callback_query.answer("Checking membership...", show_alert=True)
    
    if await is_user_member(client, user_id):
        await callback_query.message.edit_text("✅ **You have successfully joined!**\n\n**Please go back to the chat and send your link again.**", parse_mode=ParseMode.MARKDOWN)
    else:
        buttons = [[InlineKeyboardButton(f"✅ Join TA_HD_How_To_Download", url=CHANNEL_LINK)]]
        bot_username = (await client.get_me()).username
        try_again_url = f"https://t.me/{bot_username}"
        buttons.append([InlineKeyboardButton("🔄 Try Again", url=try_again_url)])
        keyboard = InlineKeyboardMarkup(buttons)
        await callback_query.message.edit_text("❌ **You are still not a member.**", reply_markup=keyboard)


@app.on_message(filters.command("broadcast") & filters.private & filters.user(ADMIN_ID))
async def broadcast_cmd(client, message):
    if not message.reply_to_message:
        return await message.reply_text("📌 **Reply to a message** with `/broadcast`.")
    sent_count = 0
    failed_count = 0
    total_users = len(user_list)
    progress_msg = await message.reply_text(f"📢 **Broadcasting to {total_users} users...** (0/{total_users})")
    for user_id in list(user_list):
        try:
            if user_id in banned_users:
                continue
            await message.reply_to_message.copy(user_id, protect_content=True)
            sent_count += 1
        except Exception as e:
            print(f"Failed to send broadcast to user {user_id}: {e}")
            failed_count += 1
        if (sent_count + failed_count) % 10 == 0:
            try:
                await progress_msg.edit_text(
                    f"📢 **Broadcasting...**\n✅ Sent: {sent_count}\n❌ Failed: {failed_count}\nTotal: {total_users}"
                )
            except MessageNotModified:
                pass
        await asyncio.sleep(0.1)
    await progress_msg.edit_text(f"✅ **Broadcast complete!**\nSent to {sent_count} users.\nFailed to send to {failed_count} users.")

@app.on_message(filters.command("restrict") & filters.private & filters.user(ADMIN_ID))
async def restrict_cmd(client, message):
    global restrict_status
    restrict_status = not restrict_status
    save_data()
    status_text = "ON" if restrict_status else "OFF"
    await message.reply_text(f"🔒 **Message forwarding restriction is now {status_text}.**")
    
@app.on_message(filters.command("ban") & filters.private & filters.user(ADMIN_ID))
async def ban_cmd(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **Usage:** `/ban <user_id>`", parse_mode=ParseMode.MARKDOWN)
    try:
        user_id_to_ban = int(args[1])
        if user_id_to_ban == ADMIN_ID:
            return await message.reply_text("❌ You cannot ban the admin.")
        banned_users.add(user_id_to_ban)
        save_data()
        await message.reply_text(f"✅ User `{user_id_to_ban}` has been banned.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await message.reply_text("❌ Invalid User ID. Please provide a valid integer ID.")

@app.on_message(filters.command("unban") & filters.private & filters.user(ADMIN_ID))
async def unban_cmd(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **Usage:** `/unban <user_id>`", parse_mode=ParseMode.MARKDOWN)
    try:
        user_id_to_unban = int(args[1])
        if user_id_to_unban in banned_users:
            banned_users.remove(user_id_to_unban)
            save_data()
            await message.reply_text(f"✅ User `{user_id_to_unban}` has been unbanned.", parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text(f"❌ User `{user_id_to_unban}` is not currently banned.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await message.reply_text("❌ Invalid User ID. Please provide a valid integer ID.")

@app.on_message(filters.command("auto_delete") & filters.private & filters.user(ADMIN_ID))
async def auto_delete_cmd(client, message):
    global autodelete_time
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **Usage:** `/auto_delete <time>` (e.g., 30m, 1h, 12h, 24h, off)", parse_mode=ParseMode.MARKDOWN)
    
    time_str = args[1].lower().strip()
    if time_str == "off":
        autodelete_time = 0
        await message.reply_text("✅ Auto-delete has been turned off.")
    else:
        match = re.match(r"(\d+)([hmd])", time_str)
        if not match:
            return await message.reply_text("❌ Invalid time format. Use '30m', '1h', '12h', '24h', or 'off'.")
        
        value = int(match.group(1))
        unit = match.group(2)
        
        if unit == 'm':
            autodelete_time = value * 60
        elif unit == 'h':
            autodelete_time = value * 3600
        elif unit == 'd':
            autodelete_time = value * 86400
        
        await message.reply_text(f"✅ Auto-delete time set to **{autodelete_time // 60} minutes**.", parse_mode=ParseMode.MARKDOWN)
    save_data()

@app.on_message(filters.command("channel_id") & filters.private & filters.user(ADMIN_ID))
async def channel_id_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "channel_id_awaiting_message"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে একটি চ্যানেল থেকে একটি মেসেজ এখানে ফরওয়ার্ড করুন।**")
    
@app.on_message(filters.forwarded & filters.private & filters.user(ADMIN_ID))
async def forwarded_message_handler(client, message):
    user_id = message.from_user.id
    if user_id in user_states and user_states[user_id].get("command") == "channel_id_awaiting_message":
        if message.forward_from_chat:
            channel_id = message.forward_from_chat.id
            await message.reply_text(f"✅ **Channel ID:** `{channel_id}`", parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text("❌ **এটি একটি চ্যানেল মেসেজ নয়।**")
        del user_states[user_id]
        save_data()


# --- Run Services ---
def run_flask_and_pyrogram():
    connect_to_mongodb()
    load_data()
    flask_thread = threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=PORT))
    flask_thread.daemon = True
    flask_thread.start()
    
    ping_thread = threading.Thread(target=ping_service)
    ping_thread.daemon = True
    ping_thread.start()

    print("Bot is starting...")
    app.run()

if __name__ == "__main__":
    run_flask_and_pyrogram()
