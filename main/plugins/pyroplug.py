#Github.com-Vasusen-code
#Modified: poll forwarding, message pinning, inline link rewriting
#Bug fixes: PeerIdInvalid (separate status_chat from content target),
#           syntax bug in rewrite_inline_links, resolve_peer for SAVE_CHANNEL

import asyncio, time, os, re

from .. import bot as Drone
from .. import userbot, Bot
from main.plugins.progress import progress_for_pyrogram
from main.plugins.helpers import screenshot

from pyrogram import Client, filters
from pyrogram.errors import ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid, PeerIdInvalid
from pyrogram.enums import MessageMediaType, PollType
from ethon.pyfunc import video_metadata
from ethon.telefunc import fast_upload
from telethon.tl.types import DocumentAttributeVideo
from telethon import events

# ---------------------------------------------------------------------------
# Global mapping: original chat_id+msg_id -> new msg_id in our saved channel
# This is used to rewrite inline links that reference already-saved messages.
# ---------------------------------------------------------------------------
msg_map = {}

# Cache of channel IDs we've already resolved to avoid repeated dialog scans
_resolved_peers = set()

async def resolve_peer_safe(client, chat_id):
    """
    Ensure Pyrogram has the access hash for `chat_id` in its session cache.

    Pyrogram raises PeerIdInvalid when it knows a numeric channel ID but
    hasn't stored the access hash yet (i.e. the channel hasn't appeared in
    any update since the session was created). Walking get_dialogs() forces
    Pyrogram to fetch & cache access hashes for every channel the account
    is in, which fixes the error.

    Returns True if the peer was resolved, False otherwise.
    """
    if chat_id in _resolved_peers:
        return True
    # Fast path: get_chat works when the peer is already cached
    try:
        await client.get_chat(chat_id)
        _resolved_peers.add(chat_id)
        return True
    except Exception:
        pass
    # Slow path: iterate dialogs until we find the channel
    try:
        async for dialog in client.get_dialogs():
            if dialog.chat and dialog.chat.id == chat_id:
                _resolved_peers.add(chat_id)
                return True
    except Exception:
        pass
    return False


def thumbnail(sender):
    if os.path.exists(f'{sender}.jpg'):
        return f'{sender}.jpg'
    else:
        return None


# ---------------------------------------------------------------------------
# Pin the message to the saved channel
# ---------------------------------------------------------------------------
async def pin_if_channel(client, chat_id, msg_id):
    """
    Pin the forwarded/saved message in the target channel.
    Only works if chat_id corresponds to a channel/supergroup.
    Silently skips if pinning fails (e.g. insufficient permissions).
    """
    try:
        await client.pin_chat_message(
            chat_id=chat_id,
            message_id=msg_id,
            both_sides=False  # Only notify the pinner, not all members
        )
    except Exception as e:
        print(f"Could not pin message {msg_id} in {chat_id}: {e}")


# ---------------------------------------------------------------------------
# Resolve chat from a Telegram message link
# ---------------------------------------------------------------------------
def resolve_chat_from_link(msg_link):
    """
    Parse a Telegram message link and return the chat identifier.
    
    For private channels: t.me/c/1234567/42  -> -1001234567 (Pyrogram format)
    For bot chats:        t.me/b/botname/42  -> 'botname'
    For public channels:  t.me/channelname/42 -> 'channelname'
    
    Returns:
        (chat, is_private) tuple - chat is int for private, str for public/bot
    """
    if 't.me/c/' in msg_link:
        channel_id = msg_link.split("/")[-2]
        chat = int('-100' + channel_id)
        return chat, True
    elif 't.me/b/' in msg_link:
        chat = str(msg_link.split("/")[-2])
        return chat, True
    else:
        chat = str(msg_link.split("/")[-2])
        return chat, False


# ---------------------------------------------------------------------------
# Inline link rewriting
# ---------------------------------------------------------------------------
def rewrite_inline_links(text, original_chat_id, new_chat_id):
    """
    Rewrite inline links in message text that reference other messages in the
    same source chat. The pattern matches Telegram message links like:
      https://t.me/c/1234567890/42
      https://t.me/channelname/42
      t.me/c/1234567890/42
      t.me/channelname/42

    If the referenced message was already saved by the bot, the link is
    rewritten to point to the new message in our saved channel.

    Args:
        text: The message text (markdown or plain) to process
        original_chat_id: The source chat id (int or username string)
        new_chat_id: Our saved channel id

    Returns:
        Rewritten text with updated links where applicable
    """
    if not text:
        return text

    def replace_link(match):
        full_url = match.group(0)
        # Private: t.me/c/1234567/42
        private_match = re.match(r'(?:https?://)?t\.me/c/(\d+)/(\d+)', full_url)
        if private_match:
            link_chat = int('-100' + private_match.group(1))
            link_msg_id = int(private_match.group(2))
            map_key = (link_chat, link_msg_id)
            if map_key in msg_map:
                new_msg_id = msg_map[map_key]  # FIXED: was msg_mapap_key]
                if isinstance(new_chat_id, int) and str(new_chat_id).startswith('-100'):
                    short_id = str(new_chat_id)[4:]  # Remove -100 prefix
                    return f"https://t.me/c/{short_id}/{new_msg_id}"
                else:
                    return f"https://t.me/{new_chat_id}/{new_msg_id}"
            return full_url

        # Public: t.me/username/42
        public_match = re.match(r'(?:https?://)?t\.me/([a-zA-Z][\w]{4,})/(\d+)', full_url)
        if public_match:
            link_chat = public_match.group(1)
            link_msg_id = int(public_match.group(2))

            map_key_str = (link_chat, link_msg_id)
            if map_key_str in msg_map:
                new_msg_id = msg_map[map_key_str]  # FIXED: was msg_mapap_key_str]
                if isinstance(new_chat_id, int) and str(new_chat_id).startswith('-100'):
                    short_id = str(new_chat_id)[4:]
                    return f"https://t.me/c/{short_id}/{new_msg_id}"
                else:
                    return f"https://t.me/{new_chat_id}/{new_msg_id}"
            return full_url

        return full_url

    # Match t.me links in text
    pattern = r'(?:https?://)?t\.me/(?:c/\d+|\w{5,})/\d+'
    result = re.sub(pattern, replace_link, text)
    return result


# ---------------------------------------------------------------------------
# Poll forwarding
# ---------------------------------------------------------------------------
async def forward_poll(client, target_chat, msg, status_msg):
    """
    Forward a poll message by re-creating the poll with the same question,
    options, and answer details. Since Telegram doesn't allow directly
    forwarding polls from restricted chats, we reconstruct the poll.

    Args:
        client: Pyrogram bot client
        target_chat: The chat where the poll should be sent (SAVE_CHANNEL or user DM)
        msg: The original message containing the poll
        status_msg: The status message in the user's DM to update progress

    Returns the sent message object so caller can pin it.
    """
    poll = msg.poll

    if poll is None:
        return None

    # Build the options list from the original poll
    options = [opt.text for opt in poll.options]

    # Determine poll type
    is_quiz = poll.type == PollType.QUIZ
    is_anonymous = poll.is_anonymous

    # Find the correct answer for quiz polls
    correct_option_index = None
    if is_quiz:
        if hasattr(poll, 'correct_option_index') and poll.correct_option_index is not None:
            correct_option_index = poll.correct_option_index
        else:
            for idx, opt in enumerate(poll.options):
                if hasattr(opt, 'data') and opt.data and opt.data.get('correct', False):
                    correct_option_index = idx
                    break
            if correct_option_index is None:
                correct_option_index = 0

    # Build explanation if available (for quiz polls)
    explanation = None
    if hasattr(poll, 'explanation') and poll.explanation:
        explanation = poll.explanation

    explanation_entities = None
    if hasattr(poll, 'explanation_entities') and poll.explanation_entities:
        explanation_entities = poll.explanation_entities

    # Update status in user's DM
    try:
        await status_msg.edit("Forwarding poll...")
    except Exception:
        pass

    sent_msg = None
    try:
        if is_quiz:
            sent_msg = await client.send_poll(
                chat_id=target_chat,
                question=poll.question,
                options=options,
                is_anonymous=is_anonymous,
                type=PollType.QUIZ,
                correct_option_index=correct_option_index,
                explanation=explanation,
                explanation_entities=explanation_entities,
            )
        else:
            sent_msg = await client.send_poll(
                chat_id=target_chat,
                question=poll.question,
                options=options,
                is_anonymous=is_anonymous,
                type=PollType.REGULAR,
            )

        # Send vote count details as a follow-up message for reference
        vote_info = "**Original Poll Vote Summary:**\n\n"
        vote_info += f"**Question:** {poll.question}\n"
        vote_info += f"**Total Voters:** {poll.total_voter_count}\n"
        vote_info += f"**Poll Status:** {'Closed' if poll.is_closed else 'Open'}\n"
        vote_info += f"**Type:** {'Quiz' if is_quiz else 'Regular'}\n"
        vote_info += f"**Anonymous:** {'Yes' if is_anonymous else 'No'}\n\n"

        for idx, opt in enumerate(poll.options):
            marker = ""
            if is_quiz and idx == correct_option_index:
                marker = " (Correct)"
            vote_info += f"  {idx + 1}. {opt.text} - {opt.voter_count} vote(s){marker}\n"

        await client.send_message(target_chat, vote_info)

    except Exception as e:
        print(f"Poll forward error: {e}")
        # Fallback: send poll details as text if re-creation fails
        poll_text = "**Poll (could not re-create):**\n\n"
        poll_text += f"**Question:** {poll.question}\n"
        poll_text += f"**Total Voters:** {poll.total_voter_count}\n"
        poll_text += f"**Status:** {'Closed' if poll.is_closed else 'Open'}\n\n"
        for idx, opt in enumerate(poll.options):
            poll_text += f"  {idx + 1}. {opt.text} - {opt.voter_count} vote(s)\n"
        sent_msg = await client.send_message(target_chat, poll_text)

    return sent_msg


# ---------------------------------------------------------------------------
# Register message mapping for inline link rewriting
# ---------------------------------------------------------------------------
def register_msg_mapping(original_chat, original_msg_id, new_chat_id, new_msg_id):
    """
    Store the mapping from original chat+msg_id to the new message id
    in our saved channel, so inline links can be rewritten later.
    """
    msg_map[(original_chat, original_msg_id)] = new_msg_id


# ---------------------------------------------------------------------------
# Core message handler
# ---------------------------------------------------------------------------
async def get_msg(userbot, client, bot, sender, edit_id, status_chat, msg_link, i):

    """ 
    userbot: PyrogramUserBot
    client: PyrogramBotClient
    bot: TelethonBotClient
    sender: Target chat for content (SAVE_CHANNEL or user DM)
    edit_id: Message ID of the "Processing!" status message in status_chat
    status_chat: Chat where status/progress messages live (user's DM)
    """

    edit = ""
    chat = ""
    round_message = False
    if "?single" in msg_link:
        msg_link = msg_link.split("?single")[0]
    msg_id = int(msg_link.split("/")[-1]) + int(i)
    height, width, duration, thumb_path = 90, 90, 0, None

    if 't.me/c/' in msg_link or 't.me/b/' in msg_link:
        if 't.me/b/' in msg_link:
            chat = str(msg_link.split("/")[-2])
        else:
            chat = int('-100' + str(msg_link.split("/")[-2]))
        file = ""
        try:
            # Ensure the access hash for this channel is in Pyrogram's cache.
            # Without this, private channels the userbot hasn't interacted with
            # in this session raise PeerIdInvalid even when the account is a member.
            await resolve_peer_safe(userbot, chat)
            msg = await userbot.get_messages(chat, msg_id)

            # ---- POLL HANDLING ----
            if msg.poll is not None:
                edit = await client.edit_message_text(status_chat, edit_id, "Processing poll...")
                sent_msg = await forward_poll(client, sender, msg, edit)
                if sent_msg:
                    register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                    await pin_if_channel(client, sender, sent_msg.id)
                await edit.delete()
                return
            # ---- END POLL HANDLING ----

            if msg.media:
                if msg.media==MessageMediaType.WEB_PAGE:
                    edit = await client.edit_message_text(status_chat, edit_id, "Cloning.")
                    text = msg.text.markdown if msg.text else ""
                    rewritten = rewrite_inline_links(text, chat, sender)
                    await client.send_message(sender, rewritten)
                    await edit.delete()
                    return
            if not msg.media:
                if msg.text:
                    edit = await client.edit_message_text(status_chat, edit_id, "Cloning.")
                    text = msg.text.markdown if msg.text else ""
                    rewritten = rewrite_inline_links(text, chat, sender)
                    sent_msg = await client.send_message(sender, rewritten)
                    register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                    await pin_if_channel(client, sender, sent_msg.id)
                    await edit.delete()
                    return
            edit = await client.edit_message_text(status_chat, edit_id, "Trying to Download.")
            file = await userbot.download_media(
                msg,
                progress=progress_for_pyrogram,
                progress_args=(
                    client,
                    "**DOWNLOADING:**\n",
                    edit,
                    time.time()
                )
            )
            print(file)
            await edit.edit('Preparing to Upload!')
            caption = None
            if msg.caption is not None:
                caption = rewrite_inline_links(msg.caption, chat, sender)

            sent_msg = None
            if msg.media==MessageMediaType.VIDEO_NOTE:
                round_message = True
                print("Trying to get metadata")
                data = video_metadata(file)
                height, width, duration = data["height"], data["width"], data["duration"]
                print(f'd: {duration}, w: {width}, h:{height}')
                try:
                    thumb_path = await screenshot(file, duration, sender)
                except Exception:
                    thumb_path = None
                sent_msg = await client.send_video_note(
                    chat_id=sender,
                    video_note=file,
                    length=height, duration=duration,
                    thumb=thumb_path,
                    progress=progress_for_pyrogram,
                    progress_args=(
                        client,
                        '**UPLOADING:**\n',
                        edit,
                        time.time()
                    )
                )
            elif msg.media==MessageMediaType.VIDEO and msg.video.mime_type in ["video/mp4", "video/x-matroska"]:
                print("Trying to get metadata")
                data = video_metadata(file)
                height, width, duration = data["height"], data["width"], data["duration"]
                print(f'd: {duration}, w: {width}, h:{height}')
                try:
                    thumb_path = await screenshot(file, duration, sender)
                except Exception:
                    thumb_path = None
                sent_msg = await client.send_video(
                    chat_id=sender,
                    video=file,
                    caption=caption,
                    supports_streaming=True,
                    height=height, width=width, duration=duration,
                    thumb=thumb_path,
                    progress=progress_for_pyrogram,
                    progress_args=(
                        client,
                        '**UPLOADING:**\n',
                        edit,
                        time.time()
                    )
                )

            elif msg.media==MessageMediaType.PHOTO:
                await edit.edit("Uploading photo.")
                sent_msg = await bot.send_file(sender, file, caption=caption)
            else:
                thumb_path=thumbnail(sender)
                sent_msg = await client.send_document(
                    sender,
                    file,
                    caption=caption,
                    thumb=thumb_path,
                    progress=progress_for_pyrogram,
                    progress_args=(
                        client,
                        '**UPLOADING:**\n',
                        edit,
                        time.time()
                    )
                )

            # Register mapping and pin the message
            if sent_msg:
                register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                await pin_if_channel(client, sender, sent_msg.id)

            try:
                os.remove(file)
                if os.path.isfile(file) == True:
                    os.remove(file)
            except Exception:
                pass
            await edit.delete()
        except (ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid):
            await client.edit_message_text(status_chat, edit_id, "Have you joined the channel?")
            return
        except PeerIdInvalid:
            # PeerIdInvalid — the userbot is not a member of this channel
            await client.edit_message_text(
                status_chat, edit_id,
                "**Peer id invalid** — the userbot account is not a member of "
                "this channel.\n\nSend the channel's invite link first so the "
                "userbot can join, then retry."
            )
            return
        except Exception as e:
            print(e)
            if "messages.SendMedia" in str(e) \
            or "SaveBigFilePartRequest" in str(e) \
            or "SendMediaRequest" in str(e) \
            or str(e) == "File size equals to 0 B":
                try:
                    if msg.media==MessageMediaType.VIDEO and msg.video.mime_type in ["video/mp4", "video/x-matroska"]:
                        UT = time.time()
                        uploader = await fast_upload(f'{file}', f'{file}', UT, bot, edit, '**UPLOADING:**')
                        attributes = [DocumentAttributeVideo(duration=duration, w=width, h=height, round_message=round_message, supports_streaming=True)]
                        sent_msg = await bot.send_file(sender, uploader, caption=caption, thumb=thumb_path, attributes=attributes, force_document=False)
                    elif msg.media==MessageMediaType.VIDEO_NOTE:
                        uploader = await fast_upload(f'{file}', f'{file}', UT, bot, edit, '**UPLOADING:**')
                        attributes = [DocumentAttributeVideo(duration=duration, w=width, h=height, round_message=round_message, supports_streaming=True)]
                        sent_msg = await bot.send_file(sender, uploader, caption=caption, thumb=thumb_path, attributes=attributes, force_document=False)
                    else:
                        UT = time.time()
                        uploader = await fast_upload(f'{file}', f'{file}', UT, bot, edit, '**UPLOADING:**')
                        sent_msg = await bot.send_file(sender, uploader, caption=caption, thumb=thumb_path, force_document=True)

                    # Register mapping and pin
                    if sent_msg:
                        register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                        await pin_if_channel(client, sender, sent_msg.id)

                    if os.path.isfile(file) == True:
                        os.remove(file)
                except Exception as e:
                    print(e)
                    await client.edit_message_text(status_chat, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')
                    try:
                        os.remove(file)
                    except Exception:
                        return
                    return
            else:
                await client.edit_message_text(status_chat, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')
                try:
                    os.remove(file)
                except Exception:
                    return
                return
        try:
            os.remove(file)
            if os.path.isfile(file) == True:
                os.remove(file)
        except Exception:
            pass
        await edit.delete()
    else:
        edit = await client.edit_message_text(status_chat, edit_id, "Cloning.")
        chat = msg_link.split("t.me")[1].split("/")[1]
        try:
            msg = await client.get_messages(chat, msg_id)

            # ---- POLL HANDLING for public chats ----
            if msg.poll is not None:
                sent_msg = await forward_poll(client, sender, msg, edit)
                if sent_msg:
                    register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                    await pin_if_channel(client, sender, sent_msg.id)
                await edit.delete()
                return
            # ---- END POLL HANDLING ----

            if msg.empty:
                new_link = f't.me/b/{chat}/{int(msg_id)}'
                #recurrsion
                return await get_msg(userbot, client, bot, sender, edit_id, status_chat, new_link, i)

            # For public chats, use copy_message but also handle inline links
            sent_msg = await client.copy_message(sender, chat, msg_id)
            if sent_msg:
                register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                await pin_if_channel(client, sender, sent_msg.id)

                # Check if the original message text has t.me links that need rewriting
                if msg.text:
                    original_text = msg.text.markdown if msg.text else ""
                    rewritten = rewrite_inline_links(original_text, chat, sender)
                    if rewritten != original_text:
                        try:
                            await client.edit_message_text(sender, sent_msg.id, rewritten)
                        except Exception as e:
                            print(f"Could not edit message for inline link rewriting: {e}")

        except Exception as e:
            print(e)
            return await client.edit_message_text(status_chat, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')
        await edit.delete()

async def get_bulk_msg(userbot, client, sender, msg_link, i):
    # For bulk messages, status_chat = sender (same chat for both)
    x = await client.send_message(sender, "Processing!")
    await get_msg(userbot, client, Drone, sender, x.id, sender, msg_link, i)
