import os
import json
import re
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import logger

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Column layout in Google Sheet (exactly matches Plan.md order)
COLUMNS = [
    "slot",
    "post_id",
    "event_id",
    "event_name",
    "iframe_url",
    "status",
    "kickoff_time",
    "last_updated"
]

def get_gspread_client() -> gspread.Client:
    """
    Authenticates and returns a gspread client.
    Looks for service account credentials in:
    1. GOOGLE_SERVICE_ACCOUNT_JSON (env variable containing the JSON string)
    2. GOOGLE_SERVICE_ACCOUNT_FILE (env variable containing path to JSON file)
    3. Fallback: local 'service_account.json' file
    """
    # 1. Check JSON string from environment variable
    sa_json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json_str:
        try:
            info = json.loads(sa_json_str)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            raise RuntimeError(f"Failed to load credentials from GOOGLE_SERVICE_ACCOUNT_JSON env var: {e}")

    # 2. Check JSON file path from environment variable
    sa_file_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_file_path and os.path.exists(sa_file_path):
        creds = Credentials.from_service_account_file(sa_file_path, scopes=SCOPES)
        return gspread.authorize(creds)

    # 3. Check fallback local files (path-independent)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    fallback_paths = [
        "service_account.json", "google_sheets_token.json",
        os.path.join(script_dir, "service_account.json"),
        os.path.join(script_dir, "google_sheets_token.json"),
        os.path.join(parent_dir, "service_account.json"),
        os.path.join(parent_dir, "google_sheets_token.json")
    ]
    for path in fallback_paths:
        if os.path.exists(path):
            creds = Credentials.from_service_account_file(path, scopes=SCOPES)
            return gspread.authorize(creds)

    raise FileNotFoundError(
        "Google Service Account credentials not found. Please set "
        "GOOGLE_SERVICE_ACCOUNT_JSON environment variable or place a "
        "'service_account.json' or 'google_sheets_token.json' file in the workspace directory."
    )

def fetch_all_slots(client: gspread.Client, spreadsheet_name: str = "Matches - Slots state") -> list:
    """
    Fetches all slot rows from the first worksheet of the specified spreadsheet.
    Returns a list of dicts. Each dict contains the sheet values plus a 'row_num' key (1-indexed).
    """
    try:
        sh = client.open(spreadsheet_name)
    except gspread.exceptions.SpreadsheetNotFound:
        # Fallback to check if it's an ID
        try:
            sh = client.open_by_key(spreadsheet_name)
        except Exception:
            raise ValueError(f"Spreadsheet '{spreadsheet_name}' not found by name or ID.")
            
    try:
        worksheet = sh.worksheet("_cache_blogs")
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.sheet1
    
    # Read headers to verify structure
    headers = [h.strip().lower() for h in worksheet.row_values(1)]
    
    # Verify all required columns exist (case-insensitive check)
    for col in COLUMNS:
        if col not in headers:
            # We will continue but warn or handle dynamically if mapping differs
            logger.warning(f"Sheets: Column '{col}' not found in Google Sheet headers: {headers}")
            
    # Fetch all values to parse them with line numbers
    all_values = worksheet.get_all_values()
    if not all_values:
        return []
        
    headers = [h.strip() for h in all_values[0]]
    rows = all_values[1:]
    
    slots = []
    for idx, row in enumerate(rows, start=2): # Data starts at row 2 (row 1 is header)
        # Pad row in case it has fewer columns than headers
        padded_row = row + [""] * (len(headers) - len(row))
        
        # Build dict representation
        row_dict = {"row_num": idx}
        for h_idx, header in enumerate(headers):
            # Normalize key name to match our schema if it exists in COLUMNS
            norm_key = header.strip().lower()
            if norm_key in COLUMNS:
                row_dict[norm_key] = padded_row[h_idx]
            else:
                row_dict[header] = padded_row[h_idx]
        slots.append(row_dict)
        
    return slots

def update_changed_slots(client: gspread.Client, changed_slots: list, spreadsheet_name: str = "Matches - Slots state"):
    """
    Updates the Google Sheet for rows that have changed.
    `changed_slots` is a list of dictionaries that represent the slot states,
    which must contain the 'row_num' key.
    """
    if not changed_slots:
        return
        
    sh = client.open(spreadsheet_name)
    try:
        worksheet = sh.worksheet("_cache_blogs")
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.sheet1
    
    # Read headers to map keys to columns
    headers = [h.strip() for h in worksheet.row_values(1)]
    header_indices = {header.lower(): idx for idx, header in enumerate(headers, start=1)}
    
    # Perform batched or cell updates
    # Using batch_update is much more efficient than updating cells one by one
    body = []
    for slot in changed_slots:
        row_num = slot.get("row_num")
        if not row_num:
            continue
            
        # Update last_updated timestamp to current time in GMT+1
        from datetime import timezone, timedelta
        now_gmt1 = datetime.now(timezone.utc) + timedelta(hours=1)
        slot["last_updated"] = f"{now_gmt1.day} {now_gmt1.strftime('%B')} - {now_gmt1.strftime('%H:%M')} (UTC+1)"
        
        # Format the row list matching header positions
        row_data = [""] * len(headers)
        for key, value in slot.items():
            if key == "row_num":
                continue
            key_norm = key.lower()
            if key_norm in header_indices:
                col_idx = header_indices[key_norm]
                row_data[col_idx - 1] = str(value)
                
        # Generate the range name, e.g. A2:H2
        range_name = f"A{row_num}:{gspread.utils.rowcol_to_a1(row_num, len(headers))}"
        body.append({
            "range": range_name,
            "values": [row_data]
        })
        
    if body:
        worksheet.batch_update(body)
        logger.success(f"Sheets: Updated {len(body)} slot rows in Google Sheets.")

def fetch_team_translations_separated(client, spreadsheet_name: str = "Matches - Slots state") -> dict:
    """
    Fetches the team translations from '_cache_national_teams' and '_cache_clubs' worksheets.
    If either doesn't exist, creates it with columns:
    arabic_name, english_name, code, logo_url
    Returns a dict mapping arabic_name -> {nameEn, code, logo_url, type}.
    """
    try:
        sh = client.open(spreadsheet_name)
    except gspread.exceptions.SpreadsheetNotFound:
        try:
            sh = client.open_by_key(spreadsheet_name)
        except Exception:
            raise ValueError(f"Spreadsheet '{spreadsheet_name}' not found by name or ID.")

    translations = {}

    # Helper to load a sheet
    def load_sheet(sheet_name, type_label):
        try:
            worksheet = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sh.add_worksheet(title=sheet_name, rows="1000", cols="5")
            worksheet.append_row(["arabic_name", "english_name", "code", "logo_url"])
            return

        all_values = worksheet.get_all_values()
        if not all_values or len(all_values) <= 1:
            return

        headers = [h.strip().lower() for h in all_values[0]]
        rows = all_values[1:]
        header_map = {h: idx for idx, h in enumerate(headers)}

        for row in rows:
            padded_row = row + [""] * (len(headers) - len(row))
            arabic_name = padded_row[header_map.get("arabic_name", 0)].strip()
            if not arabic_name:
                continue
            translations[arabic_name] = {
                "nameEn": padded_row[header_map.get("english_name", 1)].strip(),
                "code": padded_row[header_map.get("code", 2)].strip(),
                "logo_url": padded_row[header_map.get("logo_url", 3)].strip(),
                "type": type_label
            }

    load_sheet("_cache_national_teams", "national")
    load_sheet("_cache_clubs", "club")

    return translations

def save_new_team_translations_separated(client, new_translations: list, spreadsheet_name: str = "Matches - Slots state"):
    """
    Appends new translation rows to either '_cache_national_teams' or '_cache_clubs' worksheet.
    `new_translations` is a list of tuples/lists: [(arabic, english, code, logo_url, "national"|"club"), ...]
    """
    if not new_translations:
        return

    try:
        sh = client.open(spreadsheet_name)
    except gspread.exceptions.SpreadsheetNotFound:
        try:
            sh = client.open_by_key(spreadsheet_name)
        except Exception:
            raise ValueError(f"Spreadsheet '{spreadsheet_name}' not found by name or ID.")

    national_rows = []
    club_rows = []

    for trans in new_translations:
        arabic, english, code, logo_url, team_type = trans
        row = [arabic, english, code, logo_url]
        if team_type == "national":
            national_rows.append(row)
        else:
            club_rows.append(row)

    def append_to_sheet(sheet_name, rows):
        if not rows:
            return
        try:
            worksheet = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sh.add_worksheet(title=sheet_name, rows="1000", cols="5")
            worksheet.append_row(["arabic_name", "english_name", "code", "logo_url"])

        worksheet.append_rows(rows)
        logger.success(f"Sheets: Appended {len(rows)} new rows to '{sheet_name}'.")

    append_to_sheet("_cache_national_teams", national_rows)
    append_to_sheet("_cache_clubs", club_rows)

def fetch_matches_cache(client, spreadsheet_name: str = "Matches - Slots state") -> dict:
    """
    Fetches matches cache from '_cache_matches' worksheet.
    If it doesn't exist, creates it with columns:
    match_url, iframe_url, status_class, last_updated
    Returns a dict mapping match_url -> {iframe_url, status_class, last_updated}.
    """
    try:
        sh = client.open(spreadsheet_name)
    except gspread.exceptions.SpreadsheetNotFound:
        try:
            sh = client.open_by_key(spreadsheet_name)
        except Exception:
            raise ValueError(f"Spreadsheet '{spreadsheet_name}' not found by name or ID.")

    sheet_name = "_cache_matches"
    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=sheet_name, rows="1000", cols="10")
        worksheet.append_row(["match_url", "iframe_url", "status_class", "last_updated"])
        return {}

    all_values = worksheet.get_all_values()
    if not all_values or len(all_values) <= 1:
        return {}

    headers = [h.strip().lower() for h in all_values[0]]
    rows = all_values[1:]

    header_map = {h: idx for idx, h in enumerate(headers)}
    
    matches_cache = {}
    for row in rows:
        padded_row = row + [""] * (len(headers) - len(row))
        match_url = padded_row[header_map.get("match_url", 0)].strip()
        if not match_url:
            continue
        
        matches_cache[match_url] = {
            "iframe_url": padded_row[header_map.get("iframe_url", 1)].strip(),
            "status_class": padded_row[header_map.get("status_class", 2)].strip(),
            "last_updated": padded_row[header_map.get("last_updated", 3)].strip(),
        }
    return matches_cache

def parse_user_styled_time(time_str: str) -> datetime:
    """
    Parses user-styled date string "29 June | 21:00 (UTC+1)" or falls back to ISO format.
    """
    if not time_str:
        return datetime.min
    try:
        clean = time_str.replace("(UTC+1)", "").replace("-", "|").strip()
        dt = datetime.strptime(clean, "%d %B | %H:%M")
        dt = dt.replace(year=datetime.now().year)
        return dt
    except Exception:
        try:
            clean_str = re.sub(r'([+-]\d{2}:?\d{2}|Z)$', '', time_str.strip())
            return datetime.fromisoformat(clean_str)
        except Exception:
            return datetime.min

def save_matches_cache(client, matches_cache: dict, spreadsheet_name: str = "Matches - Slots state"):
    """
    Clears the '_cache_matches' worksheet and updates it with the current cache entries.
    Filters out any entries older than 2 days to keep the sheet compact.
    Writes last_updated in user styled format: 'd Month | HH:MM (UTC+1)'
    """
    try:
        sh = client.open(spreadsheet_name)
    except gspread.exceptions.SpreadsheetNotFound:
        try:
            sh = client.open_by_key(spreadsheet_name)
        except Exception:
            raise ValueError(f"Spreadsheet '{spreadsheet_name}' not found by name or ID.")

    sheet_name = "_cache_matches"
    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=sheet_name, rows="1000", cols="10")

    now = datetime.now()
    from datetime import timezone, timedelta
    now_gmt1 = datetime.now(timezone.utc) + timedelta(hours=1)
    now_gmt1_str = f"{now_gmt1.day} {now_gmt1.strftime('%B')} - {now_gmt1.strftime('%H:%M')} (UTC+1)"

    valid_cache_rows = []
    
    for url, data in matches_cache.items():
        last_updated_str = data.get("last_updated", "")
        keep = True
        if last_updated_str:
            last_updated_dt = parse_user_styled_time(last_updated_str)
            if last_updated_dt != datetime.min:
                if (now - last_updated_dt).days >= 2:
                    keep = False
        if keep:
            out_time_str = last_updated_str
            if out_time_str and not any(s in out_time_str for s in ("|", "-")):
                try:
                    dt = parse_user_styled_time(out_time_str)
                    out_time_str = f"{dt.day} {dt.strftime('%B')} - {dt.strftime('%H:%M')} (UTC+1)"
                except Exception:
                    out_time_str = now_gmt1_str
            elif not out_time_str:
                out_time_str = now_gmt1_str

            valid_cache_rows.append([
                url,
                data.get("iframe_url", ""),
                data.get("status_class", ""),
                out_time_str
            ])

    worksheet.clear()
    headers = ["match_url", "iframe_url", "status_class", "last_updated"]
    all_data = [headers] + valid_cache_rows
    worksheet.update(all_data)
    logger.success(f"Sheets: Saved {len(valid_cache_rows)} match cache entries to Google Sheets.")

if __name__ == "__main__":
    # Test block
    print("Testing sheets_module.py imports and client connection initialization...")
    try:
        client = get_gspread_client()
        print("Client initialized successfully.")
    except Exception as e:
        print(f"Auth check (expected if no credentials file): {e}")
