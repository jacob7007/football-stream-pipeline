import os
import sys
import json
import argparse
from datetime import datetime

# Load local .env file if it exists
def load_env():
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

load_env()

# Import project modules
import sheets_module
import blogger_module
import scraper_module
import reconciler
import patcher
import logger
import requests

# Configure stdout/stderr for UTF-8
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# Blogger Config (Loaded from Environment Variables)
BLOG_PLAYER_ID = os.environ.get("BLOG_PLAYER_ID")
BLOG_DATA_ID = os.environ.get("BLOG_DATA_ID")
DATA_PAGE_ID = os.environ.get("DATA_PAGE_ID")

SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Streaming Dashboard")

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
    if not BLOG_PLAYER_ID or not BLOG_DATA_ID or not DATA_PAGE_ID:
        logger.error("Missing required environment variables: BLOG_PLAYER_ID, BLOG_DATA_ID, or DATA_PAGE_ID")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Football Stream Automation Pipeline")
    parser.add_argument("--mock", action="store_true", help="Run scraper in mock mode")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without making write calls to Sheets or Blogger")
    parser.add_argument("--auto", action="store_true", help="Skip user confirmation prompts (useful for GitHub Actions)")
    parser.add_argument("--sheet", type=str, default=SPREADSHEET_NAME, help="Google Sheets spreadsheet name or ID")
    parser.add_argument("--telegram-report-chat-id", type=str, default="", help="Telegram chat ID to send the reconciliation report to")
    args = parser.parse_args()

    logger.step_header("START", "Starting Stream Pipeline Run")
    logger.info(f"Time: {datetime.now().isoformat()}")
    logger.info(f"Flags: mock={args.mock}, dry_run={args.dry_run}, auto={args.auto}, sheet={args.sheet}")
    print()

    # 1. Initialize Clients
    logger.step_header("1/7", "Initializing API clients")
    try:
        sheets_client = sheets_module.get_gspread_client()
        logger.success("Google Sheets client authorized.")
    except Exception as e:
        logger.error(f"Error initializing Google Sheets client: {e}")
        sys.exit(1)

    try:
        blogger_session = blogger_module.get_blogger_session()
        logger.success("Blogger API session authorized.")
    except Exception as e:
        logger.error(f"Error initializing Blogger API session: {e}")
        sys.exit(1)

    # 2. Fetch sheet blogs
    logger.step_header("2/7", f"Fetching blogs from Google Sheet '{args.sheet}'")
    try:
        blogs = sheets_module.fetch_all_blogs(sheets_client, args.sheet)
        logger.info(f"Found {len(blogs)} blog rows in Google Sheets:")
        print()
        
        # Calculate dynamic padding for alignment
        max_blog_len = max(len(s['blog'] if s.get('blog') else f"Row {s['row_num']}") for s in blogs) if blogs else 8
        max_id_len = max(len(s['post_id']) for s in blogs) if blogs else 19
        
        aligned_events = []
        for s in blogs:
            ev_name = s.get('event_name') or '(free)'
            # Replace ' vs ' with ' - ' in display
            ev_name_clean = ev_name.replace(" vs ", " - ")
            if " - " in ev_name_clean:
                t1, t2 = ev_name_clean.split(" - ", 1)
                aligned_events.append((t1.strip(), t2.strip()))
            else:
                aligned_events.append((ev_name_clean, ""))
                
        max_ev_t1 = max(len(item[0]) for item in aligned_events) if aligned_events else 15
        max_ev_t2 = max(len(item[1]) for item in aligned_events) if aligned_events else 15
        
        for idx, s in enumerate(blogs):
            blog_name = s['blog'] if s.get('blog') else f"Row {s['row_num']}"
            blog_status = (s['status'] if s.get('status') else "free").strip().lower()
            
            ev_t1, ev_t2 = aligned_events[idx]
            if ev_t2:
                aligned_ev = f"{ev_t1:<{max_ev_t1}} - {ev_t2:<{max_ev_t2}}"
            else:
                # it's '(free)'
                aligned_ev = f"{ev_t1:<{max_ev_t1}}   {'':<{max_ev_t2}}"
                
            status_color = logger.COLOR_GREEN if blog_status == "active" else logger.COLOR_DARK_GRAY
            status_styled = f"{status_color}{blog_status:<8}{logger.COLOR_RESET}"
            
            print(f"    Blog: {blog_name:<{max_blog_len}} | ID: {s['post_id']:<{max_id_len}} | Event: {aligned_ev} | Status: {status_styled}")
            
    except Exception as e:
        logger.error(f"Error reading Google Sheet: {e}")
        sys.exit(1)

    if not blogs:
        logger.warning("No blogs defined in Google Sheets. Exiting.")
        sys.exit(0)

    # 2b. Fetch translation and matches caches from Google Sheets
    logger.step_header("2b/7", "Fetching caches from Google Sheets")
    team_translations = {}
    matches_cache = {}
    try:
        team_translations = sheets_module.fetch_team_translations_separated(sheets_client, args.sheet)
        matches_cache = sheets_module.fetch_matches_cache(sheets_client, args.sheet)
        logger.success(f"Loaded {len(team_translations)} team translations and {len(matches_cache)} matches from cache.")
    except Exception as e:
        logger.error(f"Error loading caches: {e}")

    # 3. Scrape live matches
    logger.step_header("3/7", "Scraping competitor live matches")
    try:
        scraped_events, new_translations, updated_matches_cache = scraper_module.scrape_live_matches(
            use_mock=args.mock,
            team_translations=team_translations,
            matches_cache=matches_cache
        )
        # Sort matches: live first, then upcoming (not-started), then finished (ended)
        status_priority = {"live": 1, "not-started": 2, "finished": 3}
        scraped_events.sort(key=lambda ev: (status_priority.get(ev.get("status_class", "not-started"), 2), ev["time"]))
        
        logger.info(f"Scraped {len(scraped_events)} total matches:")
        print()
        
        max_t1_len = max(len(ev['team1']['nameEn'] or ev['team1']['nameAr']) for ev in scraped_events) if scraped_events else 15
        max_t2_len = max(len(ev['team2']['nameEn'] or ev['team2']['nameAr']) for ev in scraped_events) if scraped_events else 15
        
        for idx, ev in enumerate(scraped_events, 1):
            t1 = ev['team1']['nameEn'] or ev['team1']['nameAr']
            t2 = ev['team2']['nameEn'] or ev['team2']['nameAr']
            status = ev.get('status_class', 'unknown').upper()
            
            if ev['iframe_url']:
                iframe_link = ev['iframe_url']
                if len(iframe_link) > 30:
                    iframe_link = iframe_link[:30] + "..."
                iframe_part = f"iframe: {iframe_link}"
            else:
                iframe_part = "(No iframe)"
            
            aligned_teams = f"{t1:<{max_t1_len}} - {t2:<{max_t2_len}}"
            
            # Status colors
            if status == "LIVE":
                status_styled = f"{logger.COLOR_GREEN}{logger.COLOR_BOLD}{status:<11}{logger.COLOR_RESET}"
            elif status == "NOT-STARTED":
                status_styled = f"{logger.COLOR_YELLOW}{status:<11}{logger.COLOR_RESET}"
            elif status == "FINISHED":
                status_styled = f"{logger.COLOR_DARK_GRAY}{status:<11}{logger.COLOR_RESET}"
            else:
                status_styled = f"{status:<11}"
                
            # User friendly kickoff format
            kickoff_str = reconciler.format_to_user_style(ev['time'])
            print(f"    [{idx:2d}] {aligned_teams} | {kickoff_str} | {status_styled} | {iframe_part}")
            
    except Exception as e:
        logger.error(f"Error during scraping: {e}")
        sys.exit(1)

    # Save cache if not dry-run
    if not args.dry_run:
        if new_translations:
            print()
            logger.info(f"Saving {len(new_translations)} new translations back to Google Sheets...")
            try:
                sheets_module.save_new_team_translations_separated(sheets_client, new_translations, args.sheet)
            except Exception as e:
                logger.error(f"Error saving translations: {e}")
        if updated_matches_cache:
            print()
            logger.info(f"Saving {len(updated_matches_cache)} matches cache back to Google Sheets...")
            try:
                sheets_module.save_matches_cache(sheets_client, updated_matches_cache, args.sheet)
            except Exception as e:
                logger.error(f"Error saving matches cache: {e}")

    # Set up Telegram reporting if requested
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    send_report_chat_ids = [args.telegram_report_chat_id] if args.telegram_report_chat_id else []

    # 4. Reconcile states
    logger.step_header("4/7", "Reconciling blog states with scraped matches")
    # Filter events for blog reconciliation: must be live or not-started and have an iframe_url
    reconcile_events = [
        e for e in scraped_events
        if e.get("iframe_url") and e.get("status_class") in ["live", "not-started"]
    ]
    logger.info(f"Reconciling {len(reconcile_events)} active matches with streams (out of {len(scraped_events)} total matches).")
    actions = reconciler.reconcile_state(blogs, reconcile_events)
    
    # Check if there are any non-trivial actions
    change_actions = [a for a in actions if a["action_type"] != "no_action"]
    logger.info(f"Reconciliation finished. Total actions: {len(actions)}, updates needed: {len(change_actions)}.")
    print()
    
    for act in actions:
        action_type = act["action_type"]
        message = act["message"]
        
        # Replace ' vs ' with ' - ' in action messages
        message = message.replace(" vs ", " - ")
        
        if action_type == "no_action":
            prefix = f"{logger.COLOR_DARK_GRAY}[ NO ACTION  ]{logger.COLOR_RESET}"
            msg_styled = f"{logger.COLOR_DARK_GRAY}{message}{logger.COLOR_RESET}"
        elif action_type == "assign_new":
            prefix = f"{logger.COLOR_GREEN}{logger.COLOR_BOLD}[ ASSIGN NEW ]{logger.COLOR_RESET}"
            msg_styled = f"{logger.COLOR_GREEN}{message}{logger.COLOR_RESET}"
        elif action_type == "evict_and_assign":
            prefix = f"{logger.COLOR_RED}{logger.COLOR_BOLD}[ EVICT&ASSIGN]{logger.COLOR_RESET}"
            msg_styled = f"{logger.COLOR_RED}{message}{logger.COLOR_RESET}"
        elif action_type == "update_iframe":
            prefix = f"{logger.COLOR_BLUE}{logger.COLOR_BOLD}[ UPD IFRAME ]{logger.COLOR_RESET}"
            msg_styled = f"{logger.COLOR_BLUE}{message}{logger.COLOR_RESET}"
        elif action_type == "update_sheet_only":
            prefix = f"{logger.COLOR_YELLOW}{logger.COLOR_BOLD}[ UPD SHEET  ]{logger.COLOR_RESET}"
            msg_styled = f"{logger.COLOR_YELLOW}{message}{logger.COLOR_RESET}"
        elif action_type == "free_blog":
            prefix = f"{logger.COLOR_YELLOW}{logger.COLOR_BOLD}[ FREE BLOG  ]{logger.COLOR_RESET}"
            msg_styled = f"{logger.COLOR_YELLOW}{message}{logger.COLOR_RESET}"
        else:
            prefix = f"[{action_type.upper()}]"
            msg_styled = message
            
        print(f"    {prefix} {msg_styled}\n") # Adding empty line between actions!

    # 5. Handle Dry Run Announcement (we keep going to preview everything)
    if args.dry_run:
        logger.warning("dry-run flag is active. Running in PREVIEW mode (no API writes will be made).")
        print()

    # 6. User Confirmation
    if not args.auto and not args.dry_run:
        print(f"\n{logger.COLOR_BOLD}{logger.COLOR_YELLOW}Do you want to proceed with executing the changes above?{logger.COLOR_RESET}")
        confirm = input("Type 'yes' to proceed: ").strip().lower()
        if confirm != "yes":
            logger.warning("Execution aborted by user.")
            sys.exit(0)

    # 7. Execute updates (only if blog changes are needed)
    changed_blogs = []
    blogger_updates_made = 0

    if change_actions:
        logger.step_header("5/7", "Fetching current blog posts contents")
        try:
            # Fetch all posts in Blog Player to avoid making multiple requests
            posts_resp = blogger_module.fetch_all_posts(blogger_session, BLOG_PLAYER_ID)
            posts_list = posts_resp.get("items", [])
            posts_map = {p["id"]: p for p in posts_list}
            logger.success(f"Cached {len(posts_map)} post structures from the blog.")
        except Exception as e:
            logger.error(f"Error pre-fetching Blogger posts: {e}")
            sys.exit(1)

        logger.step_header("6/7", "Applying blog and Blogger post changes")
        # Map scraped event by event_id for fast lookup
        scraped_map = {e["event_id"]: e for e in scraped_events}

        for act in actions:
            action_type = act["action_type"]
            blog = act["blog"]
            event = act["event"]
            post_id = blog["post_id"]

            if action_type == "no_action":
                continue

            # Prepare blog modifications
            if action_type == "free_blog":
                blog["event_id"] = ""
                blog["event_name"] = ""
                blog["iframe_url"] = ""
                blog["kickoff_time"] = ""
                blog["status"] = "free"
                changed_blogs.append(blog)

            elif action_type in ["update_sheet_only"]:
                event_name = f"{event['team1'].get('nameEn') or event['team1']['nameAr']} vs {event['team2'].get('nameEn') or event['team2']['nameAr']}"
                blog["event_name"] = event_name
                blog["kickoff_time"] = reconciler.format_to_user_style(event["time"])
                blog["status"] = "active"
                changed_blogs.append(blog)

            elif action_type in ["update_iframe", "assign_new", "evict_and_assign"]:
                event_name = f"{event['team1'].get('nameEn') or event['team1']['nameAr']} vs {event['team2'].get('nameEn') or event['team2']['nameAr']}"
                blog["event_id"] = event["event_id"]
                blog["event_name"] = event_name
                blog["iframe_url"] = event["iframe_url"]
                blog["kickoff_time"] = reconciler.format_to_user_style(event["time"])
                blog["status"] = "active"
                changed_blogs.append(blog)

                # Update Blog Player blog post HTML content
                blog_name = blog['blog'] if blog.get('blog') else f"Row {blog['row_num']}"
                
                # Retrieve cached post data
                post_data = posts_map.get(post_id)
                if not post_data:
                    # Fallback to fetching it directly if not found in list
                    logger.warning(f"Post ID {post_id} not found in pre-fetched list. Fetching directly...")
                    try:
                        post_data = blogger_module.fetch_post(blogger_session, BLOG_PLAYER_ID, post_id)
                        posts_map[post_id] = post_data
                    except Exception as ex:
                        logger.error(f"Error fetching post {post_id}: {ex}")
                        continue

                current_content = post_data.get("content", "")
                
                try:
                    patched_content = patcher.patch_blog_html(current_content, event["iframe_url"])
                    if args.dry_run:
                        iframe_link = event['iframe_url']
                        if len(iframe_link) > 60:
                            iframe_link = iframe_link[:60] + "..."
                        logger.info(f"[Dry Run Preview] Would patch the blog Post ID: {post_id} (Blog: {blog_name}) iframe to: {iframe_link}")
                    else:
                        logger.info(f"Updating the blog Post ID: {post_id} (Blog: {blog_name})...")
                        blogger_module.update_post(blogger_session, BLOG_PLAYER_ID, post_id, patched_content)
                        iframe_link = event['iframe_url']
                        if len(iframe_link) > 60:
                            iframe_link = iframe_link[:60] + "..."
                        logger.success(f"Successfully patched post iframe URL to: {iframe_link}")
                    # Update our cache with the patched content
                    post_data["content"] = patched_content
                    blogger_updates_made += 1
                except Exception as ex:
                    logger.error(f"Error patching/updating post {post_id}: {ex}")
                    continue

        # Write changes to Sheets
        if changed_blogs:
            print()
            if args.dry_run:
                logger.info(f"[Dry Run Preview] Would write {len(changed_blogs)} updated blog states back to Google Sheets.")
            else:
                logger.info("Writing updated blog states back to Google Sheets...")
                try:
                    sheets_module.update_changed_blogs(sheets_client, changed_blogs, args.sheet)
                    logger.success("Sheets updated successfully.")
                except Exception as e:
                    logger.error(f"Error writing to Google Sheet: {e}")
    else:
        print()
        logger.success("Blog allocations and the blog streams are already up to date. Skipping blog updates.")

    # 8. Rebuild matches array for BLOG_DATA_ID
    logger.step_header("7/7", "Rebuilding Event List array for the data website")
    
    # We must fetch the latest blog rows from Sheets to construct the list (ensuring consistency)
    if args.dry_run:
        updated_blogs = blogs
    else:
        try:
            updated_blogs = sheets_module.fetch_all_blogs(sheets_client, args.sheet)
        except Exception as e:
            logger.error(f"Error fetching updated blog states: {e}")
            sys.exit(1)

    # Re-fetch Blog Player posts to ensure we have all URLs/permalinks
    try:
        posts_resp = blogger_module.fetch_all_posts(blogger_session, BLOG_PLAYER_ID)
        posts_list = posts_resp.get("items", [])
        posts_map = {p["id"]: p for p in posts_list}
    except Exception as e:
        logger.error(f"Error updating the blog posts cache: {e}")
        sys.exit(1)

    active_matches_list = []
    
    # Map active blogs by event_id for fast lookup
    blog_by_event_id = {}
    for s in updated_blogs:
        if s.get("status", "").strip().lower() == "active" and s.get("event_id"):
            blog_by_event_id[s["event_id"]] = s

    # Build matches list using all scraped events (today + tomorrow, including ended/finished ones)
    for ev in scraped_events:
        ev_id = ev["event_id"]
        
        # Determine blog permalink link if assigned to an active blog
        permalink_url = ""
        if ev_id in blog_by_event_id:
            s = blog_by_event_id[ev_id]
            post_id = s["post_id"]
            post_info = posts_map.get(post_id)
            if not post_info:
                # Try fetching directly
                try:
                    post_info = blogger_module.fetch_post(blogger_session, BLOG_PLAYER_ID, post_id)
                    posts_map[post_id] = post_info
                except Exception:
                    pass
            if post_info:
                permalink_url = post_info.get("url", "")

        match_obj = {
            "id": 0, # placeholder renumbered below
            "team1": ev["team1"],
            "team2": ev["team2"],
            "time": ev["time"],
            "duration": ev.get("duration", 150),
            "link": permalink_url,
            "ended": ev.get("status_class") in ["finished", "manually-finished"],
            "status_class": ev["status_class"] # temporary key
        }
        active_matches_list.append(match_obj)

    # Sort matches: live first, then upcoming (not-started), then finished (ended)
    status_priority = {"live": 1, "not-started": 2, "finished": 3}
    active_matches_list.sort(key=lambda m: (status_priority.get(m["status_class"], 2), m["time"]))
    
    for idx, match in enumerate(active_matches_list, start=1):
        match["id"] = idx
        match.pop("status_class", None)

    logger.info(f"Active matches formatted for the data website ({len(active_matches_list)} matches):")
    print()
    
    max_t1_len = max(len(m['team1'].get('nameEn') or m['team1']['nameAr']) for m in active_matches_list) if active_matches_list else 15
    max_t2_len = max(len(m['team2'].get('nameEn') or m['team2']['nameAr']) for m in active_matches_list) if active_matches_list else 15
    
    for m in active_matches_list:
        t1_en = m['team1'].get('nameEn') or m['team1']['nameAr']
        t2_en = m['team2'].get('nameEn') or m['team2']['nameAr']
        aligned_teams = f"{t1_en:<{max_t1_len}} - {t2_en:<{max_t2_len}}"
        link_str = m['link']
        if len(link_str) > 60:
            link_str = link_str[:60] + "..."
        print(f"    [{m['id']:2d}] {aligned_teams} -> Link: {link_str}")
        
    print()

    # Patch Blog DATA Matches Page
    logger.info(f"Fetching the data website Matches Page ID {DATA_PAGE_ID} from Blog {BLOG_DATA_ID}...")
    try:
        page_data = blogger_module.fetch_page(blogger_session, BLOG_DATA_ID, DATA_PAGE_ID)
        page_content = page_data.get("content", "")
        
        patched_page_content = patcher.patch_matches_page(page_content, active_matches_list)
        
        if patched_page_content == page_content:
            logger.success("Matches list page content is already up to date. Skipping Blogger update.")
        else:
            if args.dry_run:
                logger.info("[Dry Run Preview] Would update the data website Matches Page content on Blogger (content changed).")
            else:
                logger.info("Updating the data website Matches Page content (content changed)...")
                blogger_module.update_page(blogger_session, BLOG_DATA_ID, DATA_PAGE_ID, patched_page_content)
                logger.success("Matches list page successfully updated.")
    except Exception as e:
        logger.error(f"Error updating the data website page: {e}")
        # Send error report if requested via Telegram
        for cid in send_report_chat_ids:
            send_telegram_message(TELEGRAM_BOT_TOKEN, cid, f"Pipeline Run Error: {e}")
        sys.exit(1)

    if send_report_chat_ids:
        reconciliation_report = []
        for act in actions:
            action_type = act["action_type"]
            message = act["message"]
            if action_type != "no_action":
                clean_msg = message.replace(logger.COLOR_GREEN, "").replace(logger.COLOR_RED, "").replace(logger.COLOR_BLUE, "").replace(logger.COLOR_YELLOW, "").replace(logger.COLOR_RESET, "").replace(logger.COLOR_BOLD, "")
                reconciliation_report.append(f"• {clean_msg}")
                
        if reconciliation_report:
            report_text = "Pipeline Updates Applied:\n" + "\n".join(reconciliation_report)
        else:
            report_text = "Pipeline check completed. No blog changes needed (everything up to date)."
            
        for cid in send_report_chat_ids:
            send_telegram_message(TELEGRAM_BOT_TOKEN, cid, report_text)

    logger.step_header("DONE", "Pipeline run completed successfully")

if __name__ == "__main__":
    main()
