#!/bin/bash
# Startup script: sync from GitHub then launch the bot
bash sync_from_github.sh
python3 -m main
