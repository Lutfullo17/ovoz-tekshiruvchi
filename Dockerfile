FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Tashkent

WORKDIR /app

# Pillow uchun runtime kutubxonalari (wheel'lar bilan ko'pincha shart emas,
# lekin arm64 kabi platformalarda kerak bo'ladi)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fonts/ ./fonts/
COPY *.py ./

# Baza va log uchun doimiy hajm
VOLUME ["/app/data"]
ENV DB_PATH=data/history.db \
    LOG_PATH=data/bot.log

# Root bo'lmagan foydalanuvchi
RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /app/data \
    && chown -R botuser:botuser /app
USER botuser

CMD ["python", "bot.py"]
