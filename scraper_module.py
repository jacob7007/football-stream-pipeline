import os
import json
import re
import sys
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

HOMEPAGE_URL = "https://kooracitty.com/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "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")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Reconfigure stdout/stderr to use UTF-8 so Arabic team names display correctly on Windows terminals
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

def slugify(text: str) -> str:
    """
    Creates a URL/ID-safe slug from text, supporting English and Arabic.
    """
    text = text.lower().strip()
    # Keep alphanumeric characters, spaces, and hyphens; replace others
    text = re.sub(r'[^\w\s\u0600-\u06FF-]', '', text)
    # Replace spaces and multiple hyphens with a single hyphen
    text = re.sub(r'[-\s]+', '-', text)
    return text.strip('-')

def generate_stable_event_id(t1_ar: str, t1_en: str, t2_ar: str, t2_en: str, kickoff_iso: str) -> str:
    """
    Generates a unique, stable event ID based on team names and kickoff time.
    """
    t1 = t1_en if t1_en else t1_ar
    t2 = t2_en if t2_en else t2_ar
    t1_slug = slugify(t1)
    t2_slug = slugify(t2)
    
    # Extract date and time to avoid format shifts
    match = re.search(r'(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})', kickoff_iso)
    if match:
        date_str, hh, mm = match.groups()
        time_part = f"{date_str}-{hh}-{mm}"
    else:
        time_part = slugify(kickoff_iso)
        
    return f"{t1_slug}-vs-{t2_slug}-{time_part}"

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
        if w.startswith("ال") and len(w) > 2:
            cleaned_words.append(w[2:])
        else:
            cleaned_words.append(w)
    return " ".join(cleaned_words)

def get_local_fallback_mapping(team_name: str) -> dict:
    """
    Fallback team mappings when OpenRouter/LLM is unavailable.
    """
    norm = normalize_arabic(team_name)
    country_mappings = {
        "كونغو": "cd",
        "كونقو": "cd",
        "كولومب": "co",
        "كولمب": "co",
        "برتغال": "pt",
        "جزائر": "dz",
        "نمسا": "at",
        "اردن": "jo",
        "ارجنتين": "ar",
        "جنوب افريق": "za",
        "كندا": "ca",
        "كروات": "hr",
        "غانا": "gh",
        "بنما": "pa",
        "انجلتر": "gb",
        "انكلتر": "gb",
        "مغرب": "ma",
        "مصر": "eg",
        "سعود": "sa",
        "تونس": "tn",
        "عراق": "iq",
        "فرنسا": "fr",
        "اسبان": "es",
        "ايطال": "it",
        "المان": "de",
        "برازيل": "br",
        "بلجيك": "be",
        "هولند": "nl",
        "اوروغو": "uy",
        "سنغال": "sn",
        "كاميرون": "cm",
        "يابان": "jp",
        "كوريا": "kr",
        "استرال": "au",
        "امريك": "us",
        "ولايات متحد": "us",
        "مكسيك": "mx",
        "سويسر": "ch",
        "دنمارك": "dk",
        "سويد": "se",
        "نرويج": "no",
        "بولند": "pl",
        "ترك": "tr",
        "روس": "ru",
        "اوكران": "ua",
    }
    
    code = "club"
    for key, c_code in country_mappings.items():
        if key in norm:
            code = c_code
            break
            
    # Derive English name dynamically if not in dictionary
    return {
        "nameEn": team_name, # fallback to Arabic if no translation
        "code": code
    }

def get_mapping_normalized(llm_mappings: dict, team_name: str) -> dict:
    """
    Looks up a team name in the llm_mappings dictionary using normalized Arabic keys.
    Handles minor hamza/spelling variations between input name and LLM key.
    """
    if not llm_mappings:
        return None
    # 1. Direct match
    if team_name in llm_mappings:
        return llm_mappings[team_name]
        
    # 2. Normalized match
    norm_target = normalize_arabic(team_name)
    for k, v in llm_mappings.items():
        if normalize_arabic(k) == norm_target:
            return v
            
    return None

def fetch_openrouter_mappings(unique_names: list) -> dict:
    """
    Calls OpenRouter to batch translate Arabic team names to English and resolve ISO codes.
    """
    if not unique_names:
        return {}
        
    system_prompt = (
        "You are a football team database helper. You are given a list of football team/country names in Arabic.\n"
        "For each name, output a JSON object containing:\n"
        '1. \"nameEn\": The standard English name of the team/club (e.g. \"Colombia\", \"Portugal\", \"Real Madrid\", \"Al Ahly\").\n'
        '2. \"code\": The 2-letter lowercase ISO country code if the team is a national team (e.g. \"co\" for Colombia, \"pt\" for Portugal, \"ma\" for Morocco). '
        'If it is a club team or club, output \"club\".\n\n'
        "Respond ONLY with a JSON object where the keys are the input Arabic team names, and the values are the objects with 'nameEn' and 'code'. "
        "Do not write any markdown code block wrappers (like ```json), write only the raw JSON text."
    )
    
    models = ["openrouter/free", "google/gemini-2.5-flash", "meta-llama/llama-3.1-8b-instruct"]
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/google/antigravity",
        "X-Title": "Antigravity Match Scraper"
    }
    
    for model in models:
        print(f"[OpenRouter] Requesting team details using model: {model}", file=sys.stderr)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(unique_names, ensure_ascii=False)}
            ],
            "response_format": {"type": "json_object"}
        }
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=5)
            if resp.status_code == 200:
                resp_data = resp.json()
                content = resp_data["choices"][0]["message"]["content"].strip()
                
                if content.startswith("```"):
                    lines = content.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines[-1].startswith("```"):
                        lines = lines[:-1]
                    content = "\n".join(lines).strip()
                    
                print(f"[OpenRouter] API call successful using model: {model}", file=sys.stderr)
                return json.loads(content)
            else:
                print(f"[OpenRouter] Error with model {model}: HTTP {resp.status_code} - {resp.text}", file=sys.stderr)
        except Exception as e:
            print(f"[OpenRouter] Exception with model {model}: {e}", file=sys.stderr)
            
    print("[OpenRouter] All models failed. Falling back to local offline mapping rules.", file=sys.stderr)
    return {}

def parse_match_time(date_str: str, time_str: str) -> str:
    """
    Parses date and time strings (assumed GMT+3) and returns GMT+1 ISO 8601 string.
    """
    time_str = re.sub(r'\s+', ' ', time_str.strip())
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M %p")
        dt_converted = dt - timedelta(hours=2) # GMT+3 to GMT+1
        return dt_converted.strftime("%Y-%m-%dT%H:%M:%S+01:00")
    except Exception:
        return f"{date_str}T00:00:00+01:00"

def extract_stream_iframe(match_url: str) -> str:
    """
    Fetches the match detail page, searches for the stream player iframe,
    and returns its src URL if found. Returns None otherwise.
    """
    try:
        resp = requests.get(match_url, headers=HEADERS, timeout=12)
        if resp.status_code != 200:
            return None
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        iframes = soup.find_all('iframe')
        
        for iframe in iframes:
            src = iframe.get('src') or iframe.get('data-src')
            if not src:
                continue
            
            # Filter out non-streaming iframes
            if any(domain in src for domain in ['blogger.com', 'google', 'facebook', 'twitter', 'youtube', 'cloudflare']):
                continue
                
            return src
    except Exception:
        pass
    return None

def get_mock_matches() -> list:
    """
    Returns mock matches for testing the reconciliation and patch pipeline.
    """
    # Use dates/times close to current run
    base_time = datetime.now()
    t1 = (base_time + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+01:00")
    t2 = (base_time + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S+01:00")
    t3 = (base_time - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+01:00")
    
    mock_data = [
        {
            "event_id": "colombia-vs-portugal-mock-1",
            "team1": {
                "nameAr": "كولومبيا",
                "nameEn": "Colombia",
                "img": "https://flagcdn.com/co.svg"
            },
            "team2": {
                "nameAr": "البرتغال",
                "nameEn": "Portugal",
                "img": "https://flagcdn.com/pt.svg"
            },
            "time": t1,
            "duration": 150,
            "iframe_url": "https://ex.roooom.online/?alba-player=home1",
            "link": "",
            "status_class": "live"
        },
        {
            "event_id": "algeria-vs-austria-mock-2",
            "team1": {
                "nameAr": "الجزائر",
                "nameEn": "Algeria",
                "img": "https://flagcdn.com/dz.svg"
            },
            "team2": {
                "nameAr": "النمسا",
                "nameEn": "Austria",
                "img": "https://flagcdn.com/at.svg"
            },
            "time": t2,
            "duration": 150,
            "iframe_url": "https://ex.roooom.online/?alba-player=home2",
            "link": "",
            "status_class": "not-started"
        },
        {
            "event_id": "south-africa-vs-canada-mock-3",
            "team1": {
                "nameAr": "جنوب أفريقيا",
                "nameEn": "South Africa",
                "img": "https://flagcdn.com/za.svg"
            },
            "team2": {
                "nameAr": "كندا",
                "nameEn": "Canada",
                "img": "https://flagcdn.com/ca.svg"
            },
            "time": t3,
            "duration": 150,
            "iframe_url": "",
            "link": "",
            "status_class": "finished"
        }
    ]
    return mock_data

def scrape_live_matches(use_mock: bool = False) -> list:
    """
    Main function to scrape matches. Can be toggled to mock mode.
    """
    if use_mock:
        print("[Scraper] Using Mock Scraper Data.", file=sys.stderr)
        return get_mock_matches()

    pages = [
        {"url": "https://kooracitty.com/matches-today-1/", "allowed_statuses": ["live", "not-started", "finished"]},
        {"url": "https://kooracitty.com/matches-tomorrow/", "allowed_statuses": ["live", "not-started"]}
    ]

    matches_to_process = []
    unique_team_names = set()
    seen_links = set()

    for page in pages:
        url = page["url"]
        allowed = page["allowed_statuses"]
        print(f"[Scraper] Fetching matches page from {url}...", file=sys.stderr)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"[Scraper] Failed to fetch page {url}: {e}", file=sys.stderr)
            continue

        soup = BeautifulSoup(resp.text, 'html.parser')
        match_elements = soup.select('.AY_Match')
        # Filter out "No matches today" placeholders
        real_matches = [m for m in match_elements if not m.select_one('.no-data__msg')]
        print(f"[Scraper] Found {len(real_matches)} matches on {url.split('/')[-2]} page.", file=sys.stderr)

        for match in real_matches:
            classes = match.get('class', [])
            
            # Identify match status
            status_class = None
            for cls in allowed:
                if cls in classes:
                    status_class = cls
                    break
            
            if not status_class:
                continue

            link_elem = match.find('a', href=True)
            if not link_elem:
                continue
                
            match_url = urljoin(HOMEPAGE_URL, link_elem['href'])
            if match_url in seen_links:
                continue
            seen_links.add(match_url)

            team1_elem = match.select_one('.TM1 .TM_Name')
            team2_elem = match.select_one('.TM2 .TM_Name')
            team1_name = team1_elem.get_text(strip=True) if team1_elem else "Unknown Team 1"
            team2_name = team2_elem.get_text(strip=True) if team2_elem else "Unknown Team 2"

            t1_img_elem = match.select_one('.TM1 .TM_Logo img')
            t2_img_elem = match.select_one('.TM2 .TM_Logo img')
            t1_orig_img = t1_img_elem.get('data-src') or t1_img_elem.get('src') if t1_img_elem else ""
            t2_orig_img = t2_img_elem.get('data-src') or t2_img_elem.get('src') if t2_img_elem else ""

            title_str = link_elem.get('title', '')
            date_match = re.search(r'\d{4}-\d{2}-\d{2}', title_str)
            date_str = date_match.group(0) if date_match else datetime.today().strftime('%Y-%m-%d')

            time_elem = match.select_one('.MT_Time')
            time_str = time_elem.get_text(strip=True) if time_elem else "12:00 AM"

            unique_team_names.add(team1_name)
            unique_team_names.add(team2_name)

            matches_to_process.append({
                "team1_name": team1_name,
                "team2_name": team2_name,
                "team1_orig_img": t1_orig_img,
                "team2_orig_img": t2_orig_img,
                "date_str": date_str,
                "time_str": time_str,
                "match_url": match_url,
                "status_class": status_class
            })

    print(f"[Scraper] Processing {len(matches_to_process)} total scraped matches...", file=sys.stderr)
    print(f"[Scraper] Sending {len(unique_team_names)} unique teams to OpenRouter for translation & mapping...", file=sys.stderr)

    llm_mappings = fetch_openrouter_mappings(list(unique_team_names))
    print("[Scraper] Mapping completed. Resolving match detail iframes...", file=sys.stderr)

    parsed_matches = []
    for idx, match_data in enumerate(matches_to_process, 1):
        t1_name = match_data["team1_name"]
        t2_name = match_data["team2_name"]

        # Resolve translation & flag/logo mapping
        t1_info = get_mapping_normalized(llm_mappings, t1_name) or get_local_fallback_mapping(t1_name)
        t2_info = get_mapping_normalized(llm_mappings, t2_name) or get_local_fallback_mapping(t2_name)

        t1_code = t1_info.get("code", "club")
        t2_code = t2_info.get("code", "club")

        if t1_code not in ["club", "un"] and len(t1_code) == 2:
            team1_img = f"https://flagcdn.com/{t1_code}.svg"
        else:
            team1_img = match_data["team1_orig_img"]

        if t2_code not in ["club", "un"] and len(t2_code) == 2:
            team2_img = f"https://flagcdn.com/{t2_code}.svg"
        else:
            team2_img = match_data["team2_orig_img"]

        formatted_time = parse_match_time(match_data["date_str"], match_data["time_str"])
        
        # Scrape iframe for live player (do not skip if not found)
        t1_en_log = t1_info.get("nameEn") or t1_name
        t2_en_log = t2_info.get("nameEn") or t2_name
        print(f"[Scraper] Fetching stream iframe for: {t1_en_log} vs {t2_en_log}...", file=sys.stderr)
        iframe_url = extract_stream_iframe(match_data["match_url"]) or ""
            
        event_id = generate_stable_event_id(
            t1_name, t1_info.get("nameEn", ""), 
            t2_name, t2_info.get("nameEn", ""), 
            formatted_time
        )

        parsed_matches.append({
            "event_id": event_id,
            "team1": {
                "nameAr": t1_name,
                "nameEn": t1_info.get("nameEn", ""),
                "img": team1_img
            },
            "team2": {
                "nameAr": t2_name,
                "nameEn": t2_info.get("nameEn", ""),
                "img": team2_img
            },
            "time": formatted_time,
            "duration": 150,
            "iframe_url": iframe_url,
            "link": "",
            "status_class": match_data["status_class"]
        })

    return parsed_matches

if __name__ == "__main__":
    # Test execution
    print("Running scraper module test (Mock Mode)...")
    res = scrape_live_matches(use_mock=True)
    print(json.dumps(res, indent=2, ensure_ascii=False))
