#!/bin/bash
# Sync from GitHub - hard reset to match remote exactly
# The deployment server should NEVER try to rebase or push.
# It should always be a mirror of what's on GitHub.

cd /home/runner/workspace || exit 0

echo "[sync] Fetching latest changes from GitHub..."

# Fetch the latest from origin
git fetch origin main 2>/dev/null

if [ $? -ne 0 ]; then
    echo "[sync] Fetch failed, will try again next cycle."
    exit 0
fi

# Hard reset to match origin/main exactly
# This discards any local changes (session files, __pycache__, etc.)
# and makes the local branch identical to the remote.
git reset --hard origin/main 2>/dev/null

if [ $? -eq 0 ]; then
    echo "[sync] Successfully synced to latest changes."
else
    echo "[sync] Reset failed."
fi

# Clean untracked files (session files, downloads, __pycache__)
git clean -fd 2>/dev/null || true
