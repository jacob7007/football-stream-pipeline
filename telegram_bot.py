import os
import sys
import json
import subprocess
from datetime import datetime
import requests

# Import project modules from current directory
import sheets_module
import scraper_module
import blogger_module
import logger

# Blogger Config
BLOG_A_ID = "2885706943982996652"       # Tivivi Edu (Iframe Slots Blog)
SPREADSHEET_NAME = "Matches - Slots state"

def send_telegram_message(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            logger.error(f"Telegram: sendMessage failed (HTTP {r.status_code}): {r.text}")
    except Exception as e:
        logger.error(f"Telegram: Failed to send message: {e}")

def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "8834247606:AAHKuuiL1TIjWo3nPJjMBfonSrm8hR_91NQ")
    allowed_chat_ids_raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "1324494633,587683065")
    allowed_chat_ids = [x.strip() for x in allowed_chat_ids_raw.split(",") if x.strip()] if allowed_chat_ids_raw else []

    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    
    # 1. Fetch updates
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Telegram: getUpdates failed with status {resp.status_code}: {resp.text}")
            sys.exit(1)
            
        updates = resp.json().get("result", [])
    except Exception as e:
        logger.error(f"Telegram: Error polling updates: {e}")
        sys.exit(1)

    if not updates:
        # Exit silently and instantly to save resources
        sys.exit(0)

    logger.info(f"Telegram Bot: Processing {len(updates)} updates.")

    max_update_id = -1
    
    # Initialize clients on-demand if there are updates to process
    sheets_client = None
    blogger_session = None
    
    for update in updates:
        update_id = update.get("update_id")
        if update_id > max_update_id:
            max_update_id = update_id
            
        message = update.get("message")
        if not message:
            continue
            
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text", "").strip()
        
        if not text or not chat_id:
            continue
            
        # Verify chat ID is allowed
        if allowed_chat_ids and str(chat_id) not in allowed_chat_ids:
            logger.warning(f"Telegram: Unauthorized access attempt from chat ID {chat_id}")
            continue
            
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        
        logger.info(f"Telegram: Command '{cmd}' received from chat {chat_id}")
        
        if cmd == "/end":
            if not arg:
                send_telegram_message(bot_token, chat_id, "Usage: /end <team_name>")
                continue
                
            # Initialize sheets client and fetch caches
            if sheets_client is None:
                sheets_client = sheets_module.get_gspread_client()
                
            team_translations = sheets_module.fetch_team_translations_separated(sheets_client, SPREADSHEET_NAME)
            matches_cache = sheets_module.fetch_matches_cache(sheets_client, SPREADSHEET_NAME)
            
            # Scrape matches to find the target URL and event details
            logger.info("Telegram Bot: Scraping live matches to find target event...")
            scraped_events, _, _ = scraper_module.scrape_live_matches(
                use_mock=False,
                team_translations=team_translations,
                matches_cache=matches_cache
            )
            
            found_match = None
            for ev in scraped_events:
                t1_en = ev['team1'].get('nameEn') or ""
                t1_ar = ev['team1'].get('nameAr') or ""
                t2_en = ev['team2'].get('nameEn') or ""
                t2_ar = ev['team2'].get('nameAr') or ""
                
                if (arg.lower() in t1_en.lower() or 
                    arg.lower() in t1_ar.lower() or 
                    arg.lower() in t2_en.lower() or 
                    arg.lower() in t2_ar.lower()):
                    found_match = ev
                    break
                    
            if found_match:
                match_url = found_match["match_url"]
                t1_display = found_match['team1'].get('nameEn') or found_match['team1'].get('nameAr')
                t2_display = found_match['team2'].get('nameEn') or found_match['team2'].get('nameAr')
                match_name = f"{t1_display} vs {t2_display}"
                
                # Fetch latest cache state
                matches_cache = sheets_module.fetch_matches_cache(sheets_client, SPREADSHEET_NAME)
                # Mark as finished in cache
                matches_cache[match_url] = {
                    "iframe_url": "",
                    "status_class": "finished",
                    "last_updated": datetime.now().isoformat()
                }
                
                # Save cache immediately
                sheets_module.save_matches_cache(sheets_client, matches_cache, SPREADSHEET_NAME)
                
                msg = f"Match '{match_name}' marked as ended. Triggering slot updates now..."
                send_telegram_message(bot_token, chat_id, msg)
                logger.success(f"Telegram Bot: Match '{match_name}' marked as finished in cache.")
                
                # Trigger run_pipeline.py immediately to free the slot and patch Blogger B
                logger.info("Telegram Bot: Triggering pipeline update subprocess...")
                subprocess.run([sys.executable, "run_pipeline.py", "--auto", "--telegram-report-chat-id", str(chat_id)])
            else:
                send_telegram_message(bot_token, chat_id, f"Could not find any match featuring '{arg}'.")
                
        elif cmd == "/match":
            if sheets_client is None:
                sheets_client = sheets_module.get_gspread_client()
            if blogger_session is None:
                blogger_session = blogger_module.get_blogger_session()
                
            slots = sheets_module.fetch_all_slots(sheets_client, SPREADSHEET_NAME)
            team_translations = sheets_module.fetch_team_translations_separated(sheets_client, SPREADSHEET_NAME)
            matches_cache = sheets_module.fetch_matches_cache(sheets_client, SPREADSHEET_NAME)
            
            logger.info("Telegram Bot: Fetching scraped matches...")
            scraped_events, _, _ = scraper_module.scrape_live_matches(
                use_mock=False,
                team_translations=team_translations,
                matches_cache=matches_cache
            )
            
            # Fetch Blog A slot permalinks
            try:
                posts_resp = blogger_module.fetch_all_posts(blogger_session, BLOG_A_ID)
                posts_list = posts_resp.get("items", [])
                posts_map = {p["id"]: p for p in posts_list}
            except Exception as e:
                logger.error(f"Telegram Bot: Error fetching Blogger posts: {e}")
                posts_map = {}
                
            match_lines = []
            for idx, ev in enumerate(scraped_events, 1):
                t1 = ev['team1'].get('nameEn') or ev['team1'].get('nameAr')
                t2 = ev['team2'].get('nameEn') or ev['team2'].get('nameAr')
                status = ev.get('status_class', 'unknown').upper()
                
                assigned_slot_label = ""
                permalink = ""
                for s in slots:
                    if s.get("status", "").strip().lower() == "active" and s.get("event_id") == ev["event_id"]:
                        assigned_slot_label = s.get("slot") or f"Row {s['row_num']}"
                        post_info = posts_map.get(s["post_id"])
                        if post_info:
                            permalink = post_info.get("url", "")
                        break
                        
                line = f"[{idx}] {t1} vs {t2} ({status})"
                if assigned_slot_label:
                    line += f"\n   Slot: {assigned_slot_label}"
                    if permalink:
                        line += f"\n   Link: {permalink}"
                else:
                    line += "\n   (Not streaming)"
                match_lines.append(line)
                
            if not match_lines:
                response_text = "No matches currently scraped."
            else:
                response_text = "Scraped Matches:\n\n" + "\n\n".join(match_lines)
                
            send_telegram_message(bot_token, chat_id, response_text)
            
        elif cmd == "/check":
            send_telegram_message(bot_token, chat_id, "Triggering pipeline execution immediately...")
            logger.info("Telegram Bot: Triggering pipeline check subprocess...")
            subprocess.run([sys.executable, "run_pipeline.py", "--auto", "--telegram-report-chat-id", str(chat_id)])
            
    # 2. Acknowledge updates
    if max_update_id != -1:
        try:
            requests.get(f"{url}?offset={max_update_id + 1}", timeout=10)
            logger.info(f"Telegram Bot: Acknowledged updates up to ID {max_update_id}")
        except Exception as e:
            logger.error(f"Telegram Bot: Failed to acknowledge updates: {e}")

if __name__ == "__main__":
    main()
