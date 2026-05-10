#Github.com/Vasusen-code

from pyrogram import Client
import pyrogram.utils

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

from decouple import config
import logging, time, sys, traceback

logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
                    level=logging.WARNING)

# variables — safe cast: returns None instead of crashing when env var is unset
_safe_int = lambda x: int(x) if x is not None else None

API_ID = config("API_ID", default=None, cast=_safe_int)
API_HASH = config("API_HASH", default=None)
BOT_TOKEN = config("BOT_TOKEN", default=None)
SESSION = config("SESSION", default=None)
FORCESUB = config("FORCESUB", default=None)
AUTH = config("AUTH", default=None, cast=_safe_int)
SAVE_CHANNEL = config("SAVE_CHANNEL", default=None, cast=_safe_int)  # Channel/group ID where content is saved (for pinning & inline link rewriting)

# ---------------------------------------------------------------------------
# MONKEY-PATCH: Fix Pyrogram's get_peer_type to handle unknown channel IDs
#
# Pyrogram's internal handle_updates() calls resolve_peer() which calls
# get_peer_type(). If the peer isn't in the SQLite session cache,
# get_peer_type() raises ValueError("Peer id invalid: -100XXXXX"), which
# crashes the update handler with an unrecoverable error.
#
# For channel/supergroup IDs (starting with -100), we know the type is
# "channel". This patch returns the correct type instead of crashing.
# ---------------------------------------------------------------------------
_original_get_peer_type = pyrogram.utils.get_peer_type

def _patched_get_peer_type(peer_id):
    """
    Patched version of pyrogram.utils.get_peer_type that doesn't crash
    on channel/supergroup IDs that aren't in the session cache yet.
    
    Channel IDs start with -100 (e.g., -1002173883690).
    If the original function raises ValueError for such IDs, we return
    "channel" instead of crashing.
    """
    try:
        return _original_get_peer_type(peer_id)
    except ValueError:
        # Channel/supergroup IDs: -100XXXXXXXXXX
        if peer_id < -1000000000000:
            return "channel"
        # Group IDs: -XXXXXXXXX  
        elif peer_id < 0:
            return "chat"
        # User IDs: positive
        else:
            return "user"

pyrogram.utils.get_peer_type = _patched_get_peer_type
print("[PATCH] Applied get_peer_type patch — Pyrogram won't crash on unknown channels")

bot = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN) 

userbot = Client("saverestricted", session_string=SESSION, api_hash=API_HASH, api_id=API_ID) 

try:
    userbot.start()
except BaseException as e:
    print("Userbot failed to start. Full error:")
    traceback.print_exc()
    sys.exit(1)

Bot = Client(
    "SaveRestricted",
    bot_token=BOT_TOKEN,
    api_id=API_ID,
    api_hash=API_HASH
)    

try:
    Bot.start()
except Exception as e:
    print("Bot (Pyrogram) failed to start:")
    traceback.print_exc()
    sys.exit(1)

# ---------------------------------------------------------------------------
# Pre-cache all dialogs for the Bot client so Pyrogram's session has the
# access hashes for every channel/group the bot is in. This prevents
# PeerIdInvalid errors when sending/editing messages.
# ---------------------------------------------------------------------------
async def precache_bot_peers():
    """Walk through all bot dialogs to cache peer access hashes."""
    try:
        count = 0
        async for dialog in Bot.get_dialogs():
            count += 1
        print(f"[CACHE] Pre-cached {count} bot dialogs")
    except Exception as e:
        print(f"[CACHE] Warning: Could not pre-cache bot dialogs: {e}")

# Run the pre-caching in the bot's event loop
try:
    import asyncio
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # If the loop is already running (shouldn't be at import time), schedule it
        asyncio.ensure_future(precache_bot_peers())
    else:
        loop.run_until_complete(precache_bot_peers())
except Exception as e:
    print(f"[CACHE] Warning: Pre-cache scheduling failed: {e}")

# Resolve SAVE_CHANNEL peer for the Pyrogram bot client
if SAVE_CHANNEL:
    try:
        # get_chat() forces a full peer resolution from Telegram and caches it
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(Bot.get_chat(SAVE_CHANNEL))
        else:
            loop.run_until_complete(Bot.get_chat(SAVE_CHANNEL))
        print(f"[CACHE] SAVE_CHANNEL peer resolved: {SAVE_CHANNEL}")
    except Exception as e:
        print(f"[CACHE] Warning: Could not resolve SAVE_CHANNEL peer ({SAVE_CHANNEL}): {e}")
        print("Make sure the bot is an admin in the SAVE_CHANNEL.")
