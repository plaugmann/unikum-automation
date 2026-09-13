#!/usr/bin/env bash
# Opsaetning paa Raspberry Pi OS (64-bit).
#
# Koer fra projektmappen:  bash deploy/install-pi.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
cd "$ROOT"

echo "==> Systempakker"
sudo apt-get update
# Playwright har ingen egen Chromium til ARM64, saa vi bruger systemets.
sudo apt-get install -y python3-venv python3-pip chromium

CHROMIUM="$(command -v chromium || command -v chromium-browser || true)"
if [ -z "$CHROMIUM" ]; then
  echo "FEJL: fandt ikke chromium. Installer den og koer igen." >&2
  exit 1
fi
echo "    chromium: $CHROMIUM"

echo "==> Python-miljoe"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
# Bevidst UDEN "playwright install": Playwright har ingen Chromium til
# ARM64, saa vi bruger systemets via CHROMIUM_PATH.

echo "==> Kontrol"
./.venv/bin/python -c "import playwright, httpx, openai, bs4, pypdf, docx, fastapi; print('    alle pakker importeres')"

echo "==> .env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    oprettede .env - udfyld OPENAI_API_KEY, FEED_TOKEN, CLOUD_URL og CLOUD_PUSH_TOKEN"
fi
if grep -q '^CHROMIUM_PATH=$' .env; then
  sed -i "s|^CHROMIUM_PATH=$|CHROMIUM_PATH=$CHROMIUM|" .env
  echo "    satte CHROMIUM_PATH=$CHROMIUM"
fi

echo "==> systemd"
sed -e "s|/home/pi/unikum-automation|$ROOT|g" \
    -e "s|Environment=HOME=/home/pi|Environment=HOME=$HOME|" \
    -e "s|^\(\[Service\]\)|\1\nUser=$USER_NAME|" \
    deploy/unikum-update.service | sudo tee /etc/systemd/system/unikum-update.service > /dev/null
sudo cp deploy/unikum-update.timer /etc/systemd/system/unikum-update.timer
sudo systemctl daemon-reload
sudo systemctl enable --now unikum-update.timer

echo
echo "Faerdig. Naeste skridt:"
echo "  1. Udfyld .env"
echo "  2. Log ind med BankID:   ./.venv/bin/python -m unikum.cli login"
echo "  3. Proev en koersel:     ./.venv/bin/python scripts/update.py"
echo "  4. Se tidsplanen:        systemctl list-timers unikum-update.timer"
echo "  5. Se loggen:            tail -f data/logs/update.log"
