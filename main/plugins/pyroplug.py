#Github.com-Vasusen-code
#Modified: poll forwarding, message pinning, inline link rewriting
#Bug fixes: PeerIdInvalid, 'bytes'.get() crash, poll answers, photo upload,
#           no-media handling, copy ALL message types (stickers, animations, etc.)

import asyncio, time, os, re, json, urllib.parse

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
# ---------------------------------------------------------------------------
msg_map = {}

# Cache of channel IDs we've already resolved to avoid repeated dialog scans
_resolved_peers = set()

# Cache of pinned message IDs per chat: {chat_id: set_of_msg_ids}
_pinned_cache = {}

# Media types that can be downloaded via userbot.download_media()
DOWNLOADABLE_MEDIA = {
    MessageMediaType.VIDEO, MessageMediaType.VIDEO_NOTE,
    MessageMediaType.PHOTO, MessageMediaType.DOCUMENT,
    MessageMediaType.AUDIO, MessageMediaType.ANIMATION,
    MessageMediaType.VOICE, MessageMediaType.STICKER,
}

async def resolve_peer_safe(client, chat_id):
    """Ensure Pyrogram has the access hash for chat_id in its session cache."""
    if chat_id in _resolved_peers:
        return True
    try:
        await client.resolve_peer(chat_id)
        _resolved_peers.add(chat_id)
        return True
    except Exception:
        pass
    try:
        await client.get_chat(chat_id)
        _resolved_peers.add(chat_id)
        return True
    except Exception:
        pass
    try:
        async for dialog in client.get_dialogs():
            if dialog.chat and dialog.chat.id == chat_id:
                _resolved_peers.add(chat_id)
                return True
    except Exception:
        pass
    return False


async def ensure_target_peer(client, target_chat):
    """Before sending to SAVE_CHANNEL, ensure the bot has the access hash cached."""
    if isinstance(target_chat, int) and target_chat < -1000000000000:
        resolved = await resolve_peer_safe(client, target_chat)
        if not resolved:
            print(f"[WARN] Could not resolve peer for {target_chat}. "
                  f"Make sure the bot is an admin in this channel.")
    return True


def thumbnail(sender):
    if os.path.exists(f'{sender}.jpg'):
        return f'{sender}.jpg'
    else:
        return None


# ---------------------------------------------------------------------------
# Pin the message to the saved channel
# ---------------------------------------------------------------------------
async def get_pinned_msg_ids(userbot_client, client, chat_id):
    """Fetch and cache pinned message IDs from a source chat.
    Uses the userbot (which is a member of the source channel) to get pinned messages.
    Falls back to the bot client for public chats."""
    # Return cached if available
    if chat_id in _pinned_cache:
        return _pinned_cache[chat_id]

    pinned_ids = set()

    # Method 1: Use Pyrogram userbot to get chat info with pinned_message
    # The userbot is a member of the source channel, so it can see pinned messages
    try:
        chat_info = await userbot_client.get_chat(chat_id)
        if chat_info:
            # chat.pinned_message gives the LATEST pinned message
            if hasattr(chat_info, 'pinned_message') and chat_info.pinned_message:
                pinned_ids.add(chat_info.pinned_message.id)
                print(f"[PIN] Found pinned message via userbot get_chat: {chat_info.pinned_message.id}")
    except Exception as e:
        print(f"[PIN] userbot get_chat failed: {e}")

    # Method 2: Iterate recent messages via userbot and check for pinned ones
    # This catches ALL pinned messages, not just the latest
    if not pinned_ids:
        try:
            # Check last 200 messages for pinned attribute
            async for msg in userbot_client.get_chat_history(chat_id, limit=200):
                if hasattr(msg, 'pinned') and msg.pinned:
                    pinned_ids.add(msg.id)
            if pinned_ids:
                print(f"[PIN] Found {len(pinned_ids)} pinned messages via userbot get_chat_history: {pinned_ids}")
        except Exception as e:
            print(f"[PIN] userbot get_chat_history failed: {e}")

    # Method 3: Use bot client for public chats
    if not pinned_ids:
        try:
            chat_info = await client.get_chat(chat_id)
            if chat_info and hasattr(chat_info, 'pinned_message') and chat_info.pinned_message:
                pinned_ids.add(chat_info.pinned_message.id)
                print(f"[PIN] Found pinned message via bot get_chat: {chat_info.pinned_message.id}")
        except Exception as e:
            print(f"[PIN] bot get_chat failed: {e}")

    _pinned_cache[chat_id] = pinned_ids
    if pinned_ids:
        print(f"[PIN] Cached {len(pinned_ids)} pinned message IDs for chat {chat_id}: {pinned_ids}")
    else:
        print(f"[PIN] No pinned messages found for chat {chat_id}")
    return pinned_ids


async def pin_if_channel(client, chat_id, msg_id, was_pinned=False):
    """Pin a message in channels/groups only if it was pinned in the original chat.
    Bots get BOT_ONESIDE_NOT_AVAIL error when trying to pin in DMs."""
    # Skip pinning in private chats (user DMs have positive IDs)
    if isinstance(chat_id, int) and chat_id > 0:
        return
    # Only pin if the original message was pinned
    if not was_pinned:
        return
    try:
        await client.pin_chat_message(
            chat_id=chat_id,
            message_id=msg_id,
            both_sides=False
        )
        print(f"[PIN] Pinned message {msg_id} in {chat_id}")
    except Exception as e:
        print(f"Could not pin message {msg_id} in {chat_id}: {e}")


# ---------------------------------------------------------------------------
# Resolve chat from a Telegram message link
# ---------------------------------------------------------------------------
def resolve_chat_from_link(msg_link):
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
# inline link rewriting
# ---------------------------------------------------------------------------
def rewrite_inline_links(text, original_chat_id, new_chat_id):
    if not text:
        return text

    def replace_link(match):
        full_url = match.group(0)
        private_match = re.match(r'(?:https?://)?t\.me/c/(\d+)/(\d+)', full_url)
        if private_match:
            link_chat = int('-100' + private_match.group(1))
            link_msg_id = int(private_match.group(2))
            map_key = (link_chat, link_msg_id)
            if map_key in msg_map:
                new_msg_id = msg_map[map_key]
                if isinstance(new_chat_id, int) and str(new_chat_id).startswith('-100'):
                    short_id = str(new_chat_id)[4:]
                    return f"https://t.me/c/{short_id}/{new_msg_id}"
                else:
                    return f"https://t.me/{new_chat_id}/{new_msg_id}"
            return full_url

        public_match = re.match(r'(?:https?://)?t\.me/([a-zA-Z][\w]{4,})/(\d+)', full_url)
        if public_match:
            link_chat = public_match.group(1)
            link_msg_id = int(public_match.group(2))
            map_key_str = (link_chat, link_msg_id)
            if map_key_str in msg_map:
                new_msg_id = msg_map[map_key_str]
                if isinstance(new_chat_id, int) and str(new_chat_id).startswith('-100'):
                    short_id = str(new_chat_id)[4:]
                    return f"https://t.me/c/{short_id}/{new_msg_id}"
                else:
                    return f"https://t.me/{new_chat_id}/{new_msg_id}"
            return full_url

        return full_url

    pattern = r'(?:https?://)?t\.me/(?:c/\d+|\w{5,})/\d+'
    result = re.sub(pattern, replace_link, text)
    return result


# ---------------------------------------------------------------------------
# Poll forwarding — multi-strategy approach
#
# Strategy order:
#   1. Try quiz poll via Pyrogram send_poll (with correct_option_index if available)
#   2. Try quiz poll via Telethon raw API (works even without correct_option_index attr)
#   3. Try regular poll via Pyrogram send_poll (loses quiz marking but creates the poll)
#   4. Fallback: send poll data as text
# ---------------------------------------------------------------------------
def _extract_correct_option(poll):
    """
    Try every possible way to find the correct option index for a quiz poll.
    Returns (index, explanation, explanation_entities) or (None, None, None).
    """
    correct_idx = None
    explanation = None
    explanation_entities = None

    # Method 1: Direct attribute (Pyrogram >= 2.0)
    if hasattr(poll, 'correct_option_index') and poll.correct_option_index is not None:
        correct_idx = poll.correct_option_index
        print(f"[POLL] Found correct_option_index via attribute: {correct_idx}")

    # Method 2: Check _raw attribute (some Pyrogram versions store it here)
    if correct_idx is None and hasattr(poll, '_raw'):
        raw = poll._raw
        if hasattr(raw, 'correct_answer') and raw.correct_answer:
            # correct_answer is bytes matching one of the option.data values
            try:
                for i, opt in enumerate(poll.options):
                    opt_data = getattr(opt, 'data', None) or getattr(opt, 'option', None)
                    if opt_data and opt_data == raw.correct_answer:
                        correct_idx = i
                        print(f"[POLL] Found correct answer via _raw.correct_answer: option {i}")
                        break
            except Exception as e:
                print(f"[POLL] _raw method failed: {e}")

    # Method 3: Check results in the Poll object
    if correct_idx is None and hasattr(poll, 'results') and poll.results:
        results = poll.results
        if hasattr(results, 'results') and results.results:
            for i, r in enumerate(results.results):
                if getattr(r, 'correct', False):
                    correct_idx = i
                    print(f"[POLL] Found correct answer via results.results[{i}].correct")
                    break

    # Extract explanation
    explanation = getattr(poll, 'explanation', None)
    explanation_entities = getattr(poll, 'explanation_entities', None)
    # Also try from _raw
    if explanation is None and hasattr(poll, '_raw'):
        raw = poll._raw
        explanation = getattr(raw, 'solution', None)
        explanation_entities = getattr(raw, 'solution_entities', None)

    return correct_idx, explanation, explanation_entities


# ---------------------------------------------------------------------------
# OCR + UPSC Answer Search
#
# When a quiz poll has an image, this module:
#   1. Downloads the image
#   2. OCRs it using pytesseract to extract question text
#   3. Searches UPSC sites (BYJU'S, ClearIAS, Drishti IAS, etc.) for the answer
#   4. Matches the found answer against poll options
#   5. Returns the correct option index (or None if unsure)
# ---------------------------------------------------------------------------

# UPSC answer sites to search (ordered by reliability)
_UPSC_SEARCH_QUERIES = [
    'site:byjus.com UPSC question answer {question}',
    'site:clearias.com UPSC question answer {question}',
    'site:drishtiias.com UPSC question answer {question}',
    'site:iasbaba.com UPSC question answer {question}',
    'site:mrunal.org UPSC question answer {question}',
    'site:visionias.in UPSC question answer {question}',
    'UPSC previous year question answer {question}',
    'UPSC answer key {question}',
]

# Known UPSC answer patterns in search snippets
_ANSWER_PATTERNS = [
    re.compile(r'(?:correct\s*answer|answer\s*(?:is|:)|option\s*(?:is|:))\s*[–\-:]?\s*(?:option\s*)?([A-Da-d])', re.IGNORECASE),
    re.compile(r'(?:answer|option)\s*(?:key|is)\s*[–\-:]?\s*\(?([A-Da-d])\)?', re.IGNORECASE),
    re.compile(r'\b([A-Da-d])\)\s*(?:is\s+correct|✓|✔|✅)', re.IGNORECASE),
    re.compile(r'\bans(?:wer)?\s*(?:\.|:|\-)\s*([A-Da-d])\b', re.IGNORECASE),
    re.compile(r'\boption\s+([A-Da-d])\b.*?(?:correct|right|answer)', re.IGNORECASE),
    re.compile(r'(?:correct|right)\s*(?:option|answer)\s*(?:is|:)\s*([A-Da-d])', re.IGNORECASE),
]

# Option letter mapping
_LETTER_TO_INDEX = {'a': 0, 'b': 1, 'c': 2, 'd': 3}


def _ocr_image(image_path):
    """Run pytesseract OCR on an image and return extracted text."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang='eng')
        return text.strip()
    except ImportError:
        print("[OCR] pytesseract or PIL not installed — OCR skipped")
        return None
    except Exception as e:
        print(f"[OCR] OCR failed: {e}")
        return None


async def _search_upsc_answer(question_text, options_list):
    """
    Search UPSC answer sites for the correct answer to a question.
    Returns the correct option index (0-based) or None.
    """
    if not question_text or len(question_text.strip()) < 10:
        return None

    # Clean up the question text for searching (take first ~150 chars)
    search_text = question_text.strip().replace('\n', ' ')[:150]

    # Try multiple search queries
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        print("[SEARCH] duckduckgo_search not installed — UPSC search skipped")
        return None

    # Build option letter map (A=0, B=1, C=2, D=3)
    option_count = len(options_list)
    letter_map = {}
    for i in range(min(option_count, 4)):
        letter_map[chr(65 + i)] = i  # A->0, B->1, C->2, D->3

    best_answer = None
    confidence = 0

    for query_template in _UPSC_SEARCH_QUERIES:
        query = query_template.format(question=search_text)
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))

            for result in results:
                snippet = result.get('body', '') or result.get('title', '')
                if not snippet:
                    continue

                # Try each answer pattern
                for pattern in _ANSWER_PATTERNS:
                    match = pattern.search(snippet)
                    if match:
                        letter = match.group(1).upper()
                        if letter in letter_map:
                            idx = letter_map[letter]
                            # If this is from a dedicated UPSC site, give higher confidence
                            url = result.get('href', '')
                            is_upsc_site = any(site in url for site in
                                ['byjus.com', 'clearias.com', 'drishtiias.com',
                                 'iasbaba.com', 'mrunal.org', 'visionias.in',
                                 'neostencil.com', 'gradeup.co', 'unacademy.com'])

                            this_confidence = 2 if is_upsc_site else 1

                            # If multiple results agree, increase confidence
                            if best_answer is not None and best_answer == idx:
                                this_confidence += 3

                            if this_confidence > confidence:
                                best_answer = idx
                                confidence = this_confidence
                                print(f"[SEARCH] Found answer: Option {letter} (index {idx}) "
                                      f"confidence={confidence} from {url[:60]}")

        except Exception as e:
            print(f"[SEARCH] Search failed for query '{query[:50]}': {e}")
            continue

    # Only return if we have reasonable confidence
    if best_answer is not None and confidence >= 2:
        return best_answer

    return None


async def ocr_and_search_answer(userbot_client, msg, poll):
    """
    If the message containing a poll also has an image (or nearby image),
    OCR it and search UPSC sites for the correct answer.

    Returns (correct_idx, source_info) or (None, None).
    correct_idx: 0-based index of the correct option
    source_info: string describing how the answer was found
    """
    image_path = None

    # Case 1: The poll message itself has a photo
    if msg.photo:
        try:
            image_path = await userbot_client.download_media(msg, file_name="ocr_temp.jpg")
            print(f"[OCR] Downloaded poll image to {image_path}")
        except Exception as e:
            print(f"[OCR] Could not download poll image: {e}")

    # Case 2: The previous message might have the question image
    if not image_path or not os.path.exists(image_path or ''):
        try:
            chat_id = msg.chat.id if msg.chat else None
            msg_id = msg.id if msg.id else None
            if chat_id and msg_id:
                prev_msg = await userbot_client.get_messages(chat_id, msg_id - 1)
                if prev_msg and prev_msg.photo:
                    image_path = await userbot_client.download_media(prev_msg, file_name="ocr_temp.jpg")
                    print(f"[OCR] Downloaded previous message image to {image_path}")
        except Exception as e:
            print(f"[OCR] Could not get previous message image: {e}")

    if not image_path or not os.path.exists(image_path or ''):
        return None, None

    # OCR the image
    ocr_text = _ocr_image(image_path)
    if not ocr_text:
        try:
            os.remove(image_path)
        except:
            pass
        return None, None

    print(f"[OCR] Extracted text: {ocr_text[:200]}")

    # Get the poll options
    options = [opt.text for opt in poll.options]

    # Search for the answer
    correct_idx = await _search_upsc_answer(ocr_text, options)

    # Cleanup temp file
    try:
        os.remove(image_path)
    except:
        pass

    if correct_idx is not None:
        letter = chr(65 + correct_idx)
        source_info = f"Found via OCR + UPSC search: Option {letter}"
        return correct_idx, source_info

    return None, None


async def _send_caption(client, target_chat, msg, original_chat, sender):
    """Send the original message's caption as a pink-styled blockquote message."""
    caption_text = None
    if msg.caption:
        caption_text = msg.caption
    elif msg.text and msg.poll is not None:
        # For poll-only messages, the "caption" might be the text before the poll
        caption_text = None

    if not caption_text:
        return None

    # Rewrite any inline links in the caption
    rewritten = rewrite_inline_links(caption_text, original_chat, sender)

    # Send as a pink blockquote-style message
    # Telegram blockquote shows a colored left bar — we use it for the "pink caption" effect
    try:
        from pyrogram.types import MessageEntity
        caption_msg = await client.send_message(
            target_chat,
            rewritten,
            quote=True,  # blockquote styling
        )
        return caption_msg
    except Exception:
        # Fallback if quote param not supported — use markdown blockquote
        try:
            quoted = f"> {rewritten}"
            caption_msg = await client.send_message(target_chat, quoted)
            return caption_msg
        except Exception as e:
            print(f"[CAPTION] Could not send caption: {e}")
            return None


async def forward_poll(client, target_chat, msg, status_msg, original_chat=None, sender=None):
    poll = msg.poll
    if poll is None:
        return None

    options = [opt.text for opt in poll.options]
    is_quiz = poll.type == PollType.QUIZ
    is_anonymous = poll.is_anonymous

    # Try to find the correct answer for quiz polls
    correct_idx, explanation, explanation_entities = _extract_correct_option(poll)
    answer_source = "extracted from poll object"

    # ---- If we couldn't find the answer, try OCR + UPSC search ----
    if correct_idx is None and is_quiz:
        try:
            await status_msg.edit("Searching for correct answer via OCR + UPSC sites...")
        except Exception:
            pass
        ocr_idx, ocr_source = await ocr_and_search_answer(userbot, msg, poll)
        if ocr_idx is not None:
            correct_idx = ocr_idx
            answer_source = ocr_source
            print(f"[POLL] Correct answer found via OCR+search: option index {correct_idx}")
        else:
            print(f"[POLL] OCR+search did not find a confident answer — will try regular poll")

    # Also get the raw option data bytes for Telethon fallback
    option_data_list = []
    for opt in poll.options:
        data = getattr(opt, 'data', None) or getattr(opt, 'option', None)
        if data:
            option_data_list.append(data)

    try:
        await status_msg.edit("Forwarding poll...")
    except Exception:
        pass

    # ---- Send caption BEFORE the poll if present ----
    if original_chat is not None and sender is not None:
        await _send_caption(client, target_chat, msg, original_chat, sender)

    sent_msg = None

    # ---- Strategy 1: Pyrogram send_poll (quiz with correct answer) ----
    if is_quiz and correct_idx is not None:
        try:
            # For quiz polls: mark options with spoiler on the correct answer hint
            # The actual poll is sent normally, but in the vote summary the correct
            # answer will be wrapped in ||spoiler|| markdown
            sent_msg = await client.send_poll(
                chat_id=target_chat,
                question=poll.question,
                options=options,
                is_anonymous=is_anonymous,
                type=PollType.QUIZ,
                correct_option_index=correct_idx,
                explanation=explanation,
                explanation_entities=explanation_entities,
            )
            print(f"[POLL] Strategy 1 success: quiz poll with correct answer at index {correct_idx}")
        except Exception as e:
            print(f"[POLL] Strategy 1 failed (Pyrogram quiz): {e}")
            sent_msg = None

    # ---- Strategy 2: Telethon raw API for quiz poll ----
    if is_quiz and sent_msg is None:
        try:
            from telethon.tl.types import InputMediaPoll, Poll as TLPoll, PollAnswer
            from telethon.tl.functions.messages import SendMediaRequest

            # Build TL Poll object
            tl_answers = []
            for i, opt_text in enumerate(options):
                # Use the original option data bytes if available, else generate simple ones
                if i < len(option_data_list):
                    opt_bytes = option_data_list[i]
                else:
                    opt_bytes = bytes([i + 1])
                tl_answers.append(PollAnswer(text=opt_text, option=opt_bytes))

            tl_poll = TLPoll(
                id=0,  # Telegram assigns the ID
                question=poll.question,
                answers=tl_answers,
                closed=poll.is_closed,
                public_voters=not is_anonymous,
                multiple_choice=False,
                quiz=True,
            )

            # Build correct_answer from the data bytes
            correct_answer_bytes = None
            if correct_idx is not None and correct_idx < len(option_data_list):
                correct_answer_bytes = option_data_list[correct_idx]
            elif correct_idx is not None:
                correct_answer_bytes = bytes([correct_idx + 1])

            # Build explanation
            solution = None
            solution_entities = None
            if explanation:
                solution = explanation
                # Convert Pyrogram entities to Telethon if needed
                if explanation_entities:
                    from telethon.tl.types import MessageEntityBold, MessageEntityItalic, MessageEntityCode, MessageEntityTextUrl
                    solution_entities = []
                    for ent in explanation_entities:
                        te = None
                        if hasattr(ent, 'type'):
                            if ent.type.name == 'bold':
                                te = MessageEntityBold(offset=ent.offset, length=ent.length)
                            elif ent.type.name == 'italic':
                                te = MessageEntityItalic(offset=ent.offset, length=ent.length)
                            elif ent.type.name == 'code':
                                te = MessageEntityCode(offset=ent.offset, length=ent.length)
                            elif ent.type.name == 'text_link':
                                te = MessageEntityTextUrl(offset=ent.offset, length=ent.length, url=ent.url)
                        if te:
                            solution_entities.append(te)

            media = InputMediaPoll(
                poll=tl_poll,
                correct_answer=correct_answer_bytes,
                solution=solution,
                solution_entities=solution_entities or [],
            )

            from .. import bot as Drone
            result = await Drone(SendMediaRequest(
                peer=target_chat,
                media=media,
                message='',
                random_id=int(time.time() * 1000),
            ))

            # Get the sent message ID from the result
            if result and hasattr(result, 'updates'):
                for update in result.updates:
                    if hasattr(update, 'message') and hasattr(update.message, 'id'):
                        sent_msg = await client.get_messages(target_chat, update.message.id)
                        break
                    elif hasattr(update, 'id'):
                        sent_msg = await client.get_messages(target_chat, update.id)
                        break

            if sent_msg:
                print(f"[POLL] Strategy 2 success: quiz poll via Telethon raw API")
            else:
                print(f"[POLL] Strategy 2: poll sent but could not retrieve sent message")

        except Exception as e:
            print(f"[POLL] Strategy 2 failed (Telethon quiz): {e}")
            import traceback
            traceback.print_exc()
            sent_msg = None

    # ---- Strategy 3: Regular poll via Pyrogram (no quiz marking) ----
    if sent_msg is None:
        try:
            sent_msg = await client.send_poll(
                chat_id=target_chat,
                question=poll.question,
                options=options,
                is_anonymous=is_anonymous,
                type=PollType.REGULAR,
            )
            print(f"[POLL] Strategy 3 success: regular poll (quiz marking lost)")
        except Exception as e:
            print(f"[POLL] Strategy 3 failed (Pyrogram regular): {e}")
            sent_msg = None

    # ---- Strategy 4: Text fallback ----
    if sent_msg is None:
        poll_text = "**Poll (could not re-create):**\n\n"
        poll_text += f"**Question:** {poll.question}\n"
        poll_text += f"**Total Voters:** {poll.total_voter_count}\n"
        poll_text += f"**Status:** {'Closed' if poll.is_closed else 'Open'}\n"
        poll_text += f"**Type:** {'Quiz' if is_quiz else 'Regular'}\n\n"
        for idx, opt in enumerate(poll.options):
            if is_quiz and idx == correct_idx:
                # Spoiler the correct answer in text fallback
                poll_text += f"  {idx + 1}. ||{opt.text}|| - {opt.voter_count} vote(s) (Correct)\n"
            else:
                poll_text += f"  {idx + 1}. {opt.text} - {opt.voter_count} vote(s)\n"
        if explanation:
            poll_text += f"\n**Explanation:** {explanation}"
        sent_msg = await client.send_message(target_chat, poll_text)
        print(f"[POLL] Strategy 4: text fallback")

    # ---- Always send vote summary with spoiler on correct answer ----
    else:
        try:
            vote_info = "**Original Poll Vote Summary:**\n\n"
            vote_info += f"**Question:** {poll.question}\n"
            vote_info += f"**Total Voters:** {poll.total_voter_count}\n"
            vote_info += f"**Poll Status:** {'Closed' if poll.is_closed else 'Open'}\n"
            vote_info += f"**Type:** {'Quiz' if is_quiz else 'Regular'}\n"
            vote_info += f"**Anonymous:** {'Yes' if is_anonymous else 'No'}\n\n"
            for idx, opt in enumerate(poll.options):
                if is_quiz and idx == correct_idx:
                    # Wrap correct answer in ||spoiler|| — user must click to reveal
                    # After revealing, Telegram keeps it visible (client-side behavior)
                    vote_info += f"  {idx + 1}. ||{opt.text}|| - {opt.voter_count} vote(s) (Correct)\n"
                else:
                    vote_info += f"  {idx + 1}. {opt.text} - {opt.voter_count} vote(s)\n"
            if explanation:
                vote_info += f"\n**Explanation:** {explanation}"
            # Show where the answer came from
            vote_info += f"\n\n_Answer source: {answer_source}_"
            await client.send_message(target_chat, vote_info)
        except Exception as e:
            print(f"[POLL] Could not send vote summary: {e}")

    return sent_msg


# ---------------------------------------------------------------------------
# Fallback: copy message using userbot (handles stickers, animations, etc.)
# ---------------------------------------------------------------------------
async def copy_message_fallback(userbot_client, target_chat, source_chat, msg_id, caption=None):
    """
    Use the userbot's copy_message to copy any message type that we can't
    handle with download+upload (stickers, animations, contacts, locations,
    venues, dice, games, etc.). This works because the userbot has access
    to the source chat.

    Returns the sent message, or None if it fails.
    """
    try:
        sent_msg = await userbot_client.copy_message(
            chat_id=target_chat,
            from_chat_id=source_chat,
            message_id=msg_id,
            caption=caption
        )
        return sent_msg
    except Exception as e:
        print(f"copy_message_fallback failed: {e}")
        return None


def register_msg_mapping(original_chat, original_msg_id, new_chat_id, new_msg_id):
    msg_map[(original_chat, original_msg_id)] = new_msg_id


# ---------------------------------------------------------------------------
# Describe service messages as readable text
# ---------------------------------------------------------------------------
def _describe_service_message(msg):
    """Extract a readable description from a Telegram service message."""
    parts = []

    # Pinned message
    if msg.pinned_message:
        pin_text = msg.pinned_message.text if msg.pinned_message.text else "(media message)"
        parts.append(f"Pinned message: {pin_text[:200]}")

    # New chat members
    if msg.new_chat_members:
        names = []
        for member in msg.new_chat_members:
            name = getattr(member, 'first_name', None) or getattr(member, 'title', None) or str(member.id)
            names.append(name)
        parts.append(f"New members joined: {', '.join(names)}")

    # Left chat member
    if msg.left_chat_member:
        name = getattr(msg.left_chat_member, 'first_name', None) or str(msg.left_chat_member.id)
        parts.append(f"Member left: {name}")

    # New chat title
    if msg.new_chat_title:
        parts.append(f"Chat renamed to: {msg.new_chat_title}")

    # New chat photo
    if msg.new_chat_photo:
        parts.append("Chat photo updated")

    # Delete chat photo
    if msg.delete_chat_photo:
        parts.append("Chat photo removed")

    # Group created
    if msg.group_chat_created:
        parts.append("Group chat created")

    # Supergroup created
    if msg.supergroup_chat_created:
        parts.append("Supergroup/channel created")

    # Channel created
    if msg.channel_chat_created:
        parts.append("Channel created")

    # Video chat started
    if msg.video_chat_started:
        parts.append("Video chat started")

    # Video chat ended
    if msg.video_chat_ended:
        parts.append("Video chat ended")

    # Video chat members invited
    if msg.video_chat_members_invited:
        names = []
        for member in msg.video_chat_members_invited.users:
            name = getattr(member, 'first_name', None) or str(member.id)
            names.append(name)
        parts.append(f"Video chat invited: {', '.join(names)}")

    # Forum topic created
    if msg.forum_topic_created:
        parts.append(f"Forum topic created: {msg.forum_topic_created.title}")

    # Forum topic edited
    if msg.forum_topic_edited:
        new_title = getattr(msg.forum_topic_edited, 'title', None)
        if new_title:
            parts.append(f"Forum topic renamed to: {new_title}")
        else:
            parts.append("Forum topic settings updated")

    # Forum topic closed
    if msg.forum_topic_closed:
        parts.append("Forum topic closed")

    # Forum topic reopened
    if msg.forum_topic_reopened:
        parts.append("Forum topic reopened")

    # General topic hidden/unhidden
    if msg.general_topic_hidden:
        parts.append("General topic hidden")
    if msg.general_topic_unhidden:
        parts.append("General topic unhidden")

    # Giveaway
    if msg.giveaway_created:
        parts.append("Giveaway created")
    if msg.giveaway:
        parts.append(f"Giveaway: {msg.giveaway.prize_description or 'prizes'} for {msg.giveaway.quantity} winner(s)")
    if msg.giveaway_winners:
        parts.append("Giveaway winners announced")
    if msg.giveaway_completed:
        parts.append("Giveaway completed")

    if parts:
        return '\n'.join(parts)
    return None


# ---------------------------------------------------------------------------
# Send Direct — file_id reuse (instant, no download)
# Works when the bot can see the source message (public channels or channels
# the bot is a member of). This is the devgaganin pattern for speed.
# ---------------------------------------------------------------------------
async def send_direct(client, msg, target_chat, caption=None):
    """
    Try to send media by reusing file_id from the source message.
    This is instant — no download/upload needed.
    Returns the sent message or None if not possible.
    """
    try:
        if msg.video:
            return await client.send_video(target_chat, msg.video.file_id, caption=caption)
        elif msg.photo:
            file_id = msg.photo.file_id if hasattr(msg.photo, 'file_id') else msg.photo[-1].file_id
            return await client.send_photo(target_chat, file_id, caption=caption)
        elif msg.document:
            # Smart file type detection by extension (devgaganin pattern)
            file_name = getattr(msg.document, 'file_name', '') or ''
            file_ext = os.path.splitext(file_name)[1].lower() if file_name else ''
            video_exts = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv', '.ts', '.m4v'}
            audio_exts = {'.mp3', '.m4a', '.flac', '.wav', '.ogg', '.opus', '.aac', '.wma'}

            if msg.document.mime_type and msg.document.mime_type.startswith('video/') or file_ext in video_exts:
                return await client.send_video(target_chat, msg.document.file_id, caption=caption)
            elif msg.document.mime_type and msg.document.mime_type.startswith('audio/') or file_ext in audio_exts:
                return await client.send_audio(target_chat, msg.document.file_id, caption=caption)
            else:
                return await client.send_document(target_chat, msg.document.file_id, caption=caption)
        elif msg.audio:
            return await client.send_audio(target_chat, msg.audio.file_id, caption=caption)
        elif msg.voice:
            return await client.send_voice(target_chat, msg.voice.file_id, caption=caption)
        elif msg.animation:
            return await client.send_animation(target_chat, msg.animation.file_id, caption=caption)
        elif msg.video_note:
            return await client.send_video_note(target_chat, msg.video_note.file_id)
        elif msg.sticker:
            return await client.send_sticker(target_chat, msg.sticker.file_id)
    except Exception as e:
        print(f"[DIRECT] send_direct failed (will fall back to download): {e}")
    return None


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

    # Before doing anything, ensure the bot client can resolve the target chat
    await ensure_target_peer(client, sender)

    # Determine if this message was pinned in the original chat
    was_pinned = False

    if 't.me/c/' in msg_link or 't.me/b/' in msg_link:
        if 't.me/b/' in msg_link:
            chat = str(msg_link.split("/")[-2])
        else:
            chat = int('-100' + str(msg_link.split("/")[-2]))
        file = ""
        try:
            # ---- Refresh dialogs so userbot knows about all channels ----
            try:
                async for _ in userbot.get_dialogs(limit=50):
                    pass
            except Exception:
                pass

            # Ensure the access hash for this channel is in userbot's cache.
            await resolve_peer_safe(userbot, chat)

            # ---- Try multiple chat ID formats (devgaganin pattern) ----
            msg = None
            chat_id_formats = [chat]

            # Also try the -XXX format if chat is -100XXX
            if isinstance(chat, int) and str(chat).startswith('-100'):
                alt_format = int('-' + str(chat)[5:])
                chat_id_formats.append(alt_format)

            for cid in chat_id_formats:
                try:
                    msg = await userbot.get_messages(cid, msg_id)
                    if msg and not getattr(msg, 'empty', False):
                        break
                except Exception:
                    continue

            # Final fallback: refresh all dialogs and retry
            if msg is None or getattr(msg, 'empty', False):
                try:
                    async for _ in userbot.get_dialogs(limit=200):
                        pass
                    msg = await userbot.get_messages(chat, msg_id)
                except Exception:
                    pass

            if msg is None or getattr(msg, 'empty', False):
                await client.edit_message_text(status_chat, edit_id, "Could not fetch the message. Is the userbot a member of this channel?")
                return

            # Check if this message was pinned in the original chat
            try:
                pinned_ids = await get_pinned_msg_ids(userbot, client, chat)
                was_pinned = msg_id in pinned_ids
                if was_pinned:
                    print(f"[PIN] Message {msg_id} was pinned in original chat")
            except Exception as e:
                print(f"[PIN] Could not check pinned status: {e}")

            # ---- SERVICE MESSAGE HANDLING ----
            # Service messages: pin notifications, join/leave, channel edits, etc.
            if msg.service:
                service_text = _describe_service_message(msg)
                if service_text:
                    sent_msg = await client.send_message(sender, f"📋 **Service Message:**\n{service_text}")
                    register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                    await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                else:
                    print(f"[INFO] Skipped service message {msg_id} (no extractable info)")
                await client.edit_message_text(status_chat, edit_id, "Saved service message.")
                return
            # ---- END SERVICE MESSAGE HANDLING ----

            # ---- POLL HANDLING ----
            if msg.poll is not None:
                edit = await client.edit_message_text(status_chat, edit_id, "Processing poll...")
                sent_msg = await forward_poll(client, sender, msg, edit, original_chat=chat, sender=sender)
                if sent_msg:
                    register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                    await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                await edit.delete()
                return
            # ---- END POLL HANDLING ----

            # ---- TEXT ONLY (no media) ----
            if not msg.media and msg.text:
                edit = await client.edit_message_text(status_chat, edit_id, "Cloning.")
                text = msg.text.markdown if msg.text else ""
                rewritten = rewrite_inline_links(text, chat, sender)
                sent_msg = await client.send_message(sender, rewritten)
                register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                await edit.delete()
                return

            # ---- WEB PAGE PREVIEW ----
            if msg.media == MessageMediaType.WEB_PAGE:
                edit = await client.edit_message_text(status_chat, edit_id, "Cloning.")
                text = msg.text.markdown if msg.text else ""
                rewritten = rewrite_inline_links(text, chat, sender)
                await client.send_message(sender, rewritten)
                await edit.delete()
                return

            # ---- DOWNLOADABLE MEDIA ----
            if msg.media in DOWNLOADABLE_MEDIA:
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

                # If download failed or file is empty, try copy_message as fallback
                if not file or not os.path.exists(file) or os.path.getsize(file) == 0:
                    print(f"[WARN] download_media returned empty/missing file for msg {msg_id}, trying copy_message fallback")
                    edit = await client.edit_message_text(status_chat, edit_id, "Trying to copy...")
                    caption = None
                    if msg.caption is not None:
                        caption = rewrite_inline_links(msg.caption, chat, sender)
                    sent_msg = await copy_message_fallback(userbot, sender, chat, msg_id, caption)
                    if sent_msg:
                        register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                        await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                        await edit.delete()
                    else:
                        await client.edit_message_text(status_chat, edit_id, f"Could not save message `{msg_link}`")
                    return

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
                    try:
                        sent_msg = await client.send_photo(
                            chat_id=sender,
                            photo=file,
                            caption=caption,
                            progress=progress_for_pyrogram,
                            progress_args=(
                                client,
                                '**UPLOADING:**\n',
                                edit,
                                time.time()
                            )
                        )
                    except Exception as photo_err:
                        print(f"send_photo failed, falling back to bot.send_file: {photo_err}")
                        sent_msg = await bot.send_file(sender, file, caption=caption)
                elif msg.media==MessageMediaType.STICKER:
                    # Send sticker as document to preserve it
                    sent_msg = await client.send_document(
                        sender, file,
                        caption=caption,
                        progress=progress_for_pyrogram,
                        progress_args=(
                            client, '**UPLOADING:**\n', edit, time.time()
                        )
                    )
                elif msg.media==MessageMediaType.ANIMATION:
                    # GIF / animation - send as animation
                    try:
                        sent_msg = await client.send_animation(
                            chat_id=sender,
                            animation=file,
                            caption=caption,
                            progress=progress_for_pyrogram,
                            progress_args=(
                                client, '**UPLOADING:**\n', edit, time.time()
                            )
                        )
                    except Exception:
                        sent_msg = await client.send_document(
                            sender, file,
                            caption=caption,
                            progress=progress_for_pyrogram,
                            progress_args=(
                                client, '**UPLOADING:**\n', edit, time.time()
                            )
                        )
                elif msg.media==MessageMediaType.AUDIO:
                    sent_msg = await client.send_audio(
                        chat_id=sender,
                        audio=file,
                        caption=caption,
                        progress=progress_for_pyrogram,
                        progress_args=(
                            client, '**UPLOADING:**\n', edit, time.time()
                        )
                    )
                elif msg.media==MessageMediaType.VOICE:
                    sent_msg = await client.send_voice(
                        chat_id=sender,
                        voice=file,
                        caption=caption,
                        progress=progress_for_pyrogram,
                        progress_args=(
                            client, '**UPLOADING:**\n', edit, time.time()
                        )
                    )
                else:
                    thumb_path=thumbnail(sender)
                    sent_msg = await client.send_document(
                        sender, file,
                        caption=caption,
                        thumb=thumb_path,
                        progress=progress_for_pyrogram,
                        progress_args=(
                            client, '**UPLOADING:**\n', edit, time.time()
                        )
                    )

                # Register mapping and pin the message
                if sent_msg:
                    register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                    await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)

                try:
                    os.remove(file)
                    if os.path.isfile(file) == True:
                        os.remove(file)
                except Exception:
                    pass
                await edit.delete()

            # ---- OTHER MEDIA (contact, location, venue, dice, game, etc.) ----
            # These can't be downloaded, so use copy_message via the userbot
            else:
                edit = await client.edit_message_text(status_chat, edit_id, "Copying message...")
                caption = None
                if msg.caption is not None:
                    caption = rewrite_inline_links(msg.caption, chat, sender)
                sent_msg = await copy_message_fallback(userbot, sender, chat, msg_id, caption)
                if sent_msg:
                    register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                    await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                else:
                    # Last resort: send whatever text we can extract
                    fallback_text = ""
                    if msg.text:
                        fallback_text = msg.text.markdown if msg.text else ""
                    if msg.caption:
                        fallback_text += ("\n" if fallback_text else "") + msg.caption
                    if fallback_text:
                        rewritten = rewrite_inline_links(fallback_text, chat, sender)
                        sent_msg = await client.send_message(sender, f"[Unsupported media] {rewritten}")
                        register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                        await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                    else:
                        print(f"[WARN] Could not save message {msg_id} — no text, no downloadable media")
                await edit.delete()
                return

        except (ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid):
            await client.edit_message_text(status_chat, edit_id, "Have you joined the channel?")
            return
        except PeerIdInvalid:
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
            or "number of file parts" in str(e) \
            or str(e) == "File size equals to 0 B":
                try:
                    if msg.media==MessageMediaType.VIDEO and msg.video.mime_type in ["video/mp4", "video/x-matroska"]:
                        UT = time.time()
                        uploader = await fast_upload(f'{file}', f'{file}', UT, bot, edit, '**UPLOADING:**')
                        attributes = [DocumentAttributeVideo(duration=duration, w=width, h=height, round_message=round_message, supports_streaming=True)]
                        sent_msg = await bot.send_file(sender, uploader, caption=caption, thumb=thumb_path, attributes=attributes, force_document=False)
                    elif msg.media==MessageMediaType.VIDEO_NOTE:
                        UT = time.time()
                        uploader = await fast_upload(f'{file}', f'{file}', UT, bot, edit, '**UPLOADING:**')
                        attributes = [DocumentAttributeVideo(duration=duration, w=width, h=height, round_message=round_message, supports_streaming=True)]
                        sent_msg = await bot.send_file(sender, uploader, caption=caption, thumb=thumb_path, attributes=attributes, force_document=False)
                    elif msg.media==MessageMediaType.PHOTO:
                        UT = time.time()
                        sent_msg = await bot.send_file(sender, file, caption=caption)
                    else:
                        UT = time.time()
                        uploader = await fast_upload(f'{file}', f'{file}', UT, bot, edit, '**UPLOADING:**')
                        sent_msg = await bot.send_file(sender, uploader, caption=caption, thumb=thumb_path, force_document=True)

                    if sent_msg:
                        register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                        await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)

                    if os.path.isfile(file) == True:
                        os.remove(file)
                except Exception as e2:
                    print(f"Telethon fallback also failed: {e2}")
                    # Telethon upload failed too — try copy_message as last resort
                    try:
                        os.remove(file)
                    except Exception:
                        pass
                    caption = None
                    if msg.caption is not None:
                        caption = rewrite_inline_links(msg.caption, chat, sender)
                    sent_msg = await copy_message_fallback(userbot, sender, chat, msg_id, caption)
                    if sent_msg:
                        register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                        await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                        await client.edit_message_text(status_chat, edit_id, "Saved via copy (upload failed).")
                    else:
                        await client.edit_message_text(status_chat, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e2)}')
                    return
            else:
                # Non-upload error — try copy_message fallback before giving up
                caption = None
                if msg.caption is not None:
                    caption = rewrite_inline_links(msg.caption, chat, sender)
                sent_msg = await copy_message_fallback(userbot, sender, chat, msg_id, caption)
                if sent_msg:
                    register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                    await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                    await client.edit_message_text(status_chat, edit_id, "Saved via copy (direct upload failed).")
                else:
                    await client.edit_message_text(status_chat, edit_id, f'Failed to save: `{msg_link}`\n\nError: {str(e)}')
                try:
                    os.remove(file)
                except Exception:
                    pass
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

            # ---- Bot can't see this public message? Try userbot ----
            if getattr(msg, 'empty', False):
                try:
                    await userbot.join_chat(chat)
                except Exception:
                    pass
                try:
                    chat_info = await userbot.get_chat(f"@{chat}" if not chat.startswith('-') else chat)
                    msg = await userbot.get_messages(chat_info.id, msg_id)
                except Exception:
                    pass

            # Check if this message was pinned in the original public chat
            try:
                pinned_ids = await get_pinned_msg_ids(userbot, client, chat)
                was_pinned = msg_id in pinned_ids
                if was_pinned:
                    print(f"[PIN] Message {msg_id} was pinned in original public chat")
            except Exception as e:
                print(f"[PIN] Could not check pinned status for public chat: {e}")

            # ---- POLL HANDLING for public chats ----
            if msg.poll is not None:
                sent_msg = await forward_poll(client, sender, msg, edit, original_chat=chat, sender=sender)
                if sent_msg:
                    register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                    await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                await edit.delete()
                return

            # ---- SERVICE MESSAGE for public chats ----
            if getattr(msg, 'service', False):
                service_text = _describe_service_message(msg)
                if service_text:
                    sent_msg = await client.send_message(sender, f"📋 **Service Message:**\n{service_text}")
                    register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                    await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                await edit.delete()
                return

            if getattr(msg, 'empty', False):
                new_link = f't.me/b/{chat}/{int(msg_id)}'
                return await get_msg(userbot, client, bot, sender, edit_id, status_chat, new_link, i)

            # ---- Try send_direct first (file_id reuse — instant, no download) ----
            caption = None
            if msg.caption is not None:
                caption = rewrite_inline_links(msg.caption, chat, sender)
            sent_msg = await send_direct(client, msg, sender, caption=caption)

            # ---- If send_direct worked, just register and pin ----
            if sent_msg:
                register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)
                # Rewrite inline links if there's text
                if msg.text:
                    original_text = msg.text.markdown if msg.text else ""
                    rewritten = rewrite_inline_links(original_text, chat, sender)
                    if rewritten != original_text:
                        try:
                            await client.edit_message_text(sender, sent_msg.id, rewritten)
                        except Exception as e:
                            print(f"Could not edit message for inline link rewriting: {e}")
                await edit.delete()
                return

            # ---- Fallback: copy_message for public chats ----
            sent_msg = await client.copy_message(sender, chat, msg_id)
            if sent_msg:
                register_msg_mapping(chat, msg_id, sender, sent_msg.id)
                await pin_if_channel(client, sender, sent_msg.id, was_pinned=was_pinned)

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
    x = await client.send_message(sender, "Processing!")
    await get_msg(userbot, client, Drone, sender, x.id, sender, msg_link, i)
