#!/bin/bash
# Sync from GitHub - handles unstaged changes gracefully
# This script stashes any runtime-modified files before pulling

cd /home/runner/workspace || exit 0

echo "[sync] Fetching latest changes from GitHub..."

# Stash any unstaged changes (session files, __pycache__, etc.)
git stash --include-untracked 2>/dev/null || true

# Pull with rebase
git pull --rebase origin main 2>/dev/null

if [ $? -eq 0 ]; then
    echo "[sync] Successfully pulled latest changes."
else
    # If pull still fails, try a hard reset to match remote
    echo "[sync] Pull failed, forcing clean state..."
    git fetch origin main 2>/dev/null
    git reset --hard origin/main 2>/dev/null
    echo "[sync] Reset to origin/main."
fi

# Restore any stashed changes that are still relevant
git stash pop 2>/dev/null || true
