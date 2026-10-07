#!/bin/bash
# systemd（concierge-bot.service）から起動する
set -a
. /home/ikm/.config/concierge/env
set +a
export NVM_DIR="/home/ikm/.nvm"
. "$NVM_DIR/nvm.sh"
cd /home/ikm/concierge
exec /home/ikm/concierge/.venv/bin/python -u /home/ikm/concierge/bot.py
