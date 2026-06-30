import sys
import re
from datetime import datetime

# Reconfigure stdout/stderr to use UTF-8
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

def parse_iso_time(time_str: str) -> datetime:
    """
    Parses ISO time string, falling back to epoch if parsing fails or string is empty.
    Strips timezone offset to avoid mixing aware/naive datetimes.
    """
    if not time_str:
        return datetime.min
    try:
        # Strip timezone offset like +01:00 or -05:00 or Z
        clean_str = re.sub(r'([+-]\d{2}:?\d{2}|Z)$', '', time_str.strip())
        return datetime.fromisoformat(clean_str)
    except Exception:
        try:
            return datetime.strptime(time_str.split('+')[0].split('.')[0], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return datetime.min

def format_to_user_style(iso_time_str: str) -> str:
    """
    Converts ISO 8601 time string (GMT+1) to user format: "29 July - 21:00 (UTC+1)"
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

def reconcile_state(sheet_blogs: list, scraped_events: list) -> list:
    """
    Compares the Google Sheet blog states with the scraped live events.
    Returns a list of action dicts describing what updates to make to the sheet and Blogger.
    """
    actions = []
    
    # Map scraped events by event_id for fast lookup
    scraped_map = {e["event_id"]: e for e in scraped_events}
    
    active_matched = []  # blogs currently holding a scraped event
    to_be_freed = []     # blogs holding an event that is no longer scraped
    already_free = []    # blogs that were already free
    
    for blog in sheet_blogs:
        ev_id = blog.get("event_id", "").strip()
        status = blog.get("status", "").strip().lower()
        
        if status == "active" and ev_id:
            if ev_id in scraped_map:
                active_matched.append(blog)
            else:
                to_be_freed.append(blog)
        else:
            already_free.append(blog)
            
    # Pools for allocation
    # blogs we can reuse/assign immediately: first already free, then blogs whose matches disappeared
    free_blogs_queue = already_free + to_be_freed
    
    # Track assignments made in this run
    # Blog name -> Assigned Event dict
    assignments = {}
    for blog in active_matched:
        event = scraped_map[blog["event_id"]]
        assignments[blog["blog"]] = {
            "blog": blog,
            "event": event,
            "source": "matched"
        }
        
    # Process scraped events that are not yet active
    unassigned_events = [e for e in scraped_events if e["event_id"] not in [b["event_id"] for b in active_matched]]
    
    # Blogs that were marked to_be_freed but got claimed/reassigned during this run
    reassigned_blogs = set()
    
    for event in unassigned_events:
        t1_en = event['team1'].get('nameEn') or event['team1']['nameAr']
        t2_en = event['team2'].get('nameEn') or event['team2']['nameAr']
        event_name = f"{t1_en} vs {t2_en}"
        
        if free_blogs_queue:
            # Assign to the first available free/freeable blog
            blog = free_blogs_queue.pop(0)
            blog_label = blog['blog'] if blog.get('blog') else f"Row {blog['row_num']}"
            
            if blog in to_be_freed:
                reassigned_blogs.add(blog["blog"])
                
            assignments[blog["blog"]] = {
                "blog": blog,
                "event": event,
                "source": "assign"
            }
            
            actions.append({
                "action_type": "assign_new",
                "blog": blog,
                "event": event,
                "message": f"Assign new event '{event_name}' to blog {blog_label}"
            })
        else:
            # All blogs are fully active (no free blogs, and no blogs to free). We must evict one.
            active_candidates = list(assignments.values())
            
            if not active_candidates:
                print(f"Error: No blogs available for eviction to fit event '{event_name}'.", file=sys.stderr)
                continue
                
            # Find candidate with earliest kickoff time
            active_candidates.sort(key=lambda item: parse_iso_time(item["event"]["time"]))
            evict_item = active_candidates[0]
            evict_blog = evict_item["blog"]
            evict_event = evict_item["event"]
            evict_t1_en = evict_event['team1'].get('nameEn') or evict_event['team1']['nameAr']
            evict_t2_en = evict_event['team2'].get('nameEn') or evict_event['team2']['nameAr']
            evict_event_name = f"{evict_t1_en} vs {evict_t2_en}"
            
            evict_blog_label = evict_blog['blog'] if evict_blog.get('blog') else f"Row {evict_blog['row_num']}"
            
            # Reassign this blog to the new event
            assignments[evict_blog["blog"]] = {
                "blog": evict_blog,
                "event": event,
                "source": "evict"
            }
            
            actions.append({
                "action_type": "evict_and_assign",
                "blog": evict_blog,
                "event": event,
                "message": f"WARNING: All blogs full. Evicting blog {evict_blog_label} (was: '{evict_event_name}', kickoff: {evict_event['time']}) for new event '{event_name}'"
            })
            
    # For any blogs in to_be_freed that did NOT get reassigned to a new event:
    for blog in to_be_freed:
        if blog["blog"] not in reassigned_blogs:
            blog_label = blog['blog'] if blog.get('blog') else f"Row {blog['row_num']}"
            actions.append({
                "action_type": "free_blog",
                "blog": blog,
                "event": None,
                "message": f"Freeing blog {blog_label} (event '{blog.get('event_name')}' disappeared from scrape)"
            })
            
    # For the matched active blogs, check if they need an update or are up-to-date
    for blog in active_matched:
        if assignments[blog["blog"]]["source"] == "evict":
            continue
            
        event = scraped_map[blog["event_id"]]
        t1_en = event['team1'].get('nameEn') or event['team1']['nameAr']
        t2_en = event['team2'].get('nameEn') or event['team2']['nameAr']
        event_name = f"{t1_en} vs {t2_en}"
        
        blog_label = blog['blog'] if blog.get('blog') else f"Row {blog['row_num']}"
        sheet_iframe = blog.get("iframe_url", "").strip()
        scraped_iframe = event["iframe_url"].strip()
        
        if sheet_iframe != scraped_iframe:
            actions.append({
                "action_type": "update_iframe",
                "blog": blog,
                "event": event,
                "message": f"Update iframe for blog {blog_label} (event: '{event_name}')"
            })
        else:
            sheet_name = blog.get("event_name", "").strip()
            sheet_kickoff = blog.get("kickoff_time", "").strip()
            
            expected_kickoff = format_to_user_style(event["time"])
            if sheet_name != event_name or sheet_kickoff != expected_kickoff:
                actions.append({
                    "action_type": "update_sheet_only",
                    "blog": blog,
                    "event": event,
                    "message": f"Update sheet metadata only for blog {blog_label} (event: '{event_name}')"
                })
            else:
                actions.append({
                    "action_type": "no_action",
                    "blog": blog,
                    "event": event,
                    "message": f"Blog {blog_label} is up to date for event '{event_name}'"
                })
                
    return actions

if __name__ == "__main__":
    # Test block
    print("Testing refined reconciler state logic...")
    sheet_data = [
        {"blog": "Blog 1", "post_id": "101", "event_id": "teamA-vs-teamB", "event_name": "Team A vs Team B", "iframe_url": "url1", "status": "active", "kickoff_time": "2026-06-28T10:00:00+01:00", "row_num": 2},
        {"blog": "Blog 2", "post_id": "102", "event_id": "", "event_name": "", "iframe_url": "", "status": "free", "kickoff_time": "", "row_num": 3},
        {"blog": "Blog 3", "post_id": "103", "event_id": "teamC-vs-teamD", "event_name": "Team C vs Team D", "iframe_url": "url3", "status": "active", "kickoff_time": "2026-06-28T14:00:00+01:00", "row_num": 4},
    ]
    
    scraped = [
        {
            "event_id": "teamA-vs-teamB",
            "team1": {"nameAr": "Team A"}, "team2": {"nameAr": "Team B"},
            "time": "2026-06-28T10:00:00+01:00",
            "iframe_url": "url1"
        },
        {
            "event_id": "teamE-vs-teamF",
            "team1": {"nameAr": "Team E"}, "team2": {"nameAr": "Team F"},
            "time": "2026-06-28T15:00:00+01:00",
            "iframe_url": "url5"
        },
        {
            "event_id": "teamG-vs-teamH",
            "team1": {"nameAr": "Team G"}, "team2": {"nameAr": "Team H"},
            "time": "2026-06-28T16:00:00+01:00",
            "iframe_url": "url7"
        }
    ]
    
    actions = reconcile_state(sheet_data, scraped)
    for act in actions:
        print(f"- {act['action_type']}: {act['message']}")
