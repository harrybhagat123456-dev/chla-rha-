#!/bin/bash
# Startup script: clean runtime files, sync from GitHub, then launch the bot

# Clean up files that cause git conflicts during sync
find . -name "*.session" -not -path "./.git/*" -delete 2>/dev/null
find . -name "*.session-journal" -not -path "./.git/*" -delete 2>/dev/null
find . -name "__pycache__" -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null
find . -name "downloads" -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null

# Sync from GitHub
if [ -f "sync_from_github.sh" ]; then
    bash sync_from_github.sh
fi

# Start the bot
echo "starting Bot ~@DroneBots";
python3 -m main
