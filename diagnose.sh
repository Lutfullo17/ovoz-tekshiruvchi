#!/usr/bin/env bash
#
# Server new.openbudget.uz ga nega ulana olmayotganini aniqlaydi.
#
#   bash diagnose.sh
#
# Hech narsani o'zgartirmaydi — faqat tekshiradi.

HOST="new.openbudget.uz"
IP="94.158.57.152"      # saytning haqiqiy IP si (O'zbekistondan aniqlangan)
URL="https://$HOST/api/v2/info/board/55?stage=PASSED&page=0&size=5&regionId=8&districtId=93"

line() { printf '\n\033[1m%s\033[0m\n' "$*"; }

line "0. Server haqida"
echo "   Chiqish IP: $(curl -sS -m 10 https://api.ipify.org 2>/dev/null || echo '?')"
curl -sS -m 10 "http://ip-api.com/line/?fields=country,city,isp,as" 2>/dev/null | sed 's/^/   /'
echo "   curl: $(curl --version 2>/dev/null | head -1)"

line "1. DNS"
if command -v getent >/dev/null; then
    getent ahosts "$HOST" | awk '{print "   " $1}' | sort -u || echo "   YECHILMADI"
else
    nslookup "$HOST" 2>&1 | sed 's/^/   /' | tail -5
fi

line "2. 443-port ochiqmi (DNS orqali)"
if timeout 8 bash -c "cat < /dev/null > /dev/tcp/$HOST/443" 2>/dev/null; then
    echo "   OCHIQ"
else
    echo "   YOPIQ yoki javobsiz"
fi

line "3. 443-port ochiqmi (to'g'ridan-to'g'ri IP: $IP)"
if timeout 8 bash -c "cat < /dev/null > /dev/tcp/$IP/443" 2>/dev/null; then
    echo "   OCHIQ  -> muammo DNS da"
else
    echo "   YOPIQ  -> tarmoq/marshrut darajasida to'siq"
fi

line "4. curl (faqat IPv4)"
curl -4 -sS -m 20 -o /dev/null -w '   HTTP %{http_code} | ulanish %{time_connect}s | jami %{time_total}s\n' \
     "$URL" 2>&1 | sed 's/^curl/   curl/' || true

line "5. curl (batafsil, birinchi 20 qator)"
curl -v -m 20 -o /dev/null "$URL" 2>&1 | head -20 | sed 's/^/   /'

line "6. Boshqa .uz saytlari ishlayaptimi"
for t in "https://my.gov.uz" "https://soliq.uz" "https://openbudget.uz"; do
    C=$(curl -sS -m 12 -o /dev/null -w '%{http_code}' "$t" 2>/dev/null || echo "000")
    printf '   %-28s HTTP %s\n' "$t" "$C"
done

line "7. Umuman internet bormi"
for t in "https://api.ipify.org" "https://api.telegram.org" "https://api.groq.com"; do
    C=$(curl -sS -m 12 -o /dev/null -w '%{http_code}' "$t" 2>/dev/null || echo "000")
    printf '   %-28s HTTP %s\n' "$t" "$C"
done

line "8. Marshrut (traceroute)"
if command -v traceroute >/dev/null; then
    traceroute -n -m 12 -w 2 "$IP" 2>&1 | sed 's/^/   /'
elif command -v tracepath >/dev/null; then
    tracepath -n -m 12 "$IP" 2>&1 | sed 's/^/   /'
else
    echo "   traceroute o'rnatilmagan (ixtiyoriy: apt install traceroute)"
fi

line "Tugadi"
echo "  Shu natijani to'liq nusxalab yuboring."
