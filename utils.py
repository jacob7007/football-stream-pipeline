import os
import re
import requests
from datetime import datetime, timezone, timedelta
import logger

def load_env():
    """Load local .env file if it exists, without overwriting existing env vars."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val

def send_telegram_message(bot_token, chat_id, text):
    """Send a message via the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            logger.error(f"Telegram: sendMessage failed (HTTP {r.status_code}): {r.text}")
    except Exception as e:
        logger.error(f"Telegram: Failed to send message: {e}")

def format_to_human_time(iso_time_str: str) -> str:
    """
    Converts ISO 8601 time string (GMT+1) to user format: "2 July - 00:21 (UTC+1)"
    """
    if not iso_time_str:
        return ""
    try:
        clean_str = re.sub(r'([+-]\d{2}:?\d{2}|Z)$', '', iso_time_str.strip())
        dt = datetime.fromisoformat(clean_str)
        day = dt.day
        month_name = dt.strftime("%B")
        time_part = dt.strftime("%H:%M")
        return f"{day} {month_name} - {time_part} (UTC+1)"
    except Exception:
        return iso_time_str

def get_now_gmt1() -> datetime:
    """Returns the current time in GMT+1 timezone (naive datetime)."""
    return (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)

def get_now_gmt3() -> datetime:
    """Returns the current time in GMT+3 (KSA) timezone (naive datetime)."""
    return (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None)


