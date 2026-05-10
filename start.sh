#!/bin/bash
# Startup script: clean state, sync from GitHub, then launch the bot

# Abort any leftover rebase/merge from previous failed sync
cd /home/runner/workspace 2>/dev/null || true
git rebase --abort 2>/dev/null || true
git merge --abort 2>/dev/null || true

# Clean up runtime files that cause git conflicts
find . -name "*.session" -not -path "./.git/*" -delete 2>/dev/null
find . -name "*.session-journal" -not -path "./.git/*" -delete 2>/dev/null
find . -name "__pycache__" -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null
find . -name "downloads" -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null

# Sync from GitHub (hard reset to match remote)
if [ -f "sync_from_github.sh" ]; then
    bash sync_from_github.sh
fi

# Install tesseract-ocr system package for OCR (needed by pytesseract)
if ! command -v tesseract &> /dev/null; then
    echo "[SETUP] Installing tesseract-ocr for image OCR..."
    apt-get update -qq 2>/dev/null && apt-get install -y -qq tesseract-ocr tesseract-ocr-eng 2>/dev/null
    pip install pytesseract Pillow duckduckgo-search 2>/dev/null
fi

# Start the bot
echo "starting Bot ~@DroneBots";
python3 -m main
