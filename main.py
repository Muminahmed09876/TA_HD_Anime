import os
import asyncio
import time
import threading
import re
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified, FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
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

# --- Join Channels Configuration ---
# ব্যবহারকারীদের বাধ্যতামূলকভাবে জয়েন করতে হবে এমন চ্যানেল
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
# বটকে সচল রাখার জন্য একটি ছোট ওয়েব সার্ভার
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

# ডেটাবেস থেকে ডেটা লোড
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

# পেজিনেশন সহ বোতাম তৈরি করা (পরিবর্তিত)
def create_paged_buttons(keyword, button_list, page, page_size=10):
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    current_page_buttons = button_list[start_index:end_index]
    
    keyboard = []
    
    for button_data in current_page_buttons:
        keyboard.append([InlineKeyboardButton(button_data['text'], url=button_data['link'])])

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

# টেক্সট থেকে ইনলাইন বোতামের ডেটা পার্স করা
def parse_inline_buttons_from_text(text):
    button_data = []
    button_pairs = text.split(',')
    
    for pair in button_pairs:
        parts = pair.split(' - ', 1)
        if len(parts) == 2:
            button_text = parts[0].strip()
            button_link = parts[1].strip()
            button_data.append({'text': button_text, 'link': button_link})
            
    return button_data

# এডিট করার জন্য বোতামের কীবোর্ড তৈরি করা
def create_edit_buttons_keyboard(keyword, button_list):
    keyboard = []
    
    for i, button_data in enumerate(button_list, start=1):
        keyboard.append([InlineKeyboardButton(f"{i}. {button_data['text']}", callback_data="ignore")])
    
    edit_row = [
        InlineKeyboardButton("➕ Add", callback_data=f"edit_add_{keyword}"),
        InlineKeyboardButton("🗑️ Delete", callback_data=f"edit_delete_{keyword}"),
        InlineKeyboardButton("↔️ Set", callback_data=f"edit_set_{keyword}")
    ]
    
    keyboard.append(edit_row)
    
    return InlineKeyboardMarkup(keyboard)

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
                    sent_msg = await app.copy_message(message.chat.id, CHANNEL_ID, file_id, protect_content=restrict_status)
                    sent_message_ids.append(sent_msg.id)
                    await asyncio.sleep(0.5)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    sent_msg = await app.copy_message(message.chat.id, CHANNEL_ID, file_id, protect_content=restrict_status)
                    sent_message_ids.append(sent_msg.id)
                except Exception as e:
                    print(f"Error copying message {file_id}: {e}")
            await message.reply_text("🎉 **All files sent!**")
            if autodelete_time > 0:
                asyncio.create_task(delete_messages_later(message.chat.id, sent_message_ids, autodelete_time))
        else:
            await message.reply_text("❌ **No files or buttons found for this keyword.**")
        
        return
    
    if user_id == ADMIN_ID:
        admin_commands = (
            "🌟 **Welcome, Admin! Here are your commands:**\n\n"
            "**/button** - Start the interactive process to create a button filter.\n"
            "**/edit_button** - Edit an existing button filter.\n"
            "**/broadcast** - Reply to a message with this command to broadcast it.\n"
            "**/delete <keyword>** - Delete a filter and its associated files.\n"
            "**/restrict** - Toggle message forwarding restriction (ON/OFF).\n"
            "**/ban <user_id>** - Ban a user.\n"
            "**/unban <user_id>** - Unban a user.\n"
            "**/auto_delete <time>** - Set auto-delete time for files (e.g., 30m, 1h, off).\n"
            "**/channel_id** - Get the ID of a channel by forwarding a message from it."
        )
        await message.reply_text(admin_commands, parse_mode=ParseMode.MARKDOWN)
    else:
        await message.reply_text("👋 **Welcome!** You can access files via special links.")

# /button কমান্ড হ্যান্ডলার
@app.on_message(filters.command("button") & filters.private & filters.user(ADMIN_ID))
async def button_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "button_awaiting_name"}
    save_data()
    await message.reply_text("➡️ **ফিল্টারের জন্য একটি নাম দিন:**", reply_markup=ForceReply(True))

# /edit_button কমান্ড হ্যান্ডলার
@app.on_message(filters.command("edit_button") & filters.private & filters.user(ADMIN_ID))
async def edit_button_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "edit_button_awaiting_name"}
    save_data()
    await message.reply_text("➡️ **কোন বোতাম ফিল্টারটি এডিট করতে চান তার নাম দিন:**", reply_markup=ForceReply(True))

# রিপ্লাই মেসেজ হ্যান্ডলার (নতুন লজিক সহ)
@app.on_message(filters.private & filters.user(ADMIN_ID) & filters.reply)
async def reply_handler(client, message):
    user_id = message.from_user.id
    state = user_states.get(user_id)
    
    if not state:
        return
    
    if state["command"] == "button_awaiting_name":
        keyword = message.text.lower().strip()
        if keyword in filters_dict:
            return await message.reply_text("⚠️ **এই নামে একটি ফিল্টার ইতিমধ্যে আছে।** অনুগ্রহ করে অন্য একটি নাম দিন:", reply_markup=ForceReply(True))

        user_states[user_id] = {"command": "button_awaiting_buttons", "keyword": keyword}
        save_data()
        await message.reply_text("➡️ **বোতামের কোড দিন (যেমন: Button 01 - link1, Button 02 - link2):**", reply_markup=ForceReply(True))

    elif state["command"] == "button_awaiting_buttons":
        keyword = state["keyword"]
        button_text = message.text.strip()
        button_data = parse_inline_buttons_from_text(button_text)
        
        if not button_data:
            return await message.reply_text("❌ **ভুল বোতাম ফরম্যাট।** অনুগ্রহ করে আবার চেষ্টা করুন:", reply_markup=ForceReply(True))

        filters_dict[keyword] = {
            'message_text': "Select a button from the list below:",
            'button_data': button_data,
            'file_ids': [],
            'type': 'button_filter'
        }

        try:
            await app.send_message(
                CHANNEL_ID,
                f"#{keyword}\n[button (বোতাম ফিল্টার)]"
            )
        except Exception as e:
            await message.reply_text(f"❌ **চ্যানেলে সেভ করতে সমস্যা হয়েছে:** {e}")

        await message.reply_text(
            f"✅ **বোতাম ফিল্টার '{keyword}' সফলভাবে তৈরি হয়েছে।**\n🔗 শেয়ার লিংক: `https://t.me/{(await client.get_me()).username}?start={keyword}`",
            parse_mode=ParseMode.MARKDOWN
        )

        del user_states[user_id]
        save_data()
        
    elif state["command"] == "edit_button_awaiting_name":
        keyword = message.text.lower().strip()
        if keyword not in filters_dict or filters_dict[keyword].get('type') != 'button_filter':
            return await message.reply_text("⚠️ **এই নামে কোনো বোতাম ফিল্টার পাওয়া যায়নি।** অনুগ্রহ করে সঠিক নাম দিন:", reply_markup=ForceReply(True))
        
        user_states[user_id] = {"command": "editing_buttons", "keyword": keyword}
        save_data()
        
        button_list = filters_dict[keyword]['button_data']
        reply_text = f"⚙️ **'{keyword}' ফিল্টার এডিট করছেন।**\n\n**বর্তমান বোতামগুলো:**\n\n"
        for i, button_data in enumerate(button_list, 1):
            reply_text += f"{i}. **{button_data['text']}** - `{button_data['link']}`\n"
        
        await message.reply_text(reply_text, reply_markup=create_edit_buttons_keyboard(keyword, button_list), parse_mode=ParseMode.MARKDOWN)

    elif state["command"] == "edit_add_awaiting_input":
        keyword = state['keyword']
        button_text = message.text.strip()
        new_buttons = parse_inline_buttons_from_text(f"dummy - link, {button_text}")
        new_buttons.pop(0) # Remove the dummy button

        if not new_buttons:
            return await message.reply_text("❌ **ভুল বোতাম ফরম্যাট।** অনুগ্রহ করে আবার চেষ্টা করুন।", reply_markup=ForceReply(True))

        filters_dict[keyword]['button_data'].extend(new_buttons)
        save_data()
        await message.reply_text("✅ **নতুন বোতাম সফলভাবে যুক্ত হয়েছে!**")
        
        del user_states[user_id]
        await edit_button_cmd(client, message)

    elif state["command"] == "edit_delete_awaiting_number":
        keyword = state['keyword']
        numbers_str = message.text.strip().split(',')
        numbers_to_delete = [int(n.strip()) for n in numbers_str if n.strip().isdigit()]
        
        if not numbers_to_delete:
            return await message.reply_text("❌ **ভুল সংখ্যা।** অনুগ্রহ করে একটি বা একাধিক বোতাম নম্বর দিন (যেমন: 5, 4, 2):", reply_markup=ForceReply(True))

        button_list = filters_dict[keyword]['button_data']
        deleted_count = 0
        
        numbers_to_delete.sort(reverse=True)
        for num in numbers_to_delete:
            index = num - 1
            if 0 <= index < len(button_list):
                del button_list[index]
                deleted_count += 1
        
        save_data()
        await message.reply_text(f"✅ **{deleted_count}টি বোতাম সফলভাবে মুছে ফেলা হয়েছে।**")
        
        del user_states[user_id]
        await edit_button_cmd(client, message)

    elif state["command"] == "edit_set_awaiting_numbers":
        keyword = state['keyword']
        numbers_str = message.text.strip().split('-')
        if len(numbers_str) != 2:
            return await message.reply_text("❌ **ভুল ফরম্যাট।** অনুগ্রহ করে দুটি সংখ্যা দিন (যেমন: 2-3):", reply_markup=ForceReply(True))

        try:
            num1 = int(numbers_str[0].strip()) - 1
            num2 = int(numbers_str[1].strip()) - 1
        except ValueError:
            return await message.reply_text("❌ **ভুল সংখ্যা।** অনুগ্রহ করে দুটি সংখ্যা দিন:", reply_markup=ForceReply(True))
        
        button_list = filters_dict[keyword]['button_data']
        if not (0 <= num1 < len(button_list) and 0 <= num2 < len(button_list)):
            return await message.reply_text("⚠️ **অকার্যকর বোতাম নম্বর।**")

        button_list[num1], button_list[num2] = button_list[num2], button_list[num1]
        save_data()
        await message.reply_text(f"✅ **বোতাম {num1+1} এবং {num2+1} এর স্থান পরিবর্তন করা হয়েছে।**")
        
        del user_states[user_id]
        await edit_button_cmd(client, message)

# চ্যানেল মেসেজ হ্যান্ডলার (শুধুমাত্র ফাইল ফিল্টার তৈরির জন্য)
@app.on_message(filters.channel & filters.chat(CHANNEL_ID))
async def channel_content_handler(client, message):
    global last_filter
    
    if message.text and len(message.text.split()) == 1:
        keyword = message.text.lower().replace('#', '')
        if not keyword:
            return
        
        if keyword in filters_dict and filters_dict[keyword].get('type') == 'button_filter':
            await app.send_message(LOG_CHANNEL_ID, f"⚠️ **Filter '{keyword}' is a button filter. Files cannot be added to it.**")
            return
            
        last_filter = keyword
        if keyword not in filters_dict:
            filters_dict[keyword] = {'message_text': None, 'button_data': [], 'file_ids': []}
            await app.send_message(
                LOG_CHANNEL_ID,
                f"✅ **নতুন ফাইল ফিল্টার তৈরি হয়েছে!**\n🔗 শেয়ার লিংক: `https://t.me/{(await app.get_me()).username}?start={keyword}`",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await app.send_message(LOG_CHANNEL_ID, f"⚠️ **ফিল্টার '{keyword}' ইতিমধ্যে বিদ্যমান।**")
        save_data()
        return

    if message.media and last_filter:
        if last_filter in filters_dict and filters_dict[last_filter].get('type') != 'button_filter':
            if 'file_ids' not in filters_dict[last_filter]:
                filters_dict[last_filter]['file_ids'] = []
            filters_dict[last_filter]['file_ids'].append(message.id)
            save_data()
        else:
            await app.send_message(LOG_CHANNEL_ID, "⚠️ **কোনো সক্রিয় ফাইল ফিল্টার পাওয়া যায়নি বা এটি একটি বোতাম ফিল্টার।**")

# চ্যানেল থেকে মেসেজ ডিলিট করার হ্যান্ডলার
@app.on_deleted_messages(filters.channel & filters.chat(CHANNEL_ID))
async def channel_delete_handler(client, messages):
    global last_filter
    for message in messages:
        if message.text:
            keyword = message.text.lower().replace('#', '').strip()
            if keyword in filters_dict:
                del filters_dict[keyword]
                if keyword == last_filter:
                    last_filter = None
                save_data()
                await app.send_message(LOG_CHANNEL_ID, f"🗑️ **ফিল্টার '{keyword}' সফলভাবে মুছে ফেলা হয়েছে।**")
            elif last_filter == keyword:
                last_filter = None
                await app.send_message(LOG_CHANNEL_ID, "📝 **দ্রষ্টব্য:** শেষ সক্রিয় ফিল্টারটি মুছে ফেলা হয়েছে।")
                save_data()

# /broadcast কমান্ড হ্যান্ডলার
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

# /delete কমান্ড হ্যান্ডলার
@app.on_message(filters.command("delete") & filters.private & filters.user(ADMIN_ID))
async def delete_cmd(client, message):
    global last_filter
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **Please provide a keyword to delete.**")
    keyword = args[1].lower()
    if keyword in filters_dict:
        del filters_dict[keyword]
        if keyword == last_filter:
            last_filter = None
        
        save_data()
        
        await message.reply_text(f"🗑️ **Filter '{keyword}' has been deleted from the database.**")
    else:
        await message.reply_text(f"❌ **Filter '{keyword}' not found.**")

# /restrict কমান্ড হ্যান্ডলার
@app.on_message(filters.command("restrict") & filters.private & filters.user(ADMIN_ID))
async def restrict_cmd(client, message):
    global restrict_status
    restrict_status = not restrict_status
    save_data()
    status_text = "ON" if restrict_status else "OFF"
    await message.reply_text(f"🔒 **Message forwarding restriction is now {status_text}.**")
    
# /ban কমান্ড হ্যান্ডলার
@app.on_message(filters.command("ban") & filters.private & filters.user(ADMIN_ID))
async def ban_cmd(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **Usage:** `/ban <user_id>`", parse_mode=ParseMode.MARKDOWN)
    try:
        user_id_to_ban = int(args[1])
        if user_id_to_ban in banned_users:
            return await message.reply_text("⚠️ **This user is already banned.**")
        banned_users.add(user_id_to_ban)
        save_data()
        await message.reply_text(f"✅ **User `{user_id_to_ban}` has been banned.**", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await message.reply_text("❌ **Invalid User ID.**")

# /unban কমান্ড হ্যান্ডলার
@app.on_message(filters.command("unban") & filters.private & filters.user(ADMIN_ID))
async def unban_cmd(client, message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **Usage:** `/unban <user_id>`", parse_mode=ParseMode.MARKDOWN)
    try:
        user_id_to_unban = int(args[1])
        if user_id_to_unban not in banned_users:
            return await message.reply_text("⚠️ **This user is not banned.**")
        banned_users.remove(user_id_to_unban)
        save_data()
        await message.reply_text(f"✅ **User `{user_id_to_unban}` has been unbanned.**", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await message.reply_text("❌ **Invalid User ID.**")

# /auto_delete কমান্ড হ্যান্ডলার
@app.on_message(filters.command("auto_delete") & filters.private & filters.user(ADMIN_ID))
async def auto_delete_cmd(client, message):
    global autodelete_time
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("📌 **ব্যবহার:** `/auto_delete <time>`")
    time_str = args[1].lower()
    time_map = {'30m': 1800, '1h': 3600, '12h': 43200, '24h': 86400, 'off': 0}
    if time_str not in time_map:
        return await message.reply_text("❌ **ভুল সময় বিকল্প।**")
    autodelete_time = time_map[time_str]
    save_data()
    if autodelete_time == 0:
        await message.reply_text(f"🗑️ **অটো-ডিলিট বন্ধ করা হয়েছে।**")
    else:
        await message.reply_text(f"✅ **অটো-ডিলিট {time_str} তে সেট করা হয়েছে।**")

# জয়েন স্ট্যাটাস চেক করার কলব্যাক
@app.on_callback_query(filters.regex("check_join_status"))
async def check_join_status_callback(client, callback_query):
    user_id = callback_query.from_user.id
    await callback_query.answer("Checking membership...", show_alert=True)
    
    if await is_user_member(client, user_id):
        await callback_query.message.edit_text("✅ **You have successfully joined!**\n\n**Please go back to the chat and send your link again.**", parse_mode=ParseMode.MARKDOWN)
    else:
        buttons = []
        for channel in join_channels:
            try:
                await client.get_chat_member(channel['id'], user_id)
            except UserNotParticipant:
                buttons.append([InlineKeyboardButton(f"✅ Join {channel['name']}", url=channel['link'])])
        
        bot_username = (await client.get_me()).username
        try_again_url = f"https://t.me/{bot_username}"

        buttons.append([InlineKeyboardButton("🔄 Try Again", url=try_again_url)])
        keyboard = InlineKeyboardMarkup(buttons)
        await callback_query.message.edit_text("❌ **You are still not a member.**", reply_markup=keyboard)

# পেজিনেশন কলব্যাক হ্যান্ডলার (পরিবর্তিত)
@app.on_callback_query(filters.regex(r"page_([a-zA-Z0-9_]+)_(\d+)"))
async def pagination_callback(client, callback_query):
    query = callback_query
    await query.answer()
    
    parts = query.data.split('_')
    keyword = parts[1]
    page = int(parts[2])

    if keyword in filters_dict:
        filter_data = filters_dict[keyword]
        if 'button_data' in filter_data and filter_data['button_data']:
            reply_text = filter_data.get('message_text', "Select an option:")
            reply_markup = create_paged_buttons(keyword, filter_data['button_data'], page)
            try:
                await query.edit_message_text(reply_text, reply_markup=reply_markup)
            except MessageNotModified:
                pass

# এডিট বোতামের কলব্যাক হ্যান্ডলার
@app.on_callback_query(filters.regex(r"edit_"))
async def edit_button_callback(client, callback_query):
    user_id = callback_query.from_user.id
    query_data = callback_query.data
    
    parts = query_data.split('_')
    action = parts[1]
    keyword = parts[2]
    
    if action == "add":
        user_states[user_id] = {"command": "edit_add_awaiting_input", "keyword": keyword}
        save_data()
        await callback_query.message.reply_text("➡️ **নতুন বোতামের কোড দিন (যেমন: Button 04 - link):**", reply_markup=ForceReply(True))
    
    elif action == "delete":
        user_states[user_id] = {"command": "edit_delete_awaiting_number", "keyword": keyword}
        save_data()
        await callback_query.message.reply_text("➡️ **ডিলিট করতে চান এমন বোতামের নম্বর দিন (কমা দিয়ে একাধিক নম্বর দিতে পারেন, যেমন: 5, 4, 2):**", reply_markup=ForceReply(True))
    
    elif action == "set":
        user_states[user_id] = {"command": "edit_set_awaiting_numbers", "keyword": keyword}
        save_data()
        await callback_query.message.reply_text("➡️ **যে দুটি বোতামের স্থান পরিবর্তন করতে চান তাদের নম্বর দিন (যেমন: 2-3):**", reply_markup=ForceReply(True))

    await callback_query.answer()

# /channel_id কমান্ড হ্যান্ডলার
@app.on_message(filters.command("channel_id") & filters.private & filters.user(ADMIN_ID))
async def channel_id_cmd(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {"command": "channel_id_awaiting_message"}
    save_data()
    await message.reply_text("➡️ **অনুগ্রহ করে একটি চ্যানেল থেকে একটি মেসেজ এখানে ফরওয়ার্ড করুন।**")
    
# ফরওয়ার্ড করা মেসেজ হ্যান্ডলার
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
    flask_thread = threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=PORT, use_reloader=False))
    flask_thread.start()
    ping_thread = threading.Thread(target=ping_service)
    ping_thread.start()
    print("Starting TA File Share Bot...")
    app.run()

if __name__ == "__main__":
    run_flask_and_pyrogram()
