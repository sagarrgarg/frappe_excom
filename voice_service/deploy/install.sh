#!/usr/bin/env bash
# Install or upgrade the voice service on a Debian/Ubuntu VM, as root:
#   sudo ./deploy/install.sh            (from the voice_service directory)
# Then fill in /etc/excom-voice.env and: systemctl restart excom-voice
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX=/opt/excom-voice
PYTHON="${PYTHON:-python3.12}"

if [[ $EUID -ne 0 ]]; then
	echo "run as root" >&2
	exit 1
fi
command -v "$PYTHON" >/dev/null || { echo "$PYTHON is required" >&2; exit 1; }

# The unit runs as this user: no login, no home, nothing it can write.
if ! id excom-voice >/dev/null 2>&1; then
	useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin excom-voice
fi

install -d -m 755 "$PREFIX"
[[ -x "$PREFIX/venv/bin/pip" ]] || "$PYTHON" -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet -r "$SRC/requirements.txt"
"$PREFIX/venv/bin/pip" install --quiet --no-deps --force-reinstall "$SRC"

if [[ ! -f /etc/excom-voice.env ]]; then
	install -m 640 -o root -g excom-voice "$SRC/.env.example" /etc/excom-voice.env
	echo "edit /etc/excom-voice.env (FRAPPE_URL, VOICE_KEY, DEEPGRAM_API_KEY), then: systemctl restart excom-voice"
fi

install -m 644 "$SRC/deploy/excom-voice.service" /etc/systemd/system/excom-voice.service
systemctl daemon-reload
systemctl enable excom-voice
systemctl restart excom-voice || true  # fails until the env file is filled in
echo "installed; TLS: see deploy/Caddyfile"
