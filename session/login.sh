#!/usr/bin/env bash
# Sign a Google account in.
#
# The browser opens on the virtual screen of the server. Reach it with noVNC:
#
#   ssh -L 6080:127.0.0.1:6080 root@144.31.148.243
#   http://localhost:6080/vnc.html
#
# Sign in there. The script waits for the session, reads the cookies and writes the accounts
# file. The browser stays alive: it owns the session and keeps the cookies fresh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

export ACCOUNTS_FILE=/etc/gemini-proxy/accounts.json
export GEMINI_BASE=/opt/gemini-proxy
export GEMINI_PROFILE=/opt/gemini-proxy/profile
export GEMINI_CDP_PORT=9334

systemctl start gem-xvfb gem-vnc gem-novnc gem-browser
sleep 3
echo
echo "noVNC: открой http://localhost:6080/vnc.html (через туннель ssh -L 6080:127.0.0.1:6080 ...)"
echo

cd /opt/gemini-proxy
node gemacc.mjs login "$@"

echo
echo "проверка:"
ACCOUNTS_FILE=/etc/gemini-proxy/accounts.json /opt/gemini-proxy/.venv/bin/python -m gemini_proxy check
