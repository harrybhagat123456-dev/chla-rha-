#Github.com-Vasusen-code
#Modified: Uses SAVE_CHANNEL for saving content (required for pinning)

import time, os

from .. import bot as Drone
from .. import userbot, Bot, SAVE_CHANNEL
from .. import FORCESUB as fs
from main.plugins.pyroplug import get_msg
from main.plugins.helpers import get_link, join

from telethon import events
from pyrogram.errors import FloodWait

from ethon.telefunc import force_sub

ft = f"To use this bot you've to join @{fs}."

message = "Send me the message link you want to start saving from, as a reply to this message."

@Drone.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def clone(event):
    if event.is_reply:
        reply = await event.get_reply_message()
        if reply.text == message:
            return
    try:
        link = get_link(event.text)
        if not link:
            return
    except TypeError:
        return
    s, r = await force_sub(event.client, fs, event.sender_id, ft)
    if s == True:
        await event.reply(r)
        return
    # Determine where to save the content:
    # If SAVE_CHANNEL is configured, save to that channel (enables pinning & inline link support)
    # Otherwise, fall back to saving in the user's DM (original behavior)
    target = int(SAVE_CHANNEL) if SAVE_CHANNEL else event.sender_id

    # Status/progress messages stay in the user's DM so they can see what's happening.
    # Content gets delivered to SAVE_CHANNEL (if configured) or the user's DM.
    if target != event.sender_id:
        # Save channel is different from user DM — send status message to DM
        status_msg = await Drone.send_message(event.sender_id, "Processing!")
        edit_id = status_msg.id
        status_chat = event.sender_id
    else:
        edit = await event.reply("Processing!")
        edit_id = edit.id
        status_chat = event.sender_id

    try:
        if 't.me/+' in link:
            q = await join(userbot, link)
            await Drone.send_message(event.sender_id, q)
            return
        if 't.me/' in link:
            await get_msg(userbot, Bot, Drone, target, edit_id, status_chat, link, 0)
    except FloodWait as fw:
        return await Drone.send_message(event.sender_id, f'Try again after {fw.x} seconds due to floodwait from telegram.')
    except Exception as e:
        print(e)
        await Drone.send_message(event.sender_id, f"An error occurred during cloning of `{link}`\n\n**Error:** {str(e)}")
