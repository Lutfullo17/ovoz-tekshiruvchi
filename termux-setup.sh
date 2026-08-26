#!/data/data/com.termux/files/usr/bin/bash
#
# Android telefonda (Termux) o'rnatish.
#
#   bash termux-setup.sh
#
# Telefon mobil internet yoki uy Wi-Fi sida bo'ladi — bu O'zbekiston
# IP si, ya'ni openbudget.uz ochiladi.

set -eu

# Xato bo'lsa jimgina o'lmasin — qaysi qatorda uzilganini aytsin
on_error() {
    echo ""
    echo "XATO: skript $1-qatorda to'xtadi."
    echo "Yuqoridagi xabarni nusxalab yuboring."
}
trap 'on_error $LINENO' ERR

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
API_URL="https://new.openbudget.uz/api/v2/info/board/55?stage=PASSED&page=0&size=5&regionId=8&districtId=93"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[32mOK\033[0m  %s\n' "$*"; }
err() { printf '  \033[31mXATO\033[0m  %s\n' "$*"; }

say "1/5  Saytga ulanish"
CODE=$(curl -sS -m 25 -o /dev/null -w '%{http_code}' "$API_URL" 2>/dev/null || echo 000)
if [ "$CODE" = "200" ]; then
    ok "sayt ochildi — bu telefon ishlaydi"
else
    err "sayt ochilmadi (HTTP $CODE)"
    echo "  Wi-Fi ni o'chirib mobil internetga o'ting yoki teskarisi, qayta urinib ko'ring."
    exit 1
fi

say "2/5  Kerakli dasturlar"
# DIQQAT: bu yerda chiqishni yashirmaymiz. Yashirilsa xato sababi
# ko'rinmay qoladi va skript jimgina to'xtaydi.

echo "  Python o'rnatilmoqda (1-3 daqiqa)..."
if ! pkg install -y python git; then
    err "pkg install muvaffaqiyatsiz. Yuqoridagi xabarga qarang."
    echo "  Ko'p uchraydigan yechim:  pkg update && pkg upgrade"
    exit 1
fi
ok "python $(python -c 'import sys;print("%d.%d"%sys.version_info[:2])')"

# Pillow — tayyor paket bo'lsa shuni olamiz (telefonda kompilyatsiya juda uzoq).
echo "  Pillow o'rnatilmoqda..."
if pkg install -y python-pillow 2>/dev/null && python -c "import PIL" 2>/dev/null; then
    ok "Pillow (tayyor paket)"
elif python -c "import PIL" 2>/dev/null; then
    ok "Pillow allaqachon bor"
else
    echo "  Tayyor paket yo'q — manbadan yig'iladi, 5-10 daqiqa ketishi mumkin."
    pkg install -y libjpeg-turbo libpng zlib freetype || true
    if ! pip install pillow; then
        err "Pillow o'rnatilmadi. Yuqoridagi xabarni menga yuboring."
        exit 1
    fi
    ok "Pillow (manbadan)"
fi

echo "  httpx va APScheduler o'rnatilmoqda..."
pip install --upgrade pip > /dev/null 2>&1 || true
if ! pip install httpx apscheduler; then
    err "kutubxonalar o'rnatilmadi. Yuqoridagi xabarga qarang."
    exit 1
fi
ok "httpx, APScheduler"

# Hammasi haqiqatan import bo'lyaptimi — sinovdan oldin tekshiramiz
if ! python -c "import PIL, httpx, apscheduler" 2>&1; then
    err "kutubxonalar import bo'lmadi"
    exit 1
fi
ok "barcha kutubxonalar joyida"

say "3/5  Sozlamalar"
if [ -f "$APP_DIR/.env" ]; then
    ok ".env mavjud — o'zgartirilmadi"
else
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "  Tokenlarni kiriting (Telegramdan nusxa olib qo'ying):"
    echo
    printf "  BOT_TOKEN: ";     read -r V; sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=$V|" "$APP_DIR/.env"
    printf "  CHAT_ID (guruh): "; read -r V; sed -i "s|^CHAT_ID=.*|CHAT_ID=$V|" "$APP_DIR/.env"
    printf "  ADMIN_CHAT_ID (siz): "; read -r V; sed -i "s|^ADMIN_CHAT_ID=.*|ADMIN_CHAT_ID=$V|" "$APP_DIR/.env"
    printf "  GROQ_API_KEY: "; read -r V; sed -i "s|^GROQ_API_KEY=.*|GROQ_API_KEY=$V|" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    ok ".env to'ldirildi"
fi

say "4/5  Sinov (yubormasdan)"
cd "$APP_DIR"
python bot.py --once --dry-run 2>&1 | tail -12

say "5/5  Doimiy ishga tushirish"
# Wake lock — Android jarayonni uxlatib qo'ymasligi uchun
termux-wake-lock 2>/dev/null || echo "  (termux-wake-lock topilmadi, Termux:API o'rnatilmagan bo'lishi mumkin)"

pkill -f "python bot.py" 2>/dev/null || true
nohup python bot.py > "$APP_DIR/nohup.log" 2>&1 &
sleep 5

if pgrep -f "python bot.py" > /dev/null; then
    ok "bot ishga tushdi (PID $(pgrep -f 'python bot.py' | head -1))"
else
    err "bot ishga tushmadi"
    tail -20 "$APP_DIR/nohup.log"
    exit 1
fi

say "Tayyor"
cat <<INFO
  Bot har 30 daqiqada guruhga rasm yuboradi.
  Telefonni zaryadga qo'yib qo'ying, Termux ni yopmang.

  Log ko'rish:   tail -f $APP_DIR/bot.log
  To'xtatish:    pkill -f "python bot.py"
  Qayta yoqish:  cd $APP_DIR && nohup python bot.py > nohup.log 2>&1 &

  MUHIM: Android sozlamalari > Ilovalar > Termux > Batareya >
  "Cheklanmagan" (Unrestricted) qilib qo'ying, aks holda Android
  botni o'ldirib qo'yadi.
INFO
