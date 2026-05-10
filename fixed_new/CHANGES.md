# fixed_new — All Bug Fixes Applied

This folder contains the complete, fully fixed version of SaveRestrictedContentBot.
Point your Replit workspace root here if the root-level files have issues.

---

## What's Fixed vs. Original Repo

### 1. `.replit` — TOML Syntax Error (Replit deployment crash)

**Before (broken):**
```toml
[[deployment.build]]
command = "pip install -r requirements.txt"
```

**After (fixed):**
```toml
[deployment]
build = "pip install -r requirements.txt"
```

Replit's TOML parser does NOT support `[[array_of_tables]]` under `[deployment]`.
`deployment.build` must be a plain string.

Also added: `entrypoint = "main/__main__.py"`, `run = ["sh", "-c", "bash start.sh"]`

---

### 2. `main/__init__.py` — cast=int crashes on None

**Before (crashes when AUTH/API_ID/SAVE_CHANNEL is unset):**
```python
API_ID = config("API_ID", default=None, cast=int)
AUTH = config("AUTH", default=None, cast=int)
```

**After (safe):**
```python
_safe_int = lambda x: int(x) if x is not None else None
API_ID = config("API_ID", default=None, cast=_safe_int)
AUTH = config("AUTH", default=None, cast=_safe_int)
SAVE_CHANNEL = config("SAVE_CHANNEL", default=None, cast=_safe_int)
```

Also removed redundant `int()` wrappers: `api_id=API_ID` (was `int(API_ID)`),
`Bot.get_chat(SAVE_CHANNEL)` (was `Bot.get_chat(int(SAVE_CHANNEL))`).

---

### 3. `main/__init__.py` — Pyrogram get_peer_type() monkey-patch

Without this, Pyrogram's `handle_updates()` crashes with:
`ValueError: Peer id invalid: -100XXXXXXXXXX`

The patch returns "channel"/"chat"/"user" instead of crashing.

---

### 4. `main/__init__.py` — Bot dialog pre-caching + SAVE_CHANNEL peer resolution

At startup, walks `Bot.get_dialogs()` to cache all peer access hashes.
Then resolves SAVE_CHANNEL peer specifically. Prevents PeerIdInvalid at runtime.

---

### 5. `main/plugins/pyroplug.py` — Poll forwarding with correct answers

- Re-creates polls using `client.send_poll()` with `poll.correct_option_index`
- **Critical**: `poll.options[i].data` is bytes in Pyrogram v4, NOT a dict.
  Never call `opt.data.get()` — it crashes.
- Sends a "Vote Summary" follow-up message with original vote counts
- Handles both regular and quiz polls

---

### 6. `main/plugins/pyroplug.py` — Message pinning

Every saved message is pinned to SAVE_CHANNEL via `pin_if_channel()`.
Uses `both_sides=False` so only the pinner is notified.
Silently fails if bot lacks pin permissions.

---

### 7. `main/plugins/pyroplug.py` — Inline link rewriting

Rewrites `t.me/c/CHANNELID/MSGID` links in messages to point to the
saved copies in the user's SAVE_CHANNEL. Uses `msg_map` dictionary:
`(original_chat, original_msg_id) -> new_msg_id`

Supports private links (`t.me/c/...`) and public links (`t.me/channel/...`).

---

### 8. `main/plugins/pyroplug.py` — Copy ALL message types (no skipping)

**Before**: Bot skipped stickers, animations, contacts, locations, venues, dice, games.

**After**: 3-tier fallback system:

| Priority | Strategy | Message Types |
|----------|----------|---------------|
| 1 | Download + Upload | Video, Photo, Document, Audio, Animation, Voice, Sticker |
| 2 | `userbot.copy_message()` | Contact, Location, Venue, Dice, Game, any download failure |
| 3 | Text extraction | Sends `[Unsupported media]` with any available text |

---

### 9. `main/plugins/pyroplug.py` — PeerIdInvalid fix (mixed chat context)

**Before**: `frontend.py` sent "Processing!" to user DM, then `get_msg()` tried
`client.edit_message_text(SAVE_CHANNEL, edit_id_from_DM, ...)` — message doesn't
exist in channel.

**After**: Added `status_chat` parameter. Status messages go to user DM,
content goes to SAVE_CHANNEL.

---

### 10. `main/plugins/pyroplug.py` — Photo upload fix

Photos now sent via `client.send_photo()` (Pyrogram) first.
Falls back to `bot.send_file()` (Telethon) only if send_photo fails.

---

### 11. `main/plugins/frontend.py` — SAVE_CHANNEL already int

Removed `int(SAVE_CHANNEL)` since `__init__.py` now casts it.
Changed: `target = int(SAVE_CHANNEL) if SAVE_CHANNEL` -> `target = SAVE_CHANNEL if SAVE_CHANNEL`

---

### 12. `sync_from_github.sh` — Git hard reset instead of rebase

Uses `git fetch` + `git reset --hard origin/main` instead of `git pull --rebase`.
Prevents rebase conflicts when GitHub history is force-pushed.

---

### 13. `start.sh` — Cleans runtime files before sync

Aborts leftover rebases, deletes session files, __pycache__, downloads
before calling sync_from_github.sh.

---

### 14. `.gitignore` — Added session files, downloads, .env

```
__pycache__/
*.session
*.session-journal
downloads/
*.jpg
*.pyc
.env
```

---

## How to Use This Folder

### Option A: Point Replit here
1. In Replit, change your workspace to point to this folder
2. Or copy all files from `fixed_new/` to your workspace root
3. Set your Replit Secrets (API_ID, API_HASH, BOT_TOKEN, SESSION, SAVE_CHANNEL)
4. Hit Run

### Option B: Replace root files
```bash
# From your repo root:
cp -r fixed_new/* .
cp fixed_new/.replit .
cp fixed_new/.gitignore .
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| API_ID | Yes | Telegram API ID (from my.telegram.org) |
| API_HASH | Yes | Telegram API Hash |
| BOT_TOKEN | Yes | Bot token from @BotFather |
| SESSION | Yes | Pyrogram StringSession (userbot session) |
| FORCESUB | No | Channel username for force-subscribe |
| AUTH | No | User ID(s) authorized to use /batch |
| SAVE_CHANNEL | No* | Channel ID (with -100 prefix) for saving content |

*SAVE_CHANNEL required for pinning and inline link rewriting.
