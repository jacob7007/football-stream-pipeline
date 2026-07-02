import os
import re
import logger

def normalize_arabic(text: str) -> str:
    """
    Normalizes Arabic text spelling variations for fallback mapping.
    """
    if not text:
        return ""
    text = text.lower().strip()
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ة", "ه")
    words = text.split()
    cleaned_words = []
    for w in words:
        if w.startswith("وال") and len(w) > 3:
            w = w[1:] # Strip "و" to leave "ال..."
        if w.startswith("ال") and len(w) > 2:
            cleaned_words.append(w[2:])
        else:
            cleaned_words.append(w)
    return " ".join(cleaned_words)

def load_team_translations(client, spreadsheet_name: str = "Streaming Dashboard") -> dict:
    """
    Fetches the team translations from '_cache_national_teams' and '_cache_clubs' worksheets.
    If either doesn't exist, creates it.
    Returns a unified dict mapping: arabic_name_or_alias -> translation details
    including 'primary_arabic', 'original_arabic_cell', 'row_num', and 'sheet_name'.
    """
    try:
        sh = client.open(spreadsheet_name)
    except Exception:
        try:
            sh = client.open_by_key(spreadsheet_name)
        except Exception:
            raise ValueError(f"Spreadsheet '{spreadsheet_name}' not found by name or ID.")

    translations = {}

    def load_sheet(sheet_name, type_label):
        try:
            worksheet = sh.worksheet(sheet_name)
        except Exception:
            # Add worksheet if not found
            worksheet = sh.add_worksheet(title=sheet_name, rows="1000", cols="5")
            worksheet.append_row(["arabic_name", "english_name", "code", "logo_url"])
            return

        all_values = worksheet.get_all_values()
        if not all_values or len(all_values) <= 1:
            return

        headers = [h.strip().lower() for h in all_values[0]]
        rows = all_values[1:]
        header_map = {h: idx for idx, h in enumerate(headers)}

        for idx, row in enumerate(rows, start=2): # row 1 is header, data starts at row 2
            padded_row = row + [""] * (len(headers) - len(row))
            arabic_name_raw = padded_row[header_map.get("arabic_name", 0)].strip()
            if not arabic_name_raw:
                continue

            # Split raw cell by separators: |, comma, semicolon, or newline
            aliases = [a.strip() for a in re.split(r'[|;\n,]', arabic_name_raw) if a.strip()]
            if not aliases:
                continue

            primary_arabic = aliases[0]
            name_en = padded_row[header_map.get("english_name", 1)].strip()
            code = padded_row[header_map.get("code", 2)].strip()
            logo_url = padded_row[header_map.get("logo_url", 3)].strip()

            for alias in aliases:
                translations[alias] = {
                    "nameEn": name_en,
                    "code": code,
                    "logo_url": logo_url,
                    "type": type_label,
                    "row_num": idx,
                    "sheet_name": sheet_name,
                    "primary_arabic": primary_arabic,
                    "original_arabic_cell": arabic_name_raw
                }

    load_sheet("_cache_national_teams", "national")
    load_sheet("_cache_clubs", "club")

    return translations

def find_existing_translation(name: str, team_translations: dict) -> dict:
    """
    Looks up a team name in the team_translations cache.
    1. Direct lookup
    2. Normalized Arabic lookup
    """
    if not team_translations:
        return None

    # 1. Direct match
    if name in team_translations:
        return team_translations[name]

    # 2. Normalized match
    norm_name = normalize_arabic(name)
    for k, v in team_translations.items():
        if normalize_arabic(k) == norm_name:
            return v

    return None

def update_team_aliases(client, alias_updates: list, spreadsheet_name: str = "Streaming Dashboard"):
    """
    Appends new aliases to existing rows in Google Sheets.
    `alias_updates` is a list of tuples/lists: [(row_num, sheet_name, new_arabic_cell_val), ...]
    """
    if not alias_updates:
        return

    try:
        sh = client.open(spreadsheet_name)
    except Exception:
        try:
            sh = client.open_by_key(spreadsheet_name)
        except Exception:
            raise ValueError(f"Spreadsheet '{spreadsheet_name}' not found by name or ID.")

    # Group by worksheet to minimize loading worksheet calls
    by_sheet = {}
    for row_num, sheet_name, new_val in alias_updates:
        by_sheet.setdefault(sheet_name, []).append((row_num, new_val))

    for sheet_name, updates in by_sheet.items():
        try:
            worksheet = sh.worksheet(sheet_name)
        except Exception as e:
            logger.error(f"TranslationManager: Worksheet '{sheet_name}' not found for updating aliases: {e}")
            continue

        for row_num, new_val in updates:
            worksheet.update_cell(row_num, 1, new_val)
            logger.success(f"TranslationManager: Appended alias in '{sheet_name}' row {row_num} to: '{new_val}'")

def save_new_team_translations_separated(client, new_translations: list, spreadsheet_name: str = "Streaming Dashboard"):
    """
    Appends new translation rows to either '_cache_national_teams' or '_cache_clubs' worksheet.
    `new_translations` is a list of tuples/lists: [(arabic, english, code, logo_url, "national"|"club"), ...]
    """
    if not new_translations:
        return

    try:
        sh = client.open(spreadsheet_name)
    except Exception:
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
        except Exception:
            worksheet = sh.add_worksheet(title=sheet_name, rows="1000", cols="5")
            worksheet.append_row(["arabic_name", "english_name", "code", "logo_url"])

        worksheet.append_rows(rows)
        logger.success(f"TranslationManager: Appended {len(rows)} new rows to '{sheet_name}'.")

    append_to_sheet("_cache_national_teams", national_rows)
    append_to_sheet("_cache_clubs", club_rows)
