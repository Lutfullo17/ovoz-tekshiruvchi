#!/usr/bin/env bash
#
# O'zbekistondagi Linux serverga o'rnatish.
#
#   bash deploy.sh
#
# Skript boshqa loyihalarga tegmaydi: o'z papkasida, o'z venv ida va
# alohida nomdagi systemd xizmatida ishlaydi.

set -euo pipefail

SERVICE_NAME="ovoz-bot"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$APP_DIR/.venv"
API_URL="https://new.openbudget.uz/api/v2/info/board/55?stage=PASSED&page=0&size=5&regionId=8&districtId=93"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m  %s\n' "$*"; }
err()  { printf '  \033[31mXATO\033[0m  %s\n' "$*"; }

# ---------------------------------------------------------------- 1. Tarmoq
say "1/5  Saytga ulanish tekshirilmoqda"

CODE=$(curl -sS -m 25 -o /dev/null -w '%{http_code}' "$API_URL" 2>/dev/null | tail -c 3)
[ -z "$CODE" ] && CODE="000"
if [ "$CODE" = "200" ]; then
    ok "sayt javob berdi (HTTP 200) — bu server O'zbekistonda"
else
    err "saytga ulanib bo'lmadi (HTTP $CODE)"
    echo
    echo "  new.openbudget.uz faqat O'zbekiston IP lariga javob beradi."
    echo "  Bu server chet elda joylashgan bo'lsa, bot bu yerda ishlamaydi."
    echo
    IP=$(curl -sS -m 10 https://api.ipify.org 2>/dev/null || echo "?")
    echo "  Server IP: $IP"
    curl -sS -m 10 "http://ip-api.com/line/?fields=country,city,isp" 2>/dev/null | sed 's/^/  /' || true
    echo
    echo "  Sabab aniqlash uchun:  bash diagnose.sh"
    echo
    exit 1
fi

# ---------------------------------------------------------------- 2. Python
say "2/5  Python muhiti"

command -v python3 >/dev/null || { err "python3 topilmadi"; exit 1; }
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
ok "python3 $PYV"

if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV" 2>/dev/null || {
        err "venv yaratilmadi — 'sudo apt install python3-venv' kerak bo'lishi mumkin"
        exit 1
    }
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
ok "kutubxonalar o'rnatildi (alohida venv, tizim Python iga tegilmadi)"

# ---------------------------------------------------------------- 3. Sozlama
say "3/5  Sozlamalar"

if [ -f "$APP_DIR/.env" ]; then
    ok ".env mavjud — o'zgartirilmadi"
else
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "  .env yaratildi. Tokenlarni kiriting:"
    echo
    read -rp "  BOT_TOKEN: " V && sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=$V|" "$APP_DIR/.env"
    read -rp "  CHAT_ID: " V && sed -i "s|^CHAT_ID=.*|CHAT_ID=$V|" "$APP_DIR/.env"
    read -rp "  GROQ_API_KEY: " V && sed -i "s|^GROQ_API_KEY=.*|GROQ_API_KEY=$V|" "$APP_DIR/.env"
    ok ".env to'ldirildi (huquqlar: faqat egasi o'qiydi)"
fi

# ---------------------------------------------------------------- 4. Sinov
say "4/5  Sinov (Telegramga yubormasdan)"

if "$VENV/bin/python" "$APP_DIR/bot.py" --once --dry-run 2>&1 | tail -14; then
    ok "bot ishlayapti"
else
    err "sinov muvaffaqiyatsiz — yuqoridagi xabarga qarang"
    exit 1
fi

# ------------------------------------------------------------- 5. Xizmat
say "5/5  systemd xizmati"

if ! command -v systemctl >/dev/null; then
    err "systemd yo'q. Qo'lda ishga tushiring:"
    echo "    nohup $VENV/bin/python $APP_DIR/bot.py > /dev/null 2>&1 &"
    exit 0
fi

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

$SUDO tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<UNIT
[Unit]
Description=OpenBudget ovoz monitoringi (Quyi Tegana)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV/bin/python $APP_DIR/bot.py
Restart=always
RestartSec=30

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now "$SERVICE_NAME"
sleep 3

if $SUDO systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "xizmat ishga tushdi"
else
    err "xizmat ishga tushmadi"
    $SUDO systemctl status "$SERVICE_NAME" --no-pager -l | tail -20
    exit 1
fi

say "Tayyor"
cat <<INFO
  Bot har 30 daqiqada ishlaydi va Telegramga rasm yuboradi.
  Kechasi 01:00-06:00 da jim turadi (ma'lumot yig'ilaveradi).

  Holat:      sudo systemctl status $SERVICE_NAME
  Jonli log:  sudo journalctl -u $SERVICE_NAME -f
  To'xtatish: sudo systemctl disable --now $SERVICE_NAME
INFO
