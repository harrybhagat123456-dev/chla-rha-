#Github.com/Vasusen-code

from pyrogram import Client

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

from decouple import config
import logging, time, sys

logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
                    level=logging.WARNING)

# variables
API_ID = config("API_ID", default=None, cast=int)
API_HASH = config("API_HASH", default=None)
BOT_TOKEN = config("BOT_TOKEN", default=None)
SESSION = config("SESSION", default=None)
FORCESUB = config("FORCESUB", default=None)
AUTH = config("AUTH", default=None, cast=int)
SAVE_CHANNEL = config("SAVE_CHANNEL", default=None)  # Channel/group ID where content is saved (for pinning & inline link rewriting)

bot = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN) 

userbot = Client("saverestricted", session_string=SESSION, api_hash=API_HASH, api_id=API_ID) 

try:
    userbot.start()
except BaseException:
    print("Userbot Error ! Have you added SESSION while deploying??")
    sys.exit(1)

Bot = Client(
    "SaveRestricted",
    bot_token=BOT_TOKEN,
    api_id=int(API_ID),
    api_hash=API_HASH
)    

try:
    Bot.start()
except Exception as e:
    print(e)
    sys.exit(1)

# Resolve SAVE_CHANNEL peer for the Pyrogram bot client so it doesn't
# throw PeerIdInvalid when first trying to send/edit messages there.
if SAVE_CHANNEL:
    try:
        Bot.resolve_peer(int(SAVE_CHANNEL))
        print(f"SAVE_CHANNEL peer resolved: {SAVE_CHANNEL}")
    except Exception as e:
        print(f"Warning: Could not resolve SAVE_CHANNEL peer ({SAVE_CHANNEL}): {e}")
        print("Make sure the bot is an admin in the SAVE_CHANNEL.")
