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

def reconcile_state(sheet_slots: list, scraped_events: list) -> list:
    """
    Compares the Google Sheet slot states with the scraped live events.
    Returns a list of action dicts describing what updates to make to the sheet and Blogger.
    """
    actions = []
    
    # Map scraped events by event_id for fast lookup
    scraped_map = {e["event_id"]: e for e in scraped_events}
    
    active_matched = []  # slots currently holding a scraped event
    to_be_freed = []     # slots holding an event that is no longer scraped
    already_free = []    # slots that were already free
    
    for slot in sheet_slots:
        ev_id = slot.get("event_id", "").strip()
        status = slot.get("status", "").strip().lower()
        
        if status == "active" and ev_id:
            if ev_id in scraped_map:
                active_matched.append(slot)
            else:
                to_be_freed.append(slot)
        else:
            already_free.append(slot)
            
    # Pools for allocation
    # slots we can reuse/assign immediately: first already free, then slots whose matches disappeared
    free_slots_queue = already_free + to_be_freed
    
    # Track assignments made in this run
    # Slot name -> Assigned Event dict
    assignments = {}
    for slot in active_matched:
        event = scraped_map[slot["event_id"]]
        assignments[slot["slot"]] = {
            "slot": slot,
            "event": event,
            "source": "matched"
        }
        
    # Process scraped events that are not yet active
    unassigned_events = [e for e in scraped_events if e["event_id"] not in [s["event_id"] for s in active_matched]]
    
    # Slots that were marked to_be_freed but got claimed/reassigned during this run
    reassigned_slots = set()
    
    for event in unassigned_events:
        t1_en = event['team1'].get('nameEn') or event['team1']['nameAr']
        t2_en = event['team2'].get('nameEn') or event['team2']['nameAr']
        event_name = f"{t1_en} vs {t2_en}"
        
        if free_slots_queue:
            # Assign to the first available free/freeable slot
            slot = free_slots_queue.pop(0)
            slot_label = slot['slot'] if slot.get('slot') else f"Row {slot['row_num']}"
            
            if slot in to_be_freed:
                reassigned_slots.add(slot["slot"])
                
            assignments[slot["slot"]] = {
                "slot": slot,
                "event": event,
                "source": "assign"
            }
            
            actions.append({
                "action_type": "assign_new",
                "slot": slot,
                "event": event,
                "message": f"Assign new event '{event_name}' to slot {slot_label}"
            })
        else:
            # All slots are fully active (no free slots, and no slots to free). We must evict one.
            active_candidates = list(assignments.values())
            
            if not active_candidates:
                print(f"Error: No slots available for eviction to fit event '{event_name}'.", file=sys.stderr)
                continue
                
            # Find candidate with earliest kickoff time
            active_candidates.sort(key=lambda item: parse_iso_time(item["event"]["time"]))
            evict_item = active_candidates[0]
            evict_slot = evict_item["slot"]
            evict_event = evict_item["event"]
            evict_t1_en = evict_event['team1'].get('nameEn') or evict_event['team1']['nameAr']
            evict_t2_en = evict_event['team2'].get('nameEn') or evict_event['team2']['nameAr']
            evict_event_name = f"{evict_t1_en} vs {evict_t2_en}"
            
            evict_slot_label = evict_slot['slot'] if evict_slot.get('slot') else f"Row {evict_slot['row_num']}"
            
            # Reassign this slot to the new event
            assignments[evict_slot["slot"]] = {
                "slot": evict_slot,
                "event": event,
                "source": "evict"
            }
            
            actions.append({
                "action_type": "evict_and_assign",
                "slot": evict_slot,
                "event": event,
                "message": f"WARNING: All slots full. Evicting slot {evict_slot_label} (was: '{evict_event_name}', kickoff: {evict_event['time']}) for new event '{event_name}'"
            })
            
    # For any slots in to_be_freed that did NOT get reassigned to a new event:
    for slot in to_be_freed:
        if slot["slot"] not in reassigned_slots:
            slot_label = slot['slot'] if slot.get('slot') else f"Row {slot['row_num']}"
            actions.append({
                "action_type": "free_slot",
                "slot": slot,
                "event": None,
                "message": f"Freeing slot {slot_label} (event '{slot.get('event_name')}' disappeared from scrape)"
            })
            
    # For the matched active slots, check if they need an update or are up-to-date
    for slot in active_matched:
        if assignments[slot["slot"]]["source"] == "evict":
            continue
            
        event = scraped_map[slot["event_id"]]
        t1_en = event['team1'].get('nameEn') or event['team1']['nameAr']
        t2_en = event['team2'].get('nameEn') or event['team2']['nameAr']
        event_name = f"{t1_en} vs {t2_en}"
        
        slot_label = slot['slot'] if slot.get('slot') else f"Row {slot['row_num']}"
        sheet_iframe = slot.get("iframe_url", "").strip()
        scraped_iframe = event["iframe_url"].strip()
        
        if sheet_iframe != scraped_iframe:
            actions.append({
                "action_type": "update_iframe",
                "slot": slot,
                "event": event,
                "message": f"Update iframe for slot {slot_label} (event: '{event_name}')"
            })
        else:
            sheet_name = slot.get("event_name", "").strip()
            sheet_kickoff = slot.get("kickoff_time", "").strip()
            
            if sheet_name != event_name or sheet_kickoff != event["time"]:
                actions.append({
                    "action_type": "update_sheet_only",
                    "slot": slot,
                    "event": event,
                    "message": f"Update sheet metadata only for slot {slot_label} (event: '{event_name}')"
                })
            else:
                actions.append({
                    "action_type": "no_action",
                    "slot": slot,
                    "event": event,
                    "message": f"Slot {slot_label} is up to date for event '{event_name}'"
                })
                
    return actions

if __name__ == "__main__":
    # Test block
    print("Testing refined reconciler state logic...")
    sheet_data = [
        {"slot": "Slot 1", "post_id": "101", "event_id": "teamA-vs-teamB", "event_name": "Team A vs Team B", "iframe_url": "url1", "status": "active", "kickoff_time": "2026-06-28T10:00:00+01:00", "row_num": 2},
        {"slot": "Slot 2", "post_id": "102", "event_id": "", "event_name": "", "iframe_url": "", "status": "free", "kickoff_time": "", "row_num": 3},
        {"slot": "Slot 3", "post_id": "103", "event_id": "teamC-vs-teamD", "event_name": "Team C vs Team D", "iframe_url": "url3", "status": "active", "kickoff_time": "2026-06-28T14:00:00+01:00", "row_num": 4},
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
