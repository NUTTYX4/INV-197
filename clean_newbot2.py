# --- ADD THESE 3 LINES AT THE VERY TOP ---
import os
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
# -----------------------------------------

import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from telegram.request import HTTPXRequest
import subprocess
import logging
import signal
import csv
import asyncio
import json
import psutil
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from telegram.error import TimedOut, NetworkError

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
BOT_TOKEN = "8280400805:AAFY5aIJ9jrPUi6G4YhwhUbXbDPi1EMMQDc" 
AUTHORIZED_USER_ID = 5633775788  # Your Admin ID
BASE_PATH = "/home/vinod"
SCRIPT_TO_RUN = os.path.join(BASE_PATH, "fix2.py")
LOG_FILE = os.path.join(BASE_PATH, "access_log.csv")
MEMBER_FILE = os.path.join(BASE_PATH, "members.json")
HIVE_DATA_FILE = os.path.join(BASE_PATH, "hive_update.txt")

# Constants for Public Channel
PUBLIC_CHANNEL_LINK = "https://t.me/MyHiveAlerts"
TELEGRAM_LOG_CHANNEL = "@MyHiveAlerts"

# ---------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Global process holder
hive_process = None

# Global Runner Tracking (For Locking)
RUNNER_ID = None
RUNNER_NAME = None

# ---------------------------------------------------------
# MEMBER MANAGEMENT HELPERS
# ---------------------------------------------------------

def load_members():
    if not os.path.exists(MEMBER_FILE):
        return {}
    with open(MEMBER_FILE, 'r') as f:
        return json.load(f)

def save_members(members):
    with open(MEMBER_FILE, 'w') as f:
        json.dump(members, f, indent=4)

def is_member(user_id):
    members = load_members()
    return str(user_id) in members

# ---------------------------------------------------------
# SYSTEM CHECK HELPERS
# ---------------------------------------------------------

def get_pi_health():
    """Retrieves system health stats (temp, disk, memory usage)."""
    try:
        temp_output = subprocess.check_output(['/usr/bin/vcgencmd', 'measure_temp']).decode('utf-8')
        cpu_temp = temp_output.split('=')[1].strip().replace('\'C', '°C')
    except (FileNotFoundError, subprocess.CalledProcessError):
        try:
            temp_data = psutil.sensors_temperatures().get('coretemp', [None])
            cpu_temp = f"{temp_data[0].current:.1f}°C" if temp_data and temp_data[0] else "N/A"
        except Exception:
            cpu_temp = "N/A"
    
    disk_usage = psutil.disk_usage('/')
    disk_total_gb = round(disk_usage.total / (1024**3), 2)
    disk_used_gb = round(disk_usage.used / (1024**3), 2)
    disk_percent = disk_usage.percent

    memory = psutil.virtual_memory()
    mem_total_gb = round(memory.total / (1024**3), 2)
    mem_used_gb = round(memory.used / (1024**3), 2)
    mem_percent = memory.percent
    
    return (
        f"🌡️ **CPU Temp:** {cpu_temp}\n"
        f"💾 **Disk Usage:** {disk_used_gb} GB / {disk_total_gb} GB ({disk_percent}%)\n"
        f"🧠 **Memory Usage:** {mem_used_gb} GB / {mem_total_gb} GB ({mem_percent}%)"
    )

# ---------------------------------------------------------
# HELPER: CSV LOGGING & ALERTS
# ---------------------------------------------------------
async def log_and_alert(user, context: ContextTypes.DEFAULT_TYPE):
    user_id = user.id
    username = user.username or "No Username"
    full_name = user.full_name
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    is_admin = user_id == AUTHORIZED_USER_ID
    is_auth_member = is_admin or is_member(user_id)
    status = "ADMIN" if is_admin else ("MEMBER" if is_member(user_id) else "UNAUTHORIZED")

    file_exists = os.path.isfile(LOG_FILE)
    try:
        with open(LOG_FILE, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Timestamp", "User ID", "Name", "Username", "Status"])
            writer.writerow([timestamp, user_id, full_name, username, status])
    except Exception as e:
        logger.error(f"Failed to log to CSV: {e}")

    if not is_auth_member:
        alert_msg = (
            f" 🚨 *NEW UNAUTHORIZED ACCESS ATTEMPT*\n\n"
            f" *User:* {full_name}\n"
            f" *ID:* `{user_id}`\n"
            f" *Username:* @{username}\n"
            f" *Time:* {timestamp}"
        )
        kb = [[InlineKeyboardButton("✅ Approve Member", callback_data=f'approve_{user_id}_{username}')]]
        reply_markup = InlineKeyboardMarkup(kb)

        try:
            await context.bot.send_message(
                chat_id=AUTHORIZED_USER_ID, text=alert_msg, reply_markup=reply_markup, parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Could not send approval alert to admin: {e}")

    return is_auth_member, is_admin

# ---------------------------------------------------------
# PERMISSIONS DECORATORS
# ---------------------------------------------------------

def restricted(func):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != AUTHORIZED_USER_ID:
            if update.message:
                 await update.message.reply_text(" 🛑 Admin only access.")
            elif update.callback_query:
                 await update.callback_query.answer("🛑 Admin only access.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

def member_required(func):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != AUTHORIZED_USER_ID and not is_member(user_id):
            if update.message:
                await update.message.reply_text(" 🛑 Access Denied. Only approved members can send commands.", parse_mode='Markdown')
            elif update.callback_query:
                await update.callback_query.answer("🛑 Access Denied. Only approved members can send commands.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# ---------------------------------------------------------
# LOG HANDLER
# ---------------------------------------------------------
@restricted
async def handle_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not os.path.exists(LOG_FILE):
        await query.answer(" 📄 No logs recorded yet.", show_alert=True)
        return

    await query.answer(" Uploading logs...")
    try:
        await context.bot.send_document(
            chat_id=AUTHORIZED_USER_ID,
            document=open(LOG_FILE, 'rb'),
            caption=" *Access Logs*",
            parse_mode='Markdown'
        )
    except Exception as e:
        await query.edit_message_text(f" ❌ Error uploading logs: {e}")

# ---------------------------------------------------------
# MAIN MENU (START)
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    is_auth_member, is_admin = await log_and_alert(user, context)

    if not is_auth_member:
        await update.message.reply_text(" 🛑 *Access Denied.*\nYour access request has been forwarded to the administrator.", parse_mode='Markdown')
        return

    context.user_data['awaiting_calibration'] = False
    status_icon = "🟢" if (hive_process and hive_process.poll() is None) else "🔴"
    
    shared_row_1 = [
        InlineKeyboardButton(" 📄 Check Status", callback_data='status_check'),
        InlineKeyboardButton(" 🔗 Join Channel", url=PUBLIC_CHANNEL_LINK)
    ]
    shared_row_2 = [
        InlineKeyboardButton(" 📊 Hive Data (TXT)", callback_data='download_data_csv'),
        InlineKeyboardButton(" ⚙️ Check Pi Health", callback_data='check_pi_health') 
    ]
    
    if not is_admin:
        keyboard = [
            [InlineKeyboardButton(f"{status_icon} Start Hive Monitor", callback_data='start_init_member')],
            [InlineKeyboardButton(" 🛑 Stop Monitor", callback_data='stop_script_member')],
            shared_row_1,
            shared_row_2,
        ]
        msg_title = " *Member Command Center*"
    
    else:
        keyboard = [
            [InlineKeyboardButton(f"{status_icon} Start Hive Monitor", callback_data='start_init')],
            [InlineKeyboardButton(" 🛑 Stop Monitor", callback_data='stop_script')],
            shared_row_1,
            shared_row_2,
            [InlineKeyboardButton(" 🗂️ File Manager", callback_data='files_home'),
             InlineKeyboardButton(" 💻 Terminal Mode", callback_data='toggle_terminal')],
            [InlineKeyboardButton(" ⬇️ Download Access Logs", callback_data='download_logs'),
             InlineKeyboardButton(" 👥 Member Management", callback_data='members_list')]
        ]
        msg_title = " *Admin Command Center*"
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = msg_title + "\n\nUse the buttons below to control the monitoring script on your Raspberry Pi."
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

# ---------------------------------------------------------
# MEMBER APPROVAL HANDLERS
# ---------------------------------------------------------
@restricted
async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, user_id_str, username = query.data.split('_')
    user_id = int(user_id_str)
    
    members = load_members()
    
    if str(user_id) in members:
        await query.answer("User already approved.", show_alert=True)
        return
    
    members[str(user_id)] = {'username': username, 'approved_by': query.from_user.username, 'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    save_members(members)
    
    await query.edit_message_text(f"✅ User @{username} approved.", parse_mode='Markdown')
    try:
        await context.bot.send_message(chat_id=user_id, text="🎉 Approved! Send /start to begin.", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to notify member: {e}")

@restricted
async def member_management_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    members = load_members()
    keyboard = []
    
    if members:
        for uid, data in members.items():
            name = data.get('username', 'Unknown')
            keyboard.append([InlineKeyboardButton(f"🗑️ Remove @{name}", callback_data=f'remove_{uid}')])
    else:
        keyboard.append([InlineKeyboardButton("No members found.", callback_data='noop')])
        
    keyboard.append([InlineKeyboardButton(" ⬅️ Back", callback_data='main_menu')])
    await query.edit_message_text("👥 *Member Management*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

@restricted
async def remove_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.data.split('_')[1]
    members = load_members()
    if uid in members:
        del members[uid]
        save_members(members)
        await query.answer("Member removed.", show_alert=True)
    else:
        await query.answer("Member not found.", show_alert=True)
    await member_management_menu(update, context)

# ---------------------------------------------------------
# DATA & STATUS HANDLERS
# ---------------------------------------------------------
async def check_pi_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Checking health...", show_alert=True)
    info = get_pi_health()
    kb = [[InlineKeyboardButton(" 🏠 Menu", callback_data='main_menu')]]
    await query.edit_message_text(f"⚙️ *System Health*\n\n{info}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    
async def download_data_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not os.path.exists(HIVE_DATA_FILE):
        await query.answer("❌ No data file yet.", show_alert=True)
        return
    await query.answer("Uploading...", show_alert=True)
    try:
        await context.bot.send_document(
            chat_id=query.from_user.id,
            document=open(HIVE_DATA_FILE, 'rb'),
            filename=f"hive_data_{datetime.now().strftime('%Y%m%d')}.txt",
            caption="📊 **Hive Sensor Log**",
            parse_mode='Markdown'
        )
    except Exception as e:
        await context.bot.send_message(chat_id=query.from_user.id, text=f"❌ Error: {e}")

# ---------------------------------------------------------
# SCRIPT CONTROL HANDLERS
# ---------------------------------------------------------
async def manage_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global hive_process, RUNNER_ID, RUNNER_NAME
    query = update.callback_query
    action = query.data

    # Standardize actions
    if action == 'start_init_member': action = 'start_init'
    if action == 'stop_script_member': action = 'stop_script'

    uid = query.from_user.id
    
    # Permission Check
    if action in ['start_init', 'stop_script']:
        if uid != AUTHORIZED_USER_ID and not is_member(uid):
            await query.answer("🛑 Access Denied.", show_alert=True)
            return

    if action == 'start_init':
        # LOCK CHECK: If running, check ownership
        if hive_process and hive_process.poll() is None:
            status_msg = f"🔴 Script is already running by {RUNNER_NAME}!"
            await query.answer(status_msg, show_alert=True)
            return
        
        user = query.from_user
        context.user_data['starting_user_info'] = f"{user.full_name} (@{user.username or user.id})"
        context.user_data['starting_user_id'] = str(user.id)
        context.user_data['awaiting_calibration'] = True
        await query.edit_message_text(" ⚖️ *Calibration*\n\nType known weight (g).\nExample: `212`", parse_mode='Markdown')

    elif action == 'stop_script':
        if hive_process and hive_process.poll() is None:
            # LOCK CHECK: Only Admin OR the original Runner can stop
            is_admin = (uid == AUTHORIZED_USER_ID)
            is_owner = (uid == RUNNER_ID)
            
            if not (is_admin or is_owner):
                await query.answer(f"🛑 Access Denied. Script started by {RUNNER_NAME}. Only they or Admin can stop it.", show_alert=True)
                return

            hive_process.send_signal(signal.SIGTERM) 
            hive_process.wait(timeout=5)
            hive_process = None
            
            # If Admin force-stopped someone else's script, notify the channel
            if is_admin and not is_owner and RUNNER_NAME:
                force_stop_msg = f"⚠️ *Admin Override:* Monitoring script started by {RUNNER_NAME} was force-stopped by Admin."
                try:
                    await context.bot.send_message(chat_id=TELEGRAM_LOG_CHANNEL, text=force_stop_msg, parse_mode='Markdown')
                except Exception:
                    pass # Fallback if channel send fails

            # Reset Runner Stats
            RUNNER_ID = None
            RUNNER_NAME = None
            
            await context.bot.send_message(query.from_user.id, "🛑 *Monitor Stopped.*", parse_mode='Markdown')
            await query.edit_message_text(" 🛑 *Script Stopped safely.*", parse_mode='Markdown')
        else:
            await query.edit_message_text(" ℹ️ Script was not running.")
            
        kb = [[InlineKeyboardButton(" 🏠 Menu", callback_data='main_menu')]]
        await context.bot.send_message(chat_id=query.from_user.id, text="Options:", reply_markup=InlineKeyboardMarkup(kb))

    elif action == 'status_check':
        if hive_process and hive_process.poll() is None:
            status = f"🟢 Running (Started by: {RUNNER_NAME})"
        else:
            status = "🔴 Stopped"
        await query.answer(f"System Status: {status}", show_alert=True)

# ---------------------------------------------------------
# FILE MANAGER (ADMIN ONLY) - ENHANCED
# ---------------------------------------------------------
@restricted
async def file_manager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if 'current_path' not in context.user_data: 
        context.user_data['current_path'] = BASE_PATH
    
    current_path = context.user_data['current_path']

    if data == 'files_home': 
        current_path = BASE_PATH
    elif data.startswith('nav_'):
        target = data.split('nav_', 1)[1]
        
        if target == '..': 
            current_path = os.path.dirname(current_path)
        else:
            new_path = os.path.join(current_path, target)
            if os.path.isdir(new_path): 
                current_path = new_path
            elif os.path.isfile(new_path):
                context.user_data['selected_file'] = new_path
                # ENHANCED ACTIONS MENU
                kb = [
                    [InlineKeyboardButton(" 👀 Read Content", callback_data='file_read')],
                    [InlineKeyboardButton(" ⬇️ Download", callback_data='file_download'),
                     InlineKeyboardButton(" 🗑️ Delete", callback_data='file_delete')],
                    [InlineKeyboardButton(" ⬅️ Back", callback_data='files_refresh')]
                ]
                await query.edit_message_text(f" 📄 *File:* `{target}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
                return

    context.user_data['current_path'] = current_path
    
    try:
        items = os.listdir(current_path)
        items.sort(key=lambda x: (not os.path.isdir(os.path.join(current_path, x)), x.lower()))
        
        keyboard = []
        if len(current_path) > 1: 
            keyboard.append([InlineKeyboardButton(" ⬆️ Up Folder", callback_data='nav_..')])
            
        for item in items[:20]: # Limit to 20 items for readability
            full_item_path = os.path.join(current_path, item)
            icon = "📁" if os.path.isdir(full_item_path) else "📄"
            keyboard.append([InlineKeyboardButton(f"{icon} {item}", callback_data=f"nav_{item}")])
            
        keyboard.append([InlineKeyboardButton(" ❌ Close", callback_data='main_menu')])
        
        await query.edit_message_text(f" 🗂️ *Path:* `{current_path}`", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except PermissionError:
        await query.answer("🚫 Permission Denied", show_alert=True)
        context.user_data['current_path'] = os.path.dirname(current_path)
        await file_manager(update, context)
    except Exception as e:
        await query.edit_message_text(f" ❌ Error: {e}")

@restricted
async def file_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action = query.data
    file_path = context.user_data.get('selected_file')

    if action == 'file_read':
        await query.answer("Reading file...", show_alert=True)
        try:
            # Check size first
            file_size = os.path.getsize(file_path)
            if file_size > 100 * 1024: # 100KB limit
                await context.bot.send_message(chat_id=query.from_user.id, text="⚠️ File too large to read directly. Please download.", parse_mode='Markdown')
                return

            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                # Read last 3000 chars to fit Telegram message limit
                f.seek(0, 2) # Seek to end
                size = f.tell()
                f.seek(max(0, size - 3000), 0)
                content = f.read()
            
            filename = os.path.basename(file_path)
            msg = f"📄 *Reading: {filename} (Last 3000 chars)*\n\n```\n{content}\n```"
            await context.bot.send_message(chat_id=query.from_user.id, text=msg, parse_mode='Markdown')
            
        except Exception as e:
            await context.bot.send_message(chat_id=query.from_user.id, text=f"❌ Read Error: {e}")

    elif action == 'file_download':
        await query.answer("Uploading...")
        try: await context.bot.send_document(chat_id=AUTHORIZED_USER_ID, document=open(file_path, 'rb'))
        except Exception as e: await query.edit_message_text(f"❌ Error: {e}")
    
    elif action == 'file_delete':
        try:
            os.remove(file_path)
            await query.edit_message_text(f" 🗑️ Deleted `{os.path.basename(file_path)}`.", parse_mode='Markdown')
            await asyncio.sleep(1.5)
            await file_manager(update, context)
        except Exception as e: await query.edit_message_text(f"❌ Error: {e}")
        
    elif action == 'files_refresh':
        await file_manager(update, context)

@restricted
async def toggle_terminal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    state = not context.user_data.get('terminal_mode', False)
    context.user_data['terminal_mode'] = state
    status = "ENABLED 🟢" if state else "DISABLED 🔴"
    await query.edit_message_text(f" 💻 *Terminal Mode: {status}*", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(" 🏠 Menu", callback_data='main_menu')]]), parse_mode='Markdown')

@member_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global hive_process, RUNNER_ID, RUNNER_NAME

    if context.user_data.get('awaiting_calibration'):
        weight_input = update.message.text.strip()
        user_name = context.user_data.get('starting_user_info', 'Unknown')
        user_id_str = context.user_data.get('starting_user_id', 'N/A')
        
        try:
            float(weight_input) 
            
            hive_process = subprocess.Popen(
                ['python3', SCRIPT_TO_RUN, weight_input, user_name, user_id_str],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=BASE_PATH
            )
            
            # SET RUNNER GLOBALS FOR LOCKING
            RUNNER_ID = update.effective_user.id
            RUNNER_NAME = user_name.split('(')[0].strip()
            
            msg = await update.message.reply_text(f" ⚖️ Setting `{weight_input}`g...\n\n 🚨 *ACTION REQUIRED*\nPlace weight on scale!", parse_mode='Markdown')

            for i in range(10, 0, -1):
                await asyncio.sleep(1) 
                icon = "⏳" if i > 5 else "⚠️"
                try: await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f" ⚖️ Setting `{weight_input}`g...\n\n{icon} *ACTION REQUIRED* {icon}\nPlace weight on scale!\n\n *Time: {i}s*", parse_mode='Markdown')
                except Exception: pass 

            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f" ✅ *Calibration Complete!*\n\nMonitor RUNNING.", parse_mode='Markdown')
            context.user_data['awaiting_calibration'] = False
        
        except ValueError: await update.message.reply_text(" ❌ Invalid number.")
        except Exception as e:
            err_msg = f"{e}\n{hive_process.stderr.read()[:100]}" if hive_process else str(e)
            await update.message.reply_text(f" ❌ Failed: {err_msg}")
            hive_process = None
        return

    # Admin Functions
    if update.effective_user.id == AUTHORIZED_USER_ID:
        if update.message.document:
            f = await update.message.document.get_file()
            path = os.path.join(context.user_data.get('current_path', BASE_PATH), update.message.document.file_name)
            await f.download_to_drive(path)
            await update.message.reply_text(f" 💾 Saved: `{path}`", parse_mode='Markdown')
            return

        if context.user_data.get('terminal_mode'):
            try:
                output = subprocess.check_output(update.message.text, shell=True, stderr=subprocess.STDOUT, timeout=10).decode('utf-8')
                if len(output) > 4000: output = output[:4000] + "..."
                await update.message.reply_text(f"```\n{output or 'Done.'}\n```", parse_mode='Markdown')
            except Exception as e: await update.message.reply_text(f" ❌ Error:\n`{e}`", parse_mode='Markdown')
    else:
        await update.message.reply_text(" 🛑 Admin only command.")

def main():
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(start, pattern='^(main_menu)$'))
    app.add_handler(CallbackQueryHandler(handle_approval, pattern='^approve_'))
    app.add_handler(CallbackQueryHandler(member_management_menu, pattern='^members_list$'))
    app.add_handler(CallbackQueryHandler(remove_member, pattern='^remove_'))
    app.add_handler(CallbackQueryHandler(download_data_csv, pattern='^download_data_csv$'))
    app.add_handler(CallbackQueryHandler(check_pi_health, pattern='^check_pi_health$'))
    app.add_handler(CallbackQueryHandler(manage_script, pattern='^(start_init|stop_script|status_check|start_init_member|stop_script_member)$'))
    app.add_handler(CallbackQueryHandler(file_manager, pattern='^(files_home|nav_|files_refresh)$'))
    app.add_handler(CallbackQueryHandler(file_actions, pattern='^(file_download|file_delete|file_read)$')) # Added file_read
    app.add_handler(CallbackQueryHandler(toggle_terminal, pattern='^toggle_terminal$'))
    app.add_handler(CallbackQueryHandler(handle_logs, pattern='^download_logs$')) 
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    
    async def error_handler(update, context):
        if isinstance(context.error, (TimedOut, NetworkError)):
            logger.warning(f"Network Error caught: {context.error}. Retrying.")
            return
        logger.error("Unhandled exception:", exc_info=context.error)

    app.add_error_handler(error_handler)
    
    app.run_polling()

if __name__ == '__main__':
    if not os.path.exists(MEMBER_FILE): save_members({})
    main()
