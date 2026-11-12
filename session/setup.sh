#!/usr/bin/env bash
# Install the Gemini web proxy on the server.
#
#   bash setup.sh /opt/gemini_proxy_src
#
# Builds the proxy, installs the systemd units and prepares /etc/gemini-proxy (the accounts
# live there, root:hfnews 0640). The browser (the session owner) is a separate service.
set -euo pipefail
SRC=${1:-/opt/gemini_proxy_src}
DEST=/opt/gemini-proxy
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -d "$SRC" ] || { echo "no source at $SRC"; exit 1; }
command -v node >/dev/null || { echo "node is not installed"; exit 1; }
id hfnews >/dev/null 2>&1 || { echo "user hfnews is missing"; exit 1; }
id gemlogin >/dev/null 2>&1 || useradd -r -m -d /home/gemlogin -s /usr/sbin/nologin gemlogin

# The browser and the screen it lives on.
if ! command -v google-chrome-stable >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl
  curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /tmp/chrome.deb
  rm -f /tmp/chrome.deb
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xvfb x11vnc novnc websockify fonts-liberation jq

install -d -m 755 -o root -g root "$DEST"
rsync -a --delete --exclude=.venv --exclude=.git "$SRC/" "$DEST/"
install -d -m 750 -o root -g hfnews /etc/gemini-proxy
[ -f /etc/gemini-proxy/env ] || touch /etc/gemini-proxy/env
chmod 640 /etc/gemini-proxy/env
chown root:hfnews /etc/gemini-proxy/env

# The proxy needs Python 3.11+ (the gemini-webapi library). Ubuntu 22.04 ships 3.10, so take
# a standalone build from uv when there is no new enough system Python.
PY=""
for c in python3.13 python3.12 python3.11; do
  command -v "$c" >/dev/null && { PY=$c; break; }
done
if [ -n "$PY" ]; then
  "$PY" -m venv "$DEST/.venv"
else
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  command -v uv >/dev/null || { echo "uv is not available"; exit 1; }
  # uv keeps its Pythons under $HOME, and the proxy runs as hfnews: put them where the
  # service user can reach them.
  export UV_PYTHON_INSTALL_DIR=/opt/uv/python
  export UV_CACHE_DIR=/opt/uv/cache
  install -d -m 755 /opt/uv /opt/uv/python /opt/uv/cache
  uv python install 3.12
  uv venv --python 3.12 --seed --clear "$DEST/.venv"   # --seed: the venv gets pip
fi
chmod -R a+rX "$DEST/.venv"
"$DEST/.venv/bin/pip" -q install --upgrade pip
"$DEST/.venv/bin/pip" -q install -r "$DEST/requirements.txt"

# The browser owns the session: it needs its own profile and its own user.
install -d -m 700 -o gemlogin -g gemlogin "$DEST/profile"
install -d -m 755 -o gemlogin -g gemlogin /var/log/gemini
install -m 755 "$HERE/gemacc.mjs" "$DEST/gemacc.mjs"
install -m 644 "$HERE/notify.py" "$DEST/notify.py"

install -m 644 "$HERE"/units/*.service "$HERE"/units/*.timer /etc/systemd/system/
install -m 644 "$HERE/../gemini-proxy.service" /etc/systemd/system/gemini-proxy.service
systemctl daemon-reload

echo "installed. next:"
echo "  systemctl enable --now gem-xvfb gem-vnc gem-novnc gem-browser gemini-proxy"
echo "  systemctl enable --now gem-refresh.timer"
echo "  bash $HERE/login.sh          # sign a Google account in (noVNC on :6080)"
