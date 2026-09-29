# Remove these imports:
# from telegram import Bot
# from telegram.error import TelegramError

def send_telegram_message(message: str):
    """Send message via Telegram bot using the HTTP API"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }
        response = requests.post(url, data=payload, timeout=20)
        if response.status_code == 200:
            print(f"[✓] Telegram message sent at {datetime.now(IST).strftime('%H:%M:%S')}")
        else:
            print(f"[✗] Telegram error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[✗] Error sending message: {e}")
