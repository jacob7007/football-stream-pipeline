import os
import sys
import json
import argparse
from datetime import datetime

# Import project modules
import sheets_module
import blogger_module
import scraper_module
import reconciler
import patcher

# Configure stdout/stderr for UTF-8
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# Blogger Config
BLOG_A_ID = "2885706943982996652"       # Tivivi Edu (Iframe Slots Blog)
BLOG_B_ID = "6468231860851233648"       # Tivivi Goal (Event List Blog)
BLOG_B_PAGE_ID = "7306539215204955677"  # Match listing Page ID

SPREADSHEET_NAME = "Matches - Slots state"

def main():
    parser = argparse.ArgumentParser(description="Football Stream Automation Pipeline")
    parser.add_argument("--mock", action="store_true", help="Run scraper in mock mode")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without making write calls to Sheets or Blogger")
    parser.add_argument("--auto", action="store_true", help="Skip user confirmation prompts (useful for GitHub Actions)")
    parser.add_argument("--sheet", type=str, default=SPREADSHEET_NAME, help="Google Sheets spreadsheet name or ID")
    args = parser.parse_args()

    print("==================================================")
    print(f"Starting Stream Pipeline Run - {datetime.now().isoformat()}")
    print(f"Flags: mock={args.mock}, dry_run={args.dry_run}, auto={args.auto}, sheet={args.sheet}")
    print("==================================================")

    # 1. Initialize Clients
    print("\n[Step 1] Initializing API clients...")
    try:
        sheets_client = sheets_module.get_gspread_client()
        print("  - Google Sheets client authorized.")
    except Exception as e:
        print(f"  - Error initializing Google Sheets client: {e}")
        sys.exit(1)

    try:
        blogger_session = blogger_module.get_blogger_session()
        print("  - Blogger API session authorized.")
    except Exception as e:
        print(f"  - Error initializing Blogger API session: {e}")
        sys.exit(1)

    # 2. Fetch sheet slots
    print(f"\n[Step 2] Fetching slots from Google Sheet '{args.sheet}'...")
    try:
        slots = sheets_module.fetch_all_slots(sheets_client, args.sheet)
        print(f"  - Found {len(slots)} slot rows in Google Sheets:")
        for s in slots:
            slot_name = s['slot'] if s.get('slot') else f"Row {s['row_num']}"
            slot_status = s['status'] if s.get('status') else "free"
            print(f"    Slot: {slot_name} | ID: {s['post_id']} | Event: {s.get('event_name') or '(free)'} | Status: {slot_status}")
    except Exception as e:
        print(f"  - Error reading Google Sheet: {e}")
        sys.exit(1)

    if not slots:
        print("  - No slots defined in Google Sheets. Exiting.")
        sys.exit(0)

    # 3. Scrape live matches
    print("\n[Step 3] Scraping competitor live matches...")
    try:
        scraped_events = scraper_module.scrape_live_matches(use_mock=args.mock)
        # Sort matches: live first, then upcoming (not-started), then finished (ended)
        status_priority = {"live": 1, "not-started": 2, "finished": 3}
        scraped_events.sort(key=lambda ev: (status_priority.get(ev.get("status_class", "not-started"), 2), ev["time"]))
        print(f"  - Scraped {len(scraped_events)} total matches (live, upcoming, and ended).")
        for idx, ev in enumerate(scraped_events, 1):
            t1 = ev['team1']['nameEn'] or ev['team1']['nameAr']
            t2 = ev['team2']['nameEn'] or ev['team2']['nameAr']
            status = ev.get('status_class', 'unknown').upper()
            iframe_part = f" | iframe: {ev['iframe_url'][:60]}..." if ev['iframe_url'] else " | (No iframe)"
            print(f"    [{idx}] {t1} vs {t2} | kickoff: {ev['time']} | status: {status}{iframe_part}")
    except Exception as e:
        print(f"  - Error during scraping: {e}")
        sys.exit(1)

    # 4. Reconcile states
    print("\n[Step 4] Reconciling slot states with scraped matches...")
    # Filter events for slot reconciliation: must be live or not-started and have an iframe_url
    reconcile_events = [
        e for e in scraped_events
        if e.get("iframe_url") and e.get("status_class") in ["live", "not-started"]
    ]
    print(f"  - Reconciling {len(reconcile_events)} active matches with streams (out of {len(scraped_events)} total matches).")
    actions = reconciler.reconcile_state(slots, reconcile_events)
    
    # Check if there are any non-trivial actions
    change_actions = [a for a in actions if a["action_type"] != "no_action"]
    print(f"  - Reconciliation finished. Total actions: {len(actions)}, updates needed: {len(change_actions)}.")
    for act in actions:
        # Prefix warnings or key updates
        prefix = "    "
        if act["action_type"] == "evict_and_assign":
            prefix = "  ! "
        elif act["action_type"] != "no_action":
            prefix = "  * "
        print(f"{prefix}[{act['action_type'].upper()}] {act['message']}")

    # 5. Handle Dry Run Announcement (we keep going to preview everything)
    if args.dry_run:
        print("\n[Dry Run] --dry-run flag is active. Running in PREVIEW mode (no API writes will be made).")

    # 6. User Confirmation
    if not args.auto and not args.dry_run:
        print("\nDo you want to proceed with executing the changes above?")
        confirm = input("Type 'yes' to proceed: ").strip().lower()
        if confirm != "yes":
            print("Execution aborted by user.")
            sys.exit(0)

    # 7. Execute updates (only if slot changes are needed)
    changed_slots = []
    blogger_updates_made = 0

    if change_actions:
        print("\n[Step 5] Fetching current Blog A slot posts contents...")
        try:
            # Fetch all posts in Blog A to avoid making multiple requests
            posts_resp = blogger_module.fetch_all_posts(blogger_session, BLOG_A_ID)
            posts_list = posts_resp.get("items", [])
            posts_map = {p["id"]: p for p in posts_list}
            print(f"  - Cached {len(posts_map)} post structures from Blog A.")
        except Exception as e:
            print(f"  - Error pre-fetching Blogger posts: {e}")
            sys.exit(1)

        print("\n[Step 6] Applying slot and Blogger post changes...")
        # Map scraped event by event_id for fast lookup
        scraped_map = {e["event_id"]: e for e in scraped_events}

        for act in actions:
            action_type = act["action_type"]
            slot = act["slot"]
            event = act["event"]
            post_id = slot["post_id"]

            if action_type == "no_action":
                continue

            # Prepare slot modifications
            if action_type == "free_slot":
                slot["event_id"] = ""
                slot["event_name"] = ""
                slot["iframe_url"] = ""
                slot["kickoff_time"] = ""
                slot["status"] = "free"
                changed_slots.append(slot)

            elif action_type in ["update_sheet_only"]:
                event_name = f"{event['team1'].get('nameEn') or event['team1']['nameAr']} vs {event['team2'].get('nameEn') or event['team2']['nameAr']}"
                slot["event_name"] = event_name
                slot["kickoff_time"] = event["time"]
                slot["status"] = "active"
                changed_slots.append(slot)

            elif action_type in ["update_iframe", "assign_new", "evict_and_assign"]:
                event_name = f"{event['team1'].get('nameEn') or event['team1']['nameAr']} vs {event['team2'].get('nameEn') or event['team2']['nameAr']}"
                slot["event_id"] = event["event_id"]
                slot["event_name"] = event_name
                slot["iframe_url"] = event["iframe_url"]
                slot["kickoff_time"] = event["time"]
                slot["status"] = "active"
                changed_slots.append(slot)

                # Update Blog A slot post HTML content
                slot_name = slot['slot'] if slot.get('slot') else f"Row {slot['row_num']}"
                
                # Retrieve cached post data
                post_data = posts_map.get(post_id)
                if not post_data:
                    # Fallback to fetching it directly if not found in list
                    print(f"    - Warning: Post ID {post_id} not found in pre-fetched list. Fetching directly...")
                    try:
                        post_data = blogger_module.fetch_post(blogger_session, BLOG_A_ID, post_id)
                        posts_map[post_id] = post_data
                    except Exception as ex:
                        print(f"    - Error fetching post {post_id}: {ex}")
                        continue

                current_content = post_data.get("content", "")
                
                try:
                    patched_content = patcher.patch_slot_html(current_content, event["iframe_url"])
                    if args.dry_run:
                        print(f"  [Dry Run] Would patch Blog A Post ID: {post_id} (Slot: {slot_name}) iframe to: {event['iframe_url']}")
                    else:
                        print(f"  Updating Blog A Post ID: {post_id} (Slot: {slot_name})...")
                        blogger_module.update_post(blogger_session, BLOG_A_ID, post_id, patched_content)
                        print(f"    - Successfully patched post iframe URL to: {event['iframe_url']}")
                    # Update our cache with the patched content
                    post_data["content"] = patched_content
                    blogger_updates_made += 1
                except Exception as ex:
                    print(f"    - Error patching/updating post {post_id}: {ex}")
                    continue

        # Write changes to Sheets
        if changed_slots:
            if args.dry_run:
                print(f"\n  [Dry Run] Would write {len(changed_slots)} updated slot states back to Google Sheets.")
            else:
                print("\n  Writing updated slot states back to Google Sheets...")
                try:
                    sheets_module.update_changed_slots(sheets_client, changed_slots, args.sheet)
                    print("    - Sheets updated successfully.")
                except Exception as e:
                    print(f"    - Error writing to Google Sheet: {e}")
    else:
        print("\nSlot allocations and Blog A streams are already up to date. Skipping slot updates.")

    # 8. Rebuild matches array for Blog B
    print("\n[Step 7] Rebuilding Event List array for Blog B (Tivivi Goal)...")
    
    # We must fetch the latest slot rows from Sheets to construct the list (ensuring consistency)
    if args.dry_run:
        updated_slots = slots
    else:
        try:
            updated_slots = sheets_module.fetch_all_slots(sheets_client, args.sheet)
        except Exception as e:
            print(f"  - Error fetching updated slot states: {e}")
            sys.exit(1)

    # Re-fetch Blog A posts to ensure we have all URLs/permalinks
    try:
        posts_resp = blogger_module.fetch_all_posts(blogger_session, BLOG_A_ID)
        posts_list = posts_resp.get("items", [])
        posts_map = {p["id"]: p for p in posts_list}
    except Exception as e:
        print(f"  - Error updating Blog A posts cache: {e}")
        sys.exit(1)

    active_matches_list = []
    
    # Map active slots by event_id for fast lookup
    slot_by_event_id = {}
    for s in updated_slots:
        if s.get("status", "").strip().lower() == "active" and s.get("event_id"):
            slot_by_event_id[s["event_id"]] = s

    # Build matches list using all scraped events (today + tomorrow, including ended/finished ones)
    for ev in scraped_events:
        ev_id = ev["event_id"]
        
        # Determine slot permalink link if assigned to an active slot
        permalink_url = ""
        if ev_id in slot_by_event_id:
            s = slot_by_event_id[ev_id]
            post_id = s["post_id"]
            post_info = posts_map.get(post_id)
            if not post_info:
                # Try fetching directly
                try:
                    post_info = blogger_module.fetch_post(blogger_session, BLOG_A_ID, post_id)
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
            "status_class": ev["status_class"] # temporary key
        }
        active_matches_list.append(match_obj)

    # Sort matches: live first, then upcoming (not-started), then finished (ended)
    status_priority = {"live": 1, "not-started": 2, "finished": 3}
    active_matches_list.sort(key=lambda m: (status_priority.get(m["status_class"], 2), m["time"]))
    
    for idx, match in enumerate(active_matches_list, start=1):
        match["id"] = idx
        match.pop("status_class", None)

    print(f"  - Active matches formatted for Blog B ({len(active_matches_list)} matches):")
    for m in active_matches_list:
        t1_en = m['team1'].get('nameEn') or m['team1']['nameAr']
        t2_en = m['team2'].get('nameEn') or m['team2']['nameAr']
        print(f"    [{m['id']}] {t1_en} vs {t2_en} -> Link: {m['link']}")

    # Patch Blog B Matches Page
    print(f"\n  Fetching Blog B Matches Page ID {BLOG_B_PAGE_ID} from Blog {BLOG_B_ID}...")
    try:
        page_data = blogger_module.fetch_page(blogger_session, BLOG_B_ID, BLOG_B_PAGE_ID)
        page_content = page_data.get("content", "")
        
        patched_page_content = patcher.patch_matches_page(page_content, active_matches_list)
        
        if args.dry_run:
            print("  [Dry Run] Would update Blog B Matches Page content on Blogger.")
        else:
            print("  Updating Blog B Matches Page content...")
            blogger_module.update_page(blogger_session, BLOG_B_ID, BLOG_B_PAGE_ID, patched_page_content)
            print("  - Matches list page successfully updated.")
    except Exception as e:
        print(f"  - Error updating Blog B page: {e}")
        sys.exit(1)

    print("\n==================================================")
    print("Pipeline run completed successfully.")
    print("==================================================")

if __name__ == "__main__":
    main()
