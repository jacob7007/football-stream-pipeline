import os
import json
import re
import sys
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import logger

SCRAPER_URLS_ENV = os.environ.get("SCRAPER_URLS", "").strip()
SCRAPER_URLS = []
if SCRAPER_URLS_ENV:
    if SCRAPER_URLS_ENV.startswith("[") and SCRAPER_URLS_ENV.endswith("]"):
        try:
            parsed = json.loads(SCRAPER_URLS_ENV)
            if isinstance(parsed, list):
                SCRAPER_URLS = [url.strip() for url in parsed if isinstance(url, str) and url.strip()]
        except Exception:
            pass
    if not SCRAPER_URLS:
        SCRAPER_URLS = [url.strip() for url in SCRAPER_URLS_ENV.split(",") if url.strip()]

# Headers for requests
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SCRAPING_PROXY = os.environ.get("SCRAPING_PROXY")

def get_request_proxies():
    if SCRAPING_PROXY:
        return {
            "http": SCRAPING_PROXY,
            "https": SCRAPING_PROXY
        }
    return None

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

def generate_stable_event_id(t1_code: str, t2_code: str, kickoff_iso: str) -> str:
    """
    Generates a unique, stable event ID in format: code1-vs-code2-yy-mm-dd
    where team codes are lowercase.
    """
    c1 = (t1_code or "team1").strip().lower()
    c2 = (t2_code or "team2").strip().lower()
    
    # Extract date parts: yy-mm-dd
    match = re.search(r'(\d{2})(\d{2})-(\d{2})-(\d{2})', kickoff_iso)
    if match:
        _, yy, mm, dd = match.groups()
        date_part = f"{yy}-{mm}-{dd}"
    else:
        date_part = "26-00-00"
        
    return f"{c1}-vs-{c2}-{date_part}"

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
        '1. "nameEn": The standard English name of the team/club (e.g. "Colombia", "Portugal", "Real Madrid", "Al Ahly", "Barcelona").\n'
        '2. "code":\n'
        '   - If it is a national team, output the 2-letter lowercase ISO country code (e.g. "co" for Colombia, "pt" for Portugal, "ma" for Morocco).\n'
        '   - If it is a club team/club, output a standard 3-letter or 3-4 letter uppercase abbreviation/code for the club (e.g. "RMA" for Real Madrid, "FCB" for Barcelona, "WAC" for Wydad AC, "MUN" for Manchester United, "AHL" for Al Ahly).\n\n'
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
        logger.info(f"OpenRouter: Requesting team details using model: {model}")
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
                    
                logger.success(f"OpenRouter: API call successful using model: {model}")
                return json.loads(content)
            else:
                logger.error(f"OpenRouter: Error with model {model}: HTTP {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"OpenRouter: Exception with model {model}: {e}")
            
    logger.warning("OpenRouter: All models failed. Falling back to local offline mapping rules.")
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
        resp = requests.get(match_url, headers=HEADERS, timeout=12, proxies=get_request_proxies())
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
            "duration": 180,
            "iframe_url": "https://ex.roooom.online/?alba-player=home1",
            "link": "",
            "status_class": "live",
            "match_url": urljoin(SCRAPER_URLS[0] if SCRAPER_URLS else "https://example.com/", "colombia-vs-portugal-mock-1/")
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
            "duration": 180,
            "iframe_url": "https://ex.roooom.online/?alba-player=home2",
            "link": "",
            "status_class": "not-started",
            "match_url": urljoin(SCRAPER_URLS[0] if SCRAPER_URLS else "https://example.com/", "algeria-vs-austria-mock-2/")
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
            "duration": 180,
            "iframe_url": "",
            "link": "",
            "status_class": "finished",
            "match_url": urljoin(SCRAPER_URLS[0] if SCRAPER_URLS else "https://example.com/", "south-africa-vs-canada-mock-3/")
        }
    ]
    return mock_data

def get_status_priority(status: str) -> int:
    if status == "live":
        return 2
    if status == "not-started":
        return 1
    return 0

def scrape_live_matches(use_mock: bool = False, team_translations: dict = None, matches_cache: dict = None) -> tuple:
    """
    Main function to scrape matches. Can be toggled to mock mode.
    Returns (parsed_matches, new_translations_list, updated_matches_cache).
    """
    if team_translations is None:
        team_translations = {}
    if matches_cache is None:
        matches_cache = {}

    if use_mock:
        logger.info("Scraper: Using Mock Scraper Data.")
        return get_mock_matches(), [], {}

    if not SCRAPER_URLS:
        logger.error("SCRAPER_URLS environment variable is not set. Cannot run competitor scraper.")
        return [], [], {}

    urls_to_scrape = SCRAPER_URLS

    matches_to_process = []
    unique_team_names = set()
    seen_links = set()

    for url in urls_to_scrape:
        # Determine default date based on URL (today vs tomorrow)
        is_tomorrow_page = "tomorrow" in url.lower()
        if is_tomorrow_page:
            default_date = (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            default_date = datetime.today().strftime('%Y-%m-%d')

        logger.info(f"Scraper: Fetching matches page from {url}...")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20, proxies=get_request_proxies())
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Scraper: Failed to fetch page {url}: {e}")
            continue

        soup = BeautifulSoup(resp.text, 'html.parser')
        match_elements = soup.select('.AY_Match, .match-container')
        # Filter out "No matches today" placeholders
        real_matches = [m for m in match_elements if not m.select_one('.no-data__msg')]
        page_name = url.rstrip("/").split("/")[-1]
        logger.info(f"Scraper: Found {len(real_matches)} matches on {page_name} page.")

        for match in real_matches:
            classes = match.get('class', [])
            is_variant_2 = 'match-container' in classes
            
            # Identify match status
            status_class = None
            if is_variant_2:
                if 'live' in classes or 'live2' in classes:
                    status_class = 'live'
                elif 'end' in classes or 'finished' in classes:
                    status_class = 'finished'
                elif 'comming-soon' in classes or 'not-started' in classes or 'not-start' in classes:
                    status_class = 'not-started'
            else:
                for cls in ["live", "not-started", "finished"]:
                    if cls in classes:
                        status_class = cls
                        break
            
            if not status_class:
                continue

            link_elem = match.find('a', href=True)
            if not link_elem:
                continue
                
            match_url = urljoin(url, link_elem['href'])
            if match_url in seen_links:
                continue
            seen_links.add(match_url)

            if is_variant_2:
                team1_elem = match.select_one('.right-team .team-name')
                team2_elem = match.select_one('.left-team .team-name')
                t1_img_elem = match.select_one('.right-team .team-logo img')
                t2_img_elem = match.select_one('.left-team .team-logo img')
                time_elem = match.select_one('.match-time')
            else:
                team1_elem = match.select_one('.TM1 .TM_Name')
                team2_elem = match.select_one('.TM2 .TM_Name')
                t1_img_elem = match.select_one('.TM1 .TM_Logo img')
                t2_img_elem = match.select_one('.TM2 .TM_Logo img')
                time_elem = match.select_one('.MT_Time')

            team1_name = team1_elem.get_text(strip=True) if team1_elem else "Unknown Team 1"
            team2_name = team2_elem.get_text(strip=True) if team2_elem else "Unknown Team 2"

            t1_orig_img = t1_img_elem.get('data-src') or t1_img_elem.get('src') if t1_img_elem else ""
            t2_orig_img = t2_img_elem.get('data-src') or t2_img_elem.get('src') if t2_img_elem else ""

            title_str = link_elem.get('title', '')
            date_match = re.search(r'\d{4}-\d{2}-\d{2}', title_str)
            date_str = date_match.group(0) if date_match else default_date

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

    logger.info(f"Scraper: Processing {len(matches_to_process)} total scraped matches...")
    
    # Send only missing team names to OpenRouter
    missing_team_names = [name for name in unique_team_names if name not in team_translations]
    
    new_translations_list = []
    
    if missing_team_names:
        logger.info(f"Scraper: Sending {len(missing_team_names)} new/untranslated teams to OpenRouter...")
        llm_mappings = fetch_openrouter_mappings(missing_team_names)
        
        # Merge new translations into cache
        for name in missing_team_names:
            info = get_mapping_normalized(llm_mappings, name) or get_local_fallback_mapping(name)
            
            # Determine logo URL and team type
            code = info.get("code", "club")
            if code.islower() and len(code) == 2:
                team_type = "national"
                logo_url = f"https://flagcdn.com/{code.lower()}.svg"
            else:
                team_type = "club"
                # Find matching match_data to extract original image
                logo_url = ""
                for m in matches_to_process:
                    if m["team1_name"] == name:
                        logo_url = m["team1_orig_img"]
                        break
                    elif m["team2_name"] == name:
                        logo_url = m["team2_orig_img"]
                        break
            
            team_translations[name] = {
                "nameEn": info.get("nameEn", ""),
                "code": code,
                "logo_url": logo_url,
                "type": team_type
            }
            new_translations_list.append((name, info.get("nameEn", ""), code, logo_url, team_type))
    else:
        print()
        logger.success("Scraper: All teams found in translation cache. Skipping OpenRouter.")

    logger.success("Scraper: Translation completed. Resolving match detail iframes.")

    # Group matches by event_id first
    events_grouped = {}
    for match_data in matches_to_process:
        t1_name = match_data["team1_name"]
        t2_name = match_data["team2_name"]
        
        t1_info = team_translations.get(t1_name) or get_local_fallback_mapping(t1_name)
        t2_info = team_translations.get(t2_name) or get_local_fallback_mapping(t2_name)
        
        t1_code = t1_info.get("code", "club")
        t2_code = t2_info.get("code", "club")
        formatted_time = parse_match_time(match_data["date_str"], match_data["time_str"])
        event_id = generate_stable_event_id(t1_code, t2_code, formatted_time)
        
        match_data["event_id"] = event_id
        match_data["formatted_time"] = formatted_time
        match_data["t1_info"] = t1_info
        match_data["t2_info"] = t2_info
        
        events_grouped.setdefault(event_id, []).append(match_data)

    # Log overlaps and unique matches
    print()
    logger.info("Scraper: Analyzing multi-source match details...")
    for event_id, candidates in events_grouped.items():
        t1_name = candidates[0]["t1_info"].get("nameEn") or candidates[0]["team1_name"]
        t2_name = candidates[0]["t2_info"].get("nameEn") or candidates[0]["team2_name"]
        event_name = f"{t1_name} vs {t2_name}"
        
        urls = [c["match_url"] for c in candidates]
        if len(candidates) > 1:
            domains = [url.split("//")[-1].split("/")[0] for url in urls]
            logger.info(f"Overlap: '{event_name}' found on {len(candidates)} pages: {', '.join(domains)}")
        else:
            domain = urls[0].split("//")[-1].split("/")[0]
            logger.info(f"Unique: '{event_name}' only found on: {domain}")
    print()

    parsed_matches = []
    updated_matches_cache = {}
    now_dt = datetime.now()

    has_logged_fetching = False
    for event_id, candidates in events_grouped.items():
        # Determine overall status_class (prioritize live > not-started > finished)
        overall_status_class = "finished"
        best_priority = -1
        for cand in candidates:
            cached_match = matches_cache.get(cand["match_url"])
            status = cand["status_class"]
            if cached_match and cached_match.get("status_class") in ["finished", "manually-finished"]:
                status = "finished"
            
            priority = get_status_priority(status)
            if priority > best_priority:
                best_priority = priority
                overall_status_class = status

        first_cand = candidates[0]
        t1_name = first_cand["team1_name"]
        t2_name = first_cand["team2_name"]
        t1_info = first_cand["t1_info"]
        t2_info = first_cand["t2_info"]
        team1_img = t1_info.get("logo_url") or first_cand["team1_orig_img"]
        team2_img = t2_info.get("logo_url") or first_cand["team2_orig_img"]
        formatted_time = first_cand["formatted_time"]

        # Parse kickoff time to check if kickoff is far in the future
        kickoff_dt = datetime.min
        try:
            clean_time = re.sub(r'([+-]\d{2}:?\d{2}|Z)$', '', formatted_time)
            kickoff_dt = datetime.fromisoformat(clean_time)
        except Exception:
            pass

        is_finished = (overall_status_class == "finished")
        is_live = (overall_status_class == "live")
        
        is_far_future = False
        if overall_status_class == "not-started" and kickoff_dt != datetime.min:
            time_until_kickoff = (kickoff_dt - now_dt).total_seconds()
            if time_until_kickoff > 3 * 60 * 60: # > 3 hours away
                is_far_future = True

        iframe_url = ""
        resolved_match_url = first_cand["match_url"]

        if is_finished:
            iframe_url = ""
            for cand in candidates:
                updated_matches_cache[cand["match_url"]] = {
                    "iframe_url": "",
                    "status_class": "finished",
                    "last_updated": now_dt.isoformat()
                }
        else:
            # Fallback chain across candidates
            for cand in candidates:
                cand_url = cand["match_url"]
                cached_match = matches_cache.get(cand_url)
                
                cand_status = cand["status_class"]
                if cached_match and cached_match.get("status_class") in ["finished", "manually-finished"]:
                    cand_status = "finished"

                use_cache = False
                if is_far_future:
                    use_cache = True
                elif cand_status == "not-started":
                    if cached_match and cached_match.get("iframe_url"):
                        use_cache = True
                
                cand_iframe = ""
                if use_cache:
                    cand_iframe = cached_match.get("iframe_url", "") if cached_match else ""
                else:
                    if not has_logged_fetching:
                        print()
                        logger.info("Scraper: Fetching stream iframe.")
                        has_logged_fetching = True
                    logger.info(f"Scraper: Fetching iframe for {event_id} from {cand_url}...")
                    cand_iframe = extract_stream_iframe(cand_url) or ""

                if cand_iframe:
                    iframe_url = cand_iframe
                    resolved_match_url = cand_url
                    updated_matches_cache[cand_url] = {
                        "iframe_url": iframe_url,
                        "status_class": overall_status_class,
                        "last_updated": now_dt.isoformat()
                    }
                    break
                else:
                    updated_matches_cache[cand_url] = {
                        "iframe_url": "",
                        "status_class": overall_status_class,
                        "last_updated": now_dt.isoformat()
                    }
            
            # Fill skipped candidates with current resolved state
            for cand in candidates:
                cand_url = cand["match_url"]
                if cand_url not in updated_matches_cache:
                    updated_matches_cache[cand_url] = {
                        "iframe_url": iframe_url if resolved_match_url == cand_url else "",
                        "status_class": overall_status_class,
                        "last_updated": now_dt.isoformat()
                    }

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
            "duration": 180,
            "iframe_url": iframe_url,
            "link": "",
            "status_class": overall_status_class,
            "match_url": resolved_match_url
        })

    return parsed_matches, new_translations_list, updated_matches_cache

if __name__ == "__main__":
    # Test execution
    print("Running scraper module test (Mock Mode)...")
    res, _, _ = scrape_live_matches(use_mock=True)
    print(json.dumps(res, indent=2, ensure_ascii=False))
