import os
import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

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
            
    worksheet = sh.sheet1
    
    # Read headers to verify structure
    headers = [h.strip().lower() for h in worksheet.row_values(1)]
    
    # Verify all required columns exist (case-insensitive check)
    for col in COLUMNS:
        if col not in headers:
            # We will continue but warn or handle dynamically if mapping differs
            print(f"Warning: Column '{col}' not found in Google Sheet headers: {headers}")
            
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
            
        # Update last_updated timestamp to current time
        slot["last_updated"] = datetime.now().isoformat()
        
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
        print(f"Updated {len(body)} slot rows in Google Sheets.")

if __name__ == "__main__":
    # Test block
    print("Testing sheets_module.py imports and client connection initialization...")
    try:
        client = get_gspread_client()
        print("Client initialized successfully.")
    except Exception as e:
        print(f"Auth check (expected if no credentials file): {e}")
