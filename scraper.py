import os
import re
import json
import time
import math
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from client import ArchivebateSession
from storage import storage

logger = logging.getLogger("archivebate_scraper")

# Lista przykładowych, popularnych tagów z serwisu Archivebate / kamerkowych
POPULAR_TAGS = [
    {"name": "Trans", "tag": "trans", "category": "gender"},
    {"name": "Female", "tag": "female", "category": "gender"},
    {"name": "Couple", "tag": "couple", "category": "gender"},
    {"name": "Group", "tag": "group", "category": "gender"},
    {"name": "Teen (18+)", "tag": "teen", "category": "category"},
    {"name": "Lovense", "tag": "lovense", "category": "toy"},
    {"name": "Blonde", "tag": "blonde", "category": "appearance"},
    {"name": "Brunette", "tag": "brunette", "category": "appearance"},
    {"name": "Redhead", "tag": "redhead", "category": "appearance"},
    {"name": "Ebony", "tag": "ebony", "category": "appearance"},
    {"name": "Asian", "tag": "asian", "category": "appearance"},
    {"name": "Latina", "tag": "latina", "category": "appearance"},
    {"name": "MILF", "tag": "milf", "category": "category"},
    {"name": "Big Boobs", "tag": "bigboobs", "category": "appearance"},
    {"name": "Squirt", "tag": "squirt", "category": "action"},
    {"name": "Anal", "tag": "anal", "category": "action"},
    {"name": "Dildo", "tag": "dildo", "category": "toy"},
    {"name": "Polish", "tag": "polish", "category": "region"},
    {"name": "Melody", "tag": "melody", "category": "model"},
    {"name": "Chaturbate", "tag": "chaturbate", "category": "platform"},
    {"name": "Stripchat", "tag": "stripchat", "category": "platform"},
    {"name": "Camsoda", "tag": "camsoda", "category": "platform"}
]

def unpack_mixdrop(html: str) -> Optional[str]:
    """Wypakowuje bezpośredni link do strumienia MP4 ze skryptu Mixdrop."""
    try:
        match = re.search(r"eval\(function\(p,a,c,k,e,d\)\{.*?return p\}\('(.*?)',(\d+),(\d+),'([^']+)'\.split\('\|'\)", html, re.DOTALL)
        if not match:
            return None
        p, a, c, k = match.groups()
        c = int(c)
        k = k.split('|')
        
        for i in range(c - 1, -1, -1):
            token = k[i] if i < len(k) and k[i] else str(i)
            if token:
                p = re.sub(r'\b' + str(i) + r'\b', token, p)
                
        wurl_m = re.search(r'wurl\s*=\s*"([^"]+)"', p) or re.search(r'wurl="([^"]+)"', p)
        if wurl_m:
            wurl = wurl_m.group(1)
            if wurl.startswith("//"):
                wurl = "https:" + wurl
            return wurl
    except Exception as e:
        logger.error(f"Błąd wypakowywania Mixdrop: {e}")
    return None

# ============================================================
# DOKŁADNE REGUŁY KLASYFIKACJI TAGÓW DLA KAŻDEGO WIDEO
# ============================================================
KNOWN_TAG_RULES = [
    ("trans", ["trans", "shemale", "femboy", "ladyboy", "ts-", "-ts", "trap"]),
    ("teen", ["teen", "college", "young", "petite", "18yo", "19yo", "schoolgirl"]),
    ("milf", ["milf", "mature", "mom", "cougar"]),
    ("anal", ["anal", "butt", "ass", "pegging", "prostate"]),
    ("squirt", ["squirt", "gush", "creampie"]),
    ("lovense", ["lovense", "lush", "toy", "vibrat"]),
    ("blonde", ["blond", "blonde", "blondy"]),
    ("brunette", ["brunette", "brown"]),
    ("redhead", ["redhead", "ginger", "red"]),
    ("bbw", ["bbw", "curvy", "thick", "chubby", "plump"]),
    ("ebony", ["ebony", "black"]),
    ("latina", ["latina", "colombian", "venezuelan", "brazilian", "mexican", "spanish"]),
    ("asian", ["asian", "japanese", "korean", "thai", "oriental"]),
    ("couple", ["couple", "duo", "two", "mf", "ff", "mm", "threesome", "gangbang"]),
    ("feet", ["feet", "foot", "toes", "soles", "stockings", "nylon", "socks"]),
    ("dildo", ["dildo", "strap", "penetrat", "fucking"]),
    ("cosplay", ["cosplay", "anime", "costume", "nurse", "maid"]),
    ("tattoo", ["tattoo", "inked", "piercing"]),
    ("shower", ["shower", "bath", "oil", "soap"]),
    ("bigboobs", ["bigboobs", "boobs", "tits", "huge"]),
    ("pussy", ["pussy", "fingering", "masturbat"]),
    ("striptease", ["striptease", "strip", "dance"]),
]

def extract_video_tags(v: Dict[str, Any]) -> List[str]:
    """Ustala dokładne tagi dla każdego wideo na podstawie platformy, profilu, słów kluczowych i opisu."""
    tags = set(v.get("tags") or [])
    
    # 1. Platforma
    platform = v.get("platform", "")
    if platform and platform.lower() not in ["archive", "archivebate"]:
        tags.add(platform)
        
    # 2. Płeć / Gender z wideo lub z bazy profili
    gender = v.get("gender", "")
    username = v.get("username", "")
    try:
        from model_tags import model_tag_manager
        model_info = model_tag_manager.get_model(username) if username else None
        if model_info:
            if model_info.get("gender"):
                gender = model_info["gender"]
            for mt in model_info.get("tags", []):
                tags.add(mt)
    except Exception:
        pass
    
    if gender:
        tags.add(gender.capitalize())
        if gender.lower() == "trans":
            tags.add("Trans")
            if "Female" in tags:
                tags.remove("Female")
            
    # 3. Analiza tekstu (tytuł, słowa kluczowe, opis, username)
    kw_list = v.get("keywords") or []
    if isinstance(kw_list, list):
        kw_text = " ".join(kw_list)
    else:
        kw_text = str(kw_list)
        
    full_text = f"{v.get('title', '')} {v.get('username', '')} {kw_text} {v.get('description', '')}".lower()
    
    for tag_name, keywords in KNOWN_TAG_RULES:
        for kw in keywords:
            if kw in full_text:
                tags.add(tag_name.capitalize())
                break
                
    if not any(t in tags for t in ["Trans", "Female", "Male", "Couple"]):
        tags.add("Female")
    elif "Trans" in tags and "Female" in tags:
        tags.remove("Female")
        
    res_tags = sorted(list(tags))
    v["tags"] = res_tags
    if gender:
        v["gender"] = gender
    return res_tags

def parse_date_to_sort_seconds(date_str: str) -> float:
    """Konwertuje dowolny napis daty/czasu z Archivebate na szacunkowy wiek w sekundach (mniejsza wartość = nowsze nagranie)."""
    if not date_str:
        return 999999999.0
    s = str(date_str).lower().strip()

    if any(w in s for w in ['just now', 'teraz', 'niedawno', 'a few seconds', 'chwilę temu']):
        return 0.0

    # Minuty
    m_min = re.search(r'(\d+)\s*(?:min|minute|minut)', s)
    if m_min:
        return float(m_min.group(1)) * 60.0

    # Godziny
    m_hour = re.search(r'(\d+)\s*(?:hour|godz)', s)
    if m_hour:
        return float(m_hour.group(1)) * 3600.0

    # Dni
    m_day = re.search(r'(\d+)\s*(?:day|dni|dzień)', s)
    if m_day:
        return float(m_day.group(1)) * 86400.0

    # Tygodnie
    m_week = re.search(r'(\d+)\s*(?:week|tyg)', s)
    if m_week:
        return float(m_week.group(1)) * 7 * 86400.0

    # Miesiące
    m_month = re.search(r'(\d+)\s*(?:month|mies)', s)
    if m_month:
        return float(m_month.group(1)) * 30 * 86400.0

    # Lata
    m_year = re.search(r'(\d+)\s*(?:year|lat|rok)', s)
    if m_year:
        return float(m_year.group(1)) * 365 * 86400.0

    # Kalendarzowa data DD.MM.YYYY
    m_dmy = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', s)
    if m_dmy:
        try:
            d, m, y = map(int, m_dmy.groups())
            dt = datetime(y, m, d)
            now = datetime.now()
            return max(0.0, (now - dt).total_seconds())
        except Exception:
            pass

    # Kalendarzowa data YYYY-MM-DD
    m_ymd = re.search(r'(\d{4})[/-](\d{2})[/-](\d{2})', s)
    if m_ymd:
        try:
            y, m, d = map(int, m_ymd.groups())
            dt = datetime(y, m, d)
            now = datetime.now()
            return max(0.0, (now - dt).total_seconds())
        except Exception:
            pass

    return 500000000.0

def sort_videos_newest_first(videos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sortuje listę filmów bezwzględnie chronologicznie od najmłodszego do najstarszego (godziny po kolei, bez skoków 5->8->6)."""
    def sort_key(v):
        try:
            age_sec = parse_date_to_sort_seconds(v.get("date", ""))
            raw_id = re.sub(r'\D', '', str(v.get("id", "")))
            id_val = int(raw_id) if raw_id else 0
            # Mniejszy wiek w sekundach = młodszy (wyżej na liście); przy tej samej liczbie godzin nowszy ID rozstrzyga remis
            return (age_sec, -id_val)
        except Exception:
            return (999999999.0, 0)
    return sorted(videos, key=sort_key)

class ArchivebateScraper:
    def __init__(self, session: ArchivebateSession):
        self.session = session
        self._cache: Dict[str, Any] = {}

    def parse_video_card(self, section_html: str) -> Optional[Dict[str, Any]]:
        """Parsuje pojedynczy kafelek wideo z plakatami JPG oraz wideo podglądu MP4."""
        try:
            url_m = re.search(r'href="([^"]*watch/[^"]+)"', section_html)
            if not url_m:
                return None
            watch_url = url_m.group(1)
            video_id_m = re.search(r'/watch/([0-9a-zA-Z]+)', watch_url)
            video_id = video_id_m.group(1) if video_id_m else ""

            # Wyciąganie plakatu i wideo podglądu (odrzucamy logo/awatar)
            raw_media = ""
            poster_m = re.search(r'poster="([^"]+)"', section_html)
            if poster_m and "logo" not in poster_m.group(1).lower():
                raw_media = poster_m.group(1)

            if not raw_media:
                # Szukamy URL w background-image (dla starszych wideo z Livewire)
                bg_urls = re.findall(r'url\([\'"]?([^\'")]+)[\'"]?\)', section_html)
                clean_bgs = [u.replace("&quot;", "").replace('"', '').replace("'", "") for u in bg_urls if "_4x4" not in u and "logo" not in u.lower()]
                if clean_bgs:
                    raw_media = clean_bgs[0]

            if not raw_media:
                data_src_m = re.search(r'data-src="([^"]+)"', section_html)
                if data_src_m and "logo" not in data_src_m.group(1).lower():
                    raw_media = data_src_m.group(1)

            if not raw_media:
                src_m = re.search(r'src="([^"]+)"', section_html)
                if src_m and "logo" not in src_m.group(1).lower() and "icon" not in src_m.group(1).lower():
                    raw_media = src_m.group(1)

            # Awaryjna rekonstrukcja ze ścieżki daty i ID jeśli nadal brak lub logo
            if not raw_media or "logo" in raw_media.lower():
                thumb_date_m = re.search(r'thumbnails/(\d{4})/(\d{2})/(\d{2})', section_html)
                if thumb_date_m and video_id:
                    y, m, d = thumb_date_m.groups()
                    raw_media = f"https://cdn.freefile.io/thumbnails/{y}/{m}/{d}/{video_id}.jpg"

            if raw_media.startswith("//"):
                raw_media = "https:" + raw_media

            if raw_media.endswith(".mp4"):
                preview_video = raw_media
                poster_img = raw_media.replace(".mp4", ".jpg")
            elif raw_media.endswith(".jpg"):
                poster_img = raw_media
                preview_video = raw_media.replace(".jpg", ".mp4")
            else:
                poster_img = raw_media
                preview_video = raw_media

            # Czas trwania
            dur_m = re.search(r'<span class="[^"]*">([0-9:]+)</span>', section_html) or re.search(r'(\d+:\d+|\d+h \d+m)', section_html)
            duration = dur_m.group(1) if dur_m else "N/A"

            # Nazwa modelki / profilu
            user_m = re.search(r'href="https://archivebate\.com/profile/([^"]+)"', section_html) or re.search(r'/profile/([^"/\s]+)', section_html)
            username = user_m.group(1) if user_m else "Model"

            # Data dodania z HTML lub z miniatury
            p_m = re.search(r'<p>\s*([^&<]+?)\s*&middot;', section_html)
            p_date = p_m.group(1).strip() if p_m else ""

            thumb_date_m = re.search(r'thumbnails/(\d{4})/(\d{2})/(\d{2})', section_html)
            iso_date = ""
            if thumb_date_m:
                y, m, d = thumb_date_m.groups()
                iso_date = f"{d}.{m}.{y}"

            if p_date and re.search(r'\d', p_date) and not any(w in p_date.lower() for w in ['chaturbate', 'stripchat', 'camsoda', 'onlyfans']):
                if iso_date and iso_date not in p_date:
                    date = f"{p_date} • {iso_date}"
                else:
                    date = p_date
            elif iso_date:
                date = iso_date
            else:
                date = p_date or "Niedawno"

            views_m = re.search(r'(\d+\s*views)', section_html)
            views = views_m.group(1) if views_m else ""

            # Platforma
            platform_m = re.search(r'(Chaturbate|Stripchat|Camsoda|Cam4|Onlyfans|TikTok)', section_html, re.IGNORECASE)
            platform = platform_m.group(1) if platform_m else "Chaturbate"

            card_dict = {
                "id": video_id,
                "url": watch_url,
                "thumbnail": poster_img,
                "poster": poster_img,
                "preview_video": preview_video,
                "duration": duration,
                "username": username,
                "profile_url": f"https://archivebate.com/profile/{username}",
                "date": date,
                "views": views,
                "platform": platform
            }
            card_dict["tags"] = extract_video_tags(card_dict)
            return card_dict
        except Exception as e:
            logger.error(f"Błąd parsowania kafelka: {e}")
            return None

    def _sync_csrf(self, html: str, url: str):
        csrf_m = re.search(r'name="_token" value="([^"]+)"', html) or re.search(r'csrf-token" content="([^"]+)"', html)
        if csrf_m:
            self.session.csrf_token = csrf_m.group(1)
            self.session.session.headers.update({
                "X-CSRF-TOKEN": self.session.csrf_token,
                "Referer": url
            })

    def _fetch_single_ab_home_page(self, p: int, strict: bool = False) -> List[Dict[str, Any]]:
        """Pobiera pojedynczą stronę z Archivebate bez sztucznego limitu numeru strony."""
        if p < 1:
            return []
        url = f"https://archivebate.com?page={p}" if p > 1 else "https://archivebate.com"
        try:
            r = self.session.session.get(url, timeout=10)
            if strict: r.raise_for_status()
            html = r.text
            self._sync_csrf(html, url)

            m = re.search(r'wire:id="([^"]+)" wire:initial-data="([^"]+)" wire:init="([^"]+)"', html)
            if m:
                raw_data = m.group(2).replace('&quot;', '"')
                data = json.loads(raw_data)
                name = data['fingerprint']['name']
                method = m.group(3)
                rendered_html = self.session.call_livewire(name, data['fingerprint'], data['serverMemo'], method)
                if rendered_html:
                    sections = re.findall(r'<section class="video_item">.*?</section>', rendered_html, re.DOTALL)
                    parsed = []
                    for sec in sections:
                        video = self.parse_video_card(sec)
                        if video:
                            parsed.append(video)
                    return parsed
            sections = re.findall(r'<section class="video_item">.*?</section>', html, re.DOTALL)
            parsed = []
            for sec in sections:
                video = self.parse_video_card(sec)
                if video:
                    parsed.append(video)
            return parsed
        except Exception:
            if strict: raise
            return []

    def get_home_videos(
        self,
        page: int = 1,
        source: str = "all",
        author_filter: str = "all",
        blocked_models: Optional[set] = None,
        favorite_authors: Optional[set] = None,
        target_count: int = 280
    ) -> List[Dict[str, Any]]:
        """Pobiera kafelki wideo ze strony głównej z gwarancją stałej liczby 280 unikalnych nagrań z uwzględnieniem filtrów."""
        cache_key = f"home:{source}:{author_filter}:{page}"
        now = time.time()
        if not hasattr(self, "_home_cache"):
            self._home_cache = {}
        if cache_key in self._home_cache:
            entry = self._home_cache[cache_key]
            if now - entry["time"] < 60 and len(entry.get("data", [])) >= target_count:
                return entry["data"]

        from camwhores import camwhores_scraper, deduplicate_videos, merge_and_deduplicate

        if blocked_models is None:
            try:
                from storage import storage
                blocked_models = set(re.sub(r'[^a-z0-9]', '', b.lower()) for b in storage.get_blocked_models())
            except Exception:
                blocked_models = set()

        if favorite_authors is None:
            try:
                from storage import storage
                favorite_authors = set(re.sub(r'[^a-z0-9]', '', a.lower()) for a in storage.get_favorite_authors())
            except Exception:
                favorite_authors = set()

        def is_allowed_video(v: dict) -> bool:
            if not isinstance(v, dict):
                return False
            norm_u = re.sub(r'[^a-z0-9]', '', str(v.get("username", "")).lower())
            if norm_u and norm_u in blocked_models:
                return False
            if author_filter == "exclude_fav":
                if (norm_u and norm_u in favorite_authors) or v.get("is_favorite"):
                    return False
            elif author_filter == "only_fav":
                if not ((norm_u and norm_u in favorite_authors) or v.get("is_favorite")):
                    return False
            return True

        merged: List[Dict[str, Any]] = []

        if source == "only-camwhores":
            # Tylko Camwhores: pobieramy partiami strony CW
            cw_start = (page - 1) * 12 + 1
            cw_pages = list(range(cw_start, cw_start + 12))
            cw_videos = []
            with ThreadPoolExecutor(max_workers=12) as executor:
                futures = {executor.submit(camwhores_scraper.get_latest_videos, p): p for p in cw_pages}
                for f in as_completed(futures):
                    try:
                        cw_videos.extend(f.result() or [])
                    except Exception:
                        pass
            filtered = [v for v in deduplicate_videos(cw_videos) if is_allowed_video(v)]
            merged = filtered

            # Dociągamy kolejne strony Camwhores jeśli < target_count
            extra_p = cw_start + 12
            while len(merged) < target_count and extra_p <= cw_start + 25:
                batch = list(range(extra_p, extra_p + 3))
                extra_p += 3
                with ThreadPoolExecutor(max_workers=3) as executor:
                    batch_vids = []
                    for vlist in executor.map(camwhores_scraper.get_latest_videos, batch):
                        batch_vids.extend(vlist or [])
                    cw_videos.extend(batch_vids)
                    merged = [v for v in deduplicate_videos(cw_videos) if is_allowed_video(v)]

        elif source == "only-archivebate":
            # Tylko Archivebate: pobieramy partiami strony AB
            ab_start = (page - 1) * 20 + 1
            ab_pages = list(range(ab_start, ab_start + 20))
            ab_videos = []
            with ThreadPoolExecutor(max_workers=16) as executor:
                futures = {executor.submit(self._fetch_single_ab_home_page, p): p for p in ab_pages}
                for f in as_completed(futures):
                    try:
                        ab_videos.extend(f.result() or [])
                    except Exception:
                        pass
            filtered = [v for v in deduplicate_videos(ab_videos) if is_allowed_video(v)]
            merged = filtered

            # Dociągamy kolejne strony Archivebate jeśli < target_count
            extra_p = ab_start + 20
            while len(merged) < target_count and extra_p <= ab_start + 40:
                batch = list(range(extra_p, extra_p + 5))
                extra_p += 5
                if not batch:
                    break
                with ThreadPoolExecutor(max_workers=5) as executor:
                    for vlist in executor.map(self._fetch_single_ab_home_page, batch):
                        ab_videos.extend(vlist or [])
                    merged = [v for v in deduplicate_videos(ab_videos) if is_allowed_video(v)]

        else:
            # "all": Archivebate + Camwhores równolegle
            ab_start = (page - 1) * 10 + 1
            cw_start = (page - 1) * 6 + 1
            ab_pages = list(range(ab_start, ab_start + 10))
            cw_pages = list(range(cw_start, cw_start + 6))

            ab_videos = []
            cw_videos = []

            with ThreadPoolExecutor(max_workers=16) as executor:
                ab_futures = {executor.submit(self._fetch_single_ab_home_page, p): p for p in ab_pages}
                cw_futures = {executor.submit(camwhores_scraper.get_latest_videos, p): p for p in cw_pages}

                for future in as_completed(list(ab_futures) + list(cw_futures)):
                    try:
                        vids = future.result() or []
                    except Exception:
                        continue
                    if future in ab_futures:
                        ab_videos.extend(vids)
                    else:
                        cw_videos.extend(vids)

            merged_raw = merge_and_deduplicate(ab_videos, cw_videos)
            merged = [v for v in merged_raw if is_allowed_video(v)]

            # Dociągamy kolejne strony jeśli < target_count
            extra_offset = 0
            while len(merged) < target_count and extra_offset < 10:
                extra_ab = [ab_start + 10 + extra_offset * 3 + i for i in range(3)]
                extra_cw = [cw_start + 6 + extra_offset * 2 + i for i in range(2)]
                extra_offset += 1

                with ThreadPoolExecutor(max_workers=5) as executor:
                    for vlist in executor.map(self._fetch_single_ab_home_page, extra_ab):
                        ab_videos.extend(vlist or [])
                    for vlist in executor.map(camwhores_scraper.get_latest_videos, extra_cw):
                        cw_videos.extend(vlist or [])

                merged_raw = merge_and_deduplicate(ab_videos, cw_videos)
                merged = [v for v in merged_raw if is_allowed_video(v)]

        result = sort_videos_newest_first(merged)
        if result:
            self._home_cache[cache_key] = {"data": result, "time": now}
        return result

    def get_model_videos(self, username: str, page: int = 1) -> List[Dict[str, Any]]:
        """Pobiera filmy konkretnej modelki (zoptymalizowane, z pamięcią podręczną)."""
        if not username or not isinstance(username, str):
            return []
        clean_user = username.strip()
        if clean_user.lower() in ("model", "unknown", "null", "undefined", "none") or len(clean_user) < 2 or len(clean_user) > 50:
            return []
        if not re.match(r'^[a-zA-Z0-9_\-\.]+$', clean_user):
            return []

        cache_key = f"model:{clean_user}:{page}"
        now = time.time()
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if now - entry["time"] < 300:
                return entry["data"]

        all_videos = []

        def _fetch_page(p):
            url = f"https://archivebate.com/profile/{clean_user}?page={p}"
            try:
                r = self.session.session.get(url, timeout=(2.5, 4.0))
                if r.status_code >= 400:
                    return []
                html = r.text
                self._sync_csrf(html, url)
                for m in re.finditer(r'wire:id="([^"]+)" wire:initial-data="([^"]+)"', html):
                    raw_data = m.group(2).replace('&quot;', '"')
                    data = json.loads(raw_data)
                    name = data['fingerprint']['name']
                    if 'model-videos' in name or 'profile' in name:
                        rendered_html = self.session.call_livewire(name, data['fingerprint'], data['serverMemo'], "load_profile_videos")
                        if rendered_html:
                            sections = re.findall(r'<section class="video_item">.*?</section>', rendered_html, re.DOTALL)
                            vids = []
                            for sec in sections:
                                v = self.parse_video_card(sec)
                                if v:
                                    if v["username"] == "Model":
                                        v["username"] = clean_user
                                    vids.append(v)
                            return vids
                sections = re.findall(r'<section class="video_item">.*?</section>', html, re.DOTALL)
                return [self.parse_video_card(s) for s in sections if self.parse_video_card(s)]
            except Exception:
                return []

        # Równolegle: strona Archivebate + filmy Camwhores
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_ab = executor.submit(_fetch_page, page)
            from camwhores import camwhores_scraper, merge_and_deduplicate
            f_cw = executor.submit(camwhores_scraper.search_videos, username)

            try: all_videos.extend(f_ab.result(timeout=5))
            except Exception: pass
            try:
                cw_vids = f_cw.result(timeout=4)
                cw_model_vids = [v for v in cw_vids if v.get("username", "").lower() == username.lower()]
                all_videos = merge_and_deduplicate(all_videos, cw_model_vids)
            except Exception: pass

        sorted_vids = sort_videos_newest_first(all_videos)
        self._cache[cache_key] = {"data": sorted_vids, "time": now}
        return sorted_vids


    def get_archivebate_model_videos(self, username: str, page: int = 1) -> List[Dict[str, Any]]:
        """Pobiera filmy konkretnej modelki wyłącznie z Archivebate (szybkie, bez zbędnego Camwhores)."""
        cache_key = f"ab_model:{username}:{page}"
        now = time.time()
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if now - entry["time"] < 300:
                return entry["data"]

        url = f"https://archivebate.com/profile/{username}?page={page}"
        try:
            r = self.session.session.get(url, timeout=5)
            html = r.text
            self._sync_csrf(html, url)
            for m in re.finditer(r'wire:id="([^"]+)" wire:initial-data="([^"]+)"', html):
                raw_data = m.group(2).replace('&quot;', '"')
                data = json.loads(raw_data)
                name = data['fingerprint']['name']
                if 'model-videos' in name or 'profile' in name:
                    rendered_html = self.session.call_livewire(name, data['fingerprint'], data['serverMemo'], "load_profile_videos")
                    if rendered_html:
                        sections = re.findall(r'<section class="video_item">.*?</section>', rendered_html, re.DOTALL)
                        vids = []
                        for sec in sections:
                            v = self.parse_video_card(sec)
                            if v:
                                if v["username"] == "Model":
                                    v["username"] = username
                                vids.append(v)
                        self._cache[cache_key] = {"data": vids, "time": now}
                        return vids
            sections = re.findall(r'<section class="video_item">.*?</section>', html, re.DOTALL)
            vids = [self.parse_video_card(s) for s in sections if self.parse_video_card(s)]
            self._cache[cache_key] = {"data": vids, "time": now}
            return vids
        except Exception:
            return []

    def _fetch_search_profiles(self, clean_q: str, page: int = 1) -> Tuple[List[Dict[str, Any]], int, int]:
        """Szybkie pobieranie pasujących profili bezpośrednim zapytaniem z obsługą stron i metadanych.
        Zwraca: (profiles, total_models, last_page)
        """
        if not self.session.csrf_token:
            self.session.refresh_csrf()

        headers = {
            "X-CSRF-TOKEN": self.session.csrf_token or "",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://archivebate.com/"
        }

        profiles = []
        seen_users = set()
        total_models = 0
        last_page = 1
        try:
            r = self.session.session.get(
                f"https://archivebate.com/api/v1/search?query={clean_q}&page={page}",
                headers=headers,
                timeout=5
            )
            if r.status_code == 200:
                resp_json = r.json()
                meta = resp_json.get("meta", {})
                total_models = meta.get("total", 0)
                last_page = meta.get("last_page", 1)
                for p in resp_json.get("data", []):
                    u = p.get("username")
                    if u and u not in seen_users:
                        seen_users.add(u)
                        profiles.append(p)
        except Exception:
            pass

        # Jeśli dla page 1 profili jest mało (< 12), sprawdzamy warianty ze spacją i myślnikiem
        if page == 1 and len(profiles) < 12:
            extra_chars = [' ', '-', '_']
            for c in extra_chars:
                try:
                    r2 = self.session.session.get(
                        f"https://archivebate.com/api/v1/search?query={clean_q}{c}&page=1",
                        headers=headers,
                        timeout=3
                    )
                    if r2.status_code == 200:
                        for p in r2.json().get("data", []):
                            u = p.get("username")
                            if u and u not in seen_users:
                                seen_users.add(u)
                                profiles.append(p)
                except Exception:
                    pass

        if total_models == 0:
            total_models = len(profiles)

        return profiles, total_models, last_page

    def _estimate_search_meta(self, query: str, clean_q: str, total_ab_models: int, source: str = "all") -> Tuple[int, int]:
        """Szacuje całkowitą liczbę filmów oraz stron dla danego zapytania z uwzględnieniem wybranego źródła."""
        from camwhores import camwhores_scraper
        cw_max_p = camwhores_scraper.get_query_last_page(query) if source != "only-archivebate" else 0
        clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '-', clean_q.lower())
        known_tags = {t.get("tag", "").lower() for t in POPULAR_TAGS}
        is_tag = query.startswith("#") or clean_slug in known_tags or clean_q.lower() in known_tags

        if source == "only-camwhores":
            if is_tag:
                est_total = max(cw_max_p * 24, 2500)
            else:
                est_total = max(cw_max_p * 35, 35) if cw_max_p > 0 else 0
        elif source == "only-archivebate":
            if is_tag:
                est_total = max(total_ab_models * 15, 600) if total_ab_models > 0 else 300
            else:
                est_total = max(total_ab_models * 15, 15) if total_ab_models > 0 else 0
        else:
            if is_tag:
                cw_estimated = max(cw_max_p * 24, 2500)
                ab_estimated = max(total_ab_models * 15, 600) if total_ab_models > 0 else 0
                est_total = min(75000, cw_estimated + ab_estimated)
            else:
                cw_estimated = max(cw_max_p * 35, 35) if cw_max_p > 0 else 0
                ab_estimated = max(total_ab_models * 15, 15) if total_ab_models > 0 else 0
                est_total = max(280, cw_estimated + ab_estimated) if (total_ab_models > 5 or cw_max_p > 5) else max(len(POPULAR_TAGS), cw_estimated + ab_estimated)

        est_last_page = max(1, math.ceil(est_total / 280))
        return est_total, est_last_page

    def search_query(self, query: str, page: int = 1, per_page: int = 280, source: str = "all", author_filter: str = "all", group_authors: str = "0") -> Dict[str, Any]:
        """Wyszukiwanie tagów i profili z pełnym podziałem na strony (stałe 280 filmów na stronę) oraz filtrami źródła i autorów."""
        clean_q = query.replace("#", "").strip()
        if not clean_q:
            return {"query": query, "page": 1, "last_page": 1, "total_profiles": 0, "total_videos": 0, "profiles": [], "videos": []}

        grouped_search = group_authors in (True, "1", "true", "True")
        grouped_archivebate_page_size = 40
        cache_key = f"search:{clean_q.lower()}:{source}:{author_filter}:g{group_authors}:p{page}"
        meta_cache_key = f"search_meta:{clean_q.lower()}:{source}:{author_filter}:g{group_authors}"
        now = time.time()

        # Sprawdzenie pamięci podręcznej strony
        if cache_key in self._cache and (now - self._cache[cache_key]["time"] < 600) and meta_cache_key in self._cache:
            cached = self._cache[cache_key]
            meta = self._cache[meta_cache_key]
            return {
                "query": query,
                "page": page,
                "last_page": meta["last_page"],
                "total_profiles": meta["total_profiles"],
                "total_videos": meta["total_videos"],
                "profiles": meta["profiles"][:30],
                "videos": cached["videos"] if grouped_search else cached["videos"][:per_page]
            }

        fav_authors_raw = storage.get_favorite_authors()
        fav_authors_clean = set(re.sub(r'[^a-z0-9]', '', a.lower()) for a in fav_authors_raw)
        favorite_ids = {str(item.get("id")) for item in storage.data.get("favorites", []) if isinstance(item, dict)}
        blocked_set = set(re.sub(r'[^a-z0-9]', '', b.lower()) for b in storage.get_blocked_models())

        def is_allowed_video(v: dict) -> bool:
            if not isinstance(v, dict):
                return False
            u = str(v.get("username", "")).lower()
            norm_u = re.sub(r'[^a-z0-9]', '', u)
            if norm_u and norm_u in blocked_set:
                return False
            vid = str(v.get("id", ""))
            v_src = v.get("source", "")
            is_cw = v_src == "camwhores" or vid.startswith("cw_") or "camwhores" in str(v.get("platform", "")).lower()
            if source == "only-camwhores" and not is_cw:
                return False
            if source == "only-archivebate" and is_cw:
                return False
            is_fav = (norm_u and norm_u in fav_authors_clean) or (vid and vid in favorite_ids) or bool(v.get("is_favorite"))
            if author_filter == "exclude_fav" and is_fav:
                return False
            if author_filter == "only_fav" and not is_fav:
                return False
            return True

        # Pobranie lub odczyt metadanych
        if meta_cache_key in self._cache and (now - self._cache[meta_cache_key]["time"] < 600):
            meta = self._cache[meta_cache_key]
            all_profiles = meta["profiles"]
            total_models = meta["total_profiles"]
            est_total = meta["total_videos"]
            est_last_page = meta["last_page"]
        else:
            all_profiles, total_models, _ = self._fetch_search_profiles(clean_q, page=1)
            try:
                from model_tags import model_tag_manager
                known_models = model_tag_manager.get_models_with_tag(clean_q)
                for km in reversed(known_models):
                    if km.lower() not in ["model"]:
                        if not any(p.get("username", "").lower() == km.lower() for p in all_profiles):
                            all_profiles.insert(0, {"platform": "Chaturbate", "username": km, "gender": clean_q.capitalize()})
            except Exception:
                pass
            all_profiles = [p for p in all_profiles if not storage.is_model_blocked(p.get("username"))]

            if author_filter == "exclude_fav":
                all_profiles = [p for p in all_profiles if re.sub(r'[^a-z0-9]', '', str(p.get("username", "")).lower()) not in fav_authors_clean]
            elif author_filter == "only_fav":
                all_profiles = [p for p in all_profiles if re.sub(r'[^a-z0-9]', '', str(p.get("username", "")).lower()) in fav_authors_clean]

            est_total, est_last_page = self._estimate_search_meta(query, clean_q, total_models, source=source)
            if author_filter == "only_fav" and fav_authors_clean:
                est_total = min(est_total, max(len(fav_authors_clean) * 35, 35))
                est_last_page = max(1, math.ceil(est_total / 280))
            if grouped_search and source == "only-archivebate":
                # W widoku 1/autora stroną jest porcja autorów, nie sztuczna estymacja filmów.
                est_last_page = max(1, math.ceil(len(all_profiles) / grouped_archivebate_page_size))

            meta = {
                "profiles": all_profiles,
                "total_profiles": len(all_profiles) if author_filter != "all" else (total_models or len(all_profiles)),
                "total_videos": est_total,
                "last_page": est_last_page,
                "time": now
            }
            self._cache[meta_cache_key] = meta

        # Pobieranie filmów dla danej strony
        from camwhores import camwhores_scraper, deduplicate_videos, merge_and_deduplicate

        clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '-', clean_q.lower())
        known_tags = {t.get("tag", "").lower() for t in POPULAR_TAGS}
        is_tag = query.startswith("#") or clean_slug in known_tags or clean_q.lower() in known_tags

        cw_videos = []
        ab_videos = []
        seen_ids = set()

        # Pobranie partii Camwhores odpowiadającej stronie (jeśli dozwolone przez source)
        if source != "only-archivebate":
            if is_tag:
                cw_count_pages = 12 if source == "only-camwhores" else 8
                tag_start = (page - 1) * cw_count_pages + 1
                search_start = (page - 1) * 2 + 1
                with ThreadPoolExecutor(max_workers=12) as executor:
                    f_tag = [executor.submit(camwhores_scraper.search_videos, '#' + clean_q, p) for p in range(tag_start, tag_start + cw_count_pages)]
                    f_srch = [executor.submit(camwhores_scraper.search_videos, clean_q, p) for p in [search_start, search_start + 1]]
                    for f in f_tag + f_srch:
                        try:
                            for v in (f.result() or []):
                                vid = str(v.get("id"))
                                if vid and vid not in seen_ids and is_allowed_video(v):
                                    seen_ids.add(vid)
                                    cw_videos.append(v)
                        except Exception:
                            pass
            else:
                cw_count_pages = 8 if source == "only-camwhores" else 4
                search_start = (page - 1) * cw_count_pages + 1
                cw_vids = camwhores_scraper.search_videos_multi(clean_q, list(range(search_start, search_start + cw_count_pages)))
                for v in cw_vids:
                    vid = str(v.get("id"))
                    if vid and vid not in seen_ids and is_allowed_video(v):
                        seen_ids.add(vid)
                        cw_videos.append(v)

        # Pobranie modelek Archivebate odpowiadających stronie (jeśli dozwolone przez source)
        if source != "only-camwhores":
            models_for_page = []
            ab_model_batch = grouped_archivebate_page_size if (grouped_search and source == "only-archivebate") else (16 if source == "only-archivebate" else 8)
            if page == 1:
                models_for_page = all_profiles[:ab_model_batch]
            else:
                start_m = (page - 1) * ab_model_batch
                end_m = start_m + ab_model_batch
                if start_m < len(all_profiles):
                    # Ostatnia częściowa strona ma użyć pozostałych profili zamiast
                    # ponownie wpadać w zdalną stronę / fallback strony pierwszej.
                    models_for_page = all_profiles[start_m:end_m]
                else:
                    p_models, _, _ = self._fetch_search_profiles(clean_q, page=page)
                    p_models = [p for p in p_models if not storage.is_model_blocked(p.get("username"))]
                    if author_filter == "exclude_fav":
                        p_models = [p for p in p_models if re.sub(r'[^a-z0-9]', '', str(p.get("username", "")).lower()) not in fav_authors_clean]
                    elif author_filter == "only_fav":
                        p_models = [p for p in p_models if re.sub(r'[^a-z0-9]', '', str(p.get("username", "")).lower()) in fav_authors_clean]
                    # Brak danych dla strony N oznacza brak tej strony; nigdy nie
                    # wracaj do all_profiles[:N], bo to duplikuje stronę 1.
                    models_for_page = p_models[:ab_model_batch]

            if models_for_page:
                with ThreadPoolExecutor(max_workers=min(16, max(1, len(models_for_page)))) as executor:
                    futs = [executor.submit(self.get_archivebate_model_videos, p.get("username"), 1) for p in models_for_page if p.get("username")]
                    for f in as_completed(futs):
                        try:
                            for v in (f.result() or []):
                                vid = str(v.get("id"))
                                if vid and vid not in seen_ids and is_allowed_video(v):
                                    seen_ids.add(vid)
                                    ab_videos.append(v)
                        except Exception:
                            pass

        combined = merge_and_deduplicate(cw_videos, ab_videos)
        filtered = [v for v in combined if is_allowed_video(v)]

        # Dociąganie kolejnych stron Camwhores, jeśli połączone wyniki mają < per_page i źródło nie wyklucza CW
        if len(filtered) < per_page and is_tag and source != "only-archivebate":
            cw_step = 12 if source == "only-camwhores" else 8
            extra_start = (page - 1) * cw_step + cw_step + 1
            extra_cw = camwhores_scraper.search_videos_multi('#' + clean_q, list(range(extra_start, extra_start + 6)))
            for v in extra_cw:
                vid = str(v.get("id"))
                if vid and vid not in seen_ids and is_allowed_video(v):
                    seen_ids.add(vid)
                    filtered.append(v)

        sorted_vids = sort_videos_newest_first(filtered)
        final_videos = sorted_vids if grouped_search else sorted_vids[:per_page]

        self._cache[cache_key] = {
            "videos": final_videos,
            "time": now
        }

        return {
            "query": query,
            "page": page,
            "last_page": meta["last_page"],
            "total_profiles": meta["total_profiles"],
            "total_videos": meta["total_videos"],
            "profiles": meta["profiles"][:30],
            "videos": final_videos
        }

    def search_query_stream(self, query: str, source: str = "all", author_filter: str = "all", group_authors: str = "0"):
        """Generator strumieniowy SSE: pobiera wideo z uwzględnieniem filtrów source i author_filter, sortuje globalnie i wysyła progresywnie."""
        clean_q = query.replace("#", "").strip()
        if not clean_q:
            yield {"type": "done", "total_videos": 0, "total_profiles": 0, "last_page": 1}
            return

        grouped_search = group_authors in (True, "1", "true", "True")
        grouped_archivebate_page_size = 40
        cache_key = f"search:{clean_q.lower()}:{source}:{author_filter}:g{group_authors}:p1"
        meta_cache_key = f"search_meta:{clean_q.lower()}:{source}:{author_filter}:g{group_authors}"
        now = time.time()

        # Jeśli wynik strony 1 jest w pamięci RAM, zwróć go natychmiast
        if cache_key in self._cache and (now - self._cache[cache_key]["time"] < 600) and meta_cache_key in self._cache:
            cached = self._cache[cache_key]
            meta = self._cache[meta_cache_key]
            cached_page_videos = cached["videos"] if grouped_search else cached["videos"][:280]
            yield {
                "type": "profiles",
                "profiles": meta["profiles"][:30],
                "total_profiles": meta["total_profiles"],
                "estimated_total_videos": meta["total_videos"],
                "last_page": meta["last_page"]
            }
            yield {
                "type": "videos",
                "videos": cached_page_videos,
                "total_so_far": len(cached_page_videos)
            }
            yield {
                "type": "done",
                "total_videos": meta["total_videos"],
                "total_profiles": meta["total_profiles"],
                "last_page": meta["last_page"],
                "all_sorted_videos": cached_page_videos
            }
            return

        fav_authors_raw = storage.get_favorite_authors()
        fav_authors_clean = set(re.sub(r'[^a-z0-9]', '', a.lower()) for a in fav_authors_raw)
        favorite_ids = {str(item.get("id")) for item in storage.data.get("favorites", []) if isinstance(item, dict)}
        blocked_set = set(re.sub(r'[^a-z0-9]', '', b.lower()) for b in storage.get_blocked_models())

        def is_allowed_video(v: dict) -> bool:
            if not isinstance(v, dict):
                return False
            u = str(v.get("username", "")).lower()
            norm_u = re.sub(r'[^a-z0-9]', '', u)
            if norm_u and norm_u in blocked_set:
                return False
            vid = str(v.get("id", ""))
            v_src = v.get("source", "")
            is_cw = v_src == "camwhores" or vid.startswith("cw_") or "camwhores" in str(v.get("platform", "")).lower()
            if source == "only-camwhores" and not is_cw:
                return False
            if source == "only-archivebate" and is_cw:
                return False
            is_fav = (norm_u and norm_u in fav_authors_clean) or (vid and vid in favorite_ids) or bool(v.get("is_favorite"))
            if author_filter == "exclude_fav" and is_fav:
                return False
            if author_filter == "only_fav" and not is_fav:
                return False
            return True

        # 1. Szybkie pobranie profili oraz estymacja całkowitych statystyk
        all_profiles, total_models, last_model_page = self._fetch_search_profiles(clean_q, page=1)
        try:
            from model_tags import model_tag_manager
            known_models = model_tag_manager.get_models_with_tag(clean_q)
            for km in reversed(known_models):
                if km.lower() not in ["model"]:
                    if not any(p.get("username", "").lower() == km.lower() for p in all_profiles):
                        all_profiles.insert(0, {"platform": "Chaturbate", "username": km, "gender": clean_q.capitalize()})
        except Exception:
            pass

        # Filtr zablokowanych profili oraz autorów
        all_profiles = [p for p in all_profiles if not storage.is_model_blocked(p.get("username"))]
        if author_filter == "exclude_fav":
            all_profiles = [p for p in all_profiles if re.sub(r'[^a-z0-9]', '', str(p.get("username", "")).lower()) not in fav_authors_clean]
        elif author_filter == "only_fav":
            all_profiles = [p for p in all_profiles if re.sub(r'[^a-z0-9]', '', str(p.get("username", "")).lower()) in fav_authors_clean]

        est_total, est_last_page = self._estimate_search_meta(query, clean_q, total_models, source=source)
        if author_filter == "only_fav" and fav_authors_clean:
            est_total = min(est_total, max(len(fav_authors_clean) * 35, 35))
            est_last_page = max(1, math.ceil(est_total / 280))
        if grouped_search and source == "only-archivebate":
            est_last_page = max(1, math.ceil(len(all_profiles) / grouped_archivebate_page_size))

        # Emitujemy profile natychmiast (0.1 - 0.2s) wraz z oszacowaną liczbą filmów i stron!
        yield {
            "type": "profiles",
            "profiles": all_profiles[:30],
            "total_profiles": len(all_profiles) if author_filter != "all" else (total_models or len(all_profiles)),
            "estimated_total_videos": est_total,
            "last_page": est_last_page
        }

        # 2. Wideo z pamięci lokalnej użytkownika (błyskawiczne z bazy, 0.05s)
        seen_v_ids = set()
        streamed_ids = set()
        all_videos = []

        try:
            stored = storage.search_stored_videos(clean_q)
            for sv in stored:
                sv_id = str(sv.get("id"))
                if sv_id and sv_id not in seen_v_ids and is_allowed_video(sv):
                    seen_v_ids.add(sv_id)
                    all_videos.append(sv)
            if all_videos:
                for v in all_videos:
                    streamed_ids.add(v["id"])
                yield {
                    "type": "videos",
                    "videos": all_videos,
                    "total_so_far": len(streamed_ids)
                }
        except Exception:
            pass

        # 3. Progresywne pobieranie partii: Camwhores + Archivebate dla strony 1
        from camwhores import camwhores_scraper, deduplicate_videos, merge_and_deduplicate

        clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '-', clean_q.lower())
        known_tags = {t.get("tag", "").lower() for t in POPULAR_TAGS}
        is_tag = query.startswith("#") or clean_slug in known_tags or clean_q.lower() in known_tags

        # Partia 1: CW tag 1..3 (lub CW search 1) - tylko jeśli dozwolone
        if source != "only-archivebate":
            cw_p1 = []
            try:
                if is_tag:
                    batch_1_pages = [1, 2, 3, 4] if source == "only-camwhores" else [1, 2, 3]
                    cw_p1 = camwhores_scraper.search_videos_multi('#' + clean_q, batch_1_pages)
                else:
                    cw_p1 = camwhores_scraper.search_videos(clean_q, 1)

                new_batch1 = []
                for v in cw_p1:
                    vid = str(v.get("id"))
                    if vid and vid not in seen_v_ids and is_allowed_video(v):
                        seen_v_ids.add(vid)
                        all_videos.append(v)
                        new_batch1.append(v)

                if new_batch1:
                    for v in new_batch1:
                        streamed_ids.add(v["id"])
                    yield {
                        "type": "videos",
                        "videos": sort_videos_newest_first(new_batch1),
                        "total_so_far": len(streamed_ids)
                    }
            except Exception:
                pass

        # Partia 2: CW tag 4..6 + CW search 1 oraz Archivebate modelek (współbieżnie)
        target_profile_count = grouped_archivebate_page_size if (grouped_search and source == "only-archivebate") else (16 if source == "only-archivebate" else 8)
        target_profiles = all_profiles[:target_profile_count]
        cw_extra = []

        with ThreadPoolExecutor(max_workers=12) as executor:
            cw_futs = []
            if source != "only-archivebate":
                if is_tag:
                    cw_pages_b2 = [5, 6, 7, 8] if source == "only-camwhores" else [4, 5, 6]
                    cw_futs.append(executor.submit(camwhores_scraper.search_videos_multi, '#' + clean_q, cw_pages_b2))
                    cw_futs.append(executor.submit(camwhores_scraper.search_videos, clean_q, 1))
                else:
                    cw_pages_b2 = [2, 3, 4] if source == "only-camwhores" else [2, 3]
                    cw_futs.append(executor.submit(camwhores_scraper.search_videos_multi, clean_q, cw_pages_b2))

            ab_futs = {}
            if source != "only-camwhores":
                ab_futs = {executor.submit(self.get_archivebate_model_videos, p.get("username"), 1): p for p in target_profiles if p.get("username")}

            # Zbieramy CW
            for f in cw_futs:
                try:
                    for v in (f.result() or []):
                        cw_extra.append(v)
                except Exception:
                    pass

            # Wysyłamy natychmiast kolejną partię z CW (~0.9s)
            new_batch2 = []
            for v in cw_extra:
                vid = str(v.get("id"))
                if vid and vid not in seen_v_ids and is_allowed_video(v):
                    seen_v_ids.add(vid)
                    all_videos.append(v)
                    new_batch2.append(v)

            if new_batch2:
                for v in new_batch2:
                    streamed_ids.add(v["id"])
                yield {
                    "type": "videos",
                    "videos": sort_videos_newest_first(new_batch2),
                    "total_so_far": len(streamed_ids)
                }

            # Zbieramy wideo Archivebate
            for f in as_completed(ab_futs):
                try:
                    vlist = f.result() or []
                    new_prof_vids = []
                    for v in vlist:
                        vid = str(v.get("id"))
                        if vid and vid not in seen_v_ids and is_allowed_video(v):
                            seen_v_ids.add(vid)
                            all_videos.append(v)
                            new_prof_vids.append(v)
                    if new_prof_vids:
                        for v in new_prof_vids:
                            streamed_ids.add(v["id"])
                        yield {
                            "type": "videos",
                            "videos": sort_videos_newest_first(new_prof_vids),
                            "total_so_far": len(streamed_ids)
                        }
                except Exception:
                    pass

        # Partia 3: Jeśli nadal < 280 filmów i źródło nie wyklucza CW, dociągamy kolejne strony CW tag
        if len(all_videos) < 280 and is_tag and source != "only-archivebate":
            try:
                cw_pages_b3 = [9, 10, 11, 12] if source == "only-camwhores" else [7, 8, 9, 10]
                cw_more = camwhores_scraper.search_videos_multi('#' + clean_q, cw_pages_b3)
                new_more = []
                for v in cw_more:
                    vid = str(v.get("id"))
                    if vid and vid not in seen_v_ids and is_allowed_video(v):
                        seen_v_ids.add(vid)
                        all_videos.append(v)
                        new_more.append(v)
                if new_more:
                    for v in new_more:
                        streamed_ids.add(v["id"])
                    yield {
                        "type": "videos",
                        "videos": sort_videos_newest_first(new_more),
                        "total_so_far": len(streamed_ids)
                    }
            except Exception:
                pass

        # 4. Finalne sortowanie całości od najnowszego i zapisanie w pamięci RAM
        sorted_all = sort_videos_newest_first(all_videos)
        page_1_vids = sorted_all if grouped_search else sorted_all[:280]

        self._cache[cache_key] = {
            "videos": page_1_vids,
            "time": time.time()
        }
        self._cache[meta_cache_key] = {
            "profiles": all_profiles,
            "total_profiles": len(all_profiles) if author_filter != "all" else (total_models or len(all_profiles)),
            "total_videos": max(len(page_1_vids), est_total),
            "last_page": est_last_page,
            "time": time.time()
        }

        yield {
            "type": "done",
            "total_videos": max(len(page_1_vids), est_total),
            "total_profiles": len(all_profiles) if author_filter != "all" else (total_models or len(all_profiles)),
            "last_page": est_last_page,
            "all_sorted_videos": page_1_vids
        }

    def get_video_details(self, video_id_or_url: str) -> Dict[str, Any]:
        """Pobiera pełne detale wideo, w tym bezpośredni link do strumienia MP4 bez reklam."""
        if str(video_id_or_url).startswith("cw_") or "camwhores.tv" in str(video_id_or_url):
            try:
                from camwhores import camwhores_scraper
                return camwhores_scraper.get_video_details(video_id_or_url)
            except Exception as e:
                logger.error(f"Błąd pobierania detali Camwhores ({video_id_or_url}): {e}")
                return {"url": video_id_or_url, "direct_url": "", "embed_url": video_id_or_url, "source": "camwhores"}

        clean_id = video_id_or_url.split("/")[-1].split("?")[0]
        now = time.time()
        if not hasattr(self, "_details_cache"):
            self._details_cache = {}
        if clean_id in self._details_cache:
            entry = self._details_cache[clean_id]
            if now - entry["time"] < 3600 and entry.get("data", {}).get("direct_url"):
                return entry["data"]

        if video_id_or_url.startswith("http"):
            url = video_id_or_url
        else:
            url = f"https://archivebate.com/watch/{clean_id}"

        try:
            r = self.session.session.get(url, timeout=12)
            html = r.text

            # Mixdrop iframe
            iframe_m = re.search(r'<iframe[^>]*src="([^"]+)"', html)
            embed_url = iframe_m.group(1) if iframe_m else ""

            # Download fid
            fid_m = re.search(r'name="fid"\s+value="([^"]+)"', html)
            fid_url = fid_m.group(1) if fid_m else embed_url.replace("/e/", "/f/")

            # Thumbnail poster & preview video
            thumb_m = re.search(r'name="t"\s+value="([^"]+)"', html) or re.search(r'property="og:image"\s+content="([^"]+)"', html)
            raw_thumb = thumb_m.group(1) if thumb_m else ""
            if raw_thumb.startswith("//"):
                raw_thumb = "https:" + raw_thumb

            thumbnail = raw_thumb
            preview_video = ""
            if raw_thumb:
                if raw_thumb.endswith(".mp4"):
                    preview_video = raw_thumb
                    thumbnail = raw_thumb.replace(".mp4", ".jpg")
                elif raw_thumb.endswith(".jpg"):
                    thumbnail = raw_thumb
                    preview_video = raw_thumb.replace(".jpg", ".mp4")
                else:
                    thumbnail = raw_thumb
                    preview_video = raw_thumb

            # Model
            model_m = re.search(r'href="https://archivebate\.com/profile/([^"]+)"', html)
            username = model_m.group(1) if model_m else "Model"

            # Keywords
            kw_m = re.search(r'name="keywords"\s+content="([^"]+)"', html)
            keywords = [k.strip() for k in kw_m.group(1).split(",")] if kw_m else []

            # Description
            desc_m = re.search(r'name="description"\s+content="([^"]+)"', html)
            description = desc_m.group(1) if desc_m else ""

            # Data
            date_m = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}|\d{2}\.\d{2}\.\d{2})', html)
            date = date_m.group(1) if date_m else ""

            # Wyciągamy bezpośredni strumień MP4 z Mixdrop (bez reklam)
            direct_mp4_url = ""
            if embed_url:
                try:
                    mixdrop_res = self.session.session.get(embed_url, timeout=10)
                    direct_mp4_url = unpack_mixdrop(mixdrop_res.text) or ""
                except Exception as e:
                    logger.error(f"Błąd pobierania direct stream z Mixdrop: {e}")

            result = {
                "url": url,
                "embed_url": embed_url,
                "direct_url": direct_mp4_url,
                "download_url": fid_url,
                "thumbnail": thumbnail,
                "preview_video": preview_video,
                "username": username,
                "date": date,
                "keywords": keywords,
                "description": description
            }
            result["tags"] = extract_video_tags(result)
            if clean_id and direct_mp4_url:
                self._details_cache[clean_id] = {"data": result, "time": now}
            return result
        except Exception as e:
            logger.error(f"Błąd pobierania detali wideo {video_id_or_url}: {e}")
            return {
                "url": url,
                "embed_url": "",
                "direct_url": "",
                "download_url": "",
                "thumbnail": "",
                "username": "",
                "date": "",
                "keywords": [],
                "description": ""
            }

    def get_account_section_videos(self, endpoint: str, max_pages: int = 12, strict: bool = False) -> List[Dict[str, Any]]:
        """Pobiera listę sekcji konta; w trybie strict nie zamienia awarii na pustą listę."""
        if not self.session.is_logged_in:
            logged = self.session.login()
            if not logged:
                if strict:
                    raise RuntimeError("account_auth_failed")
                return []

        def fetch_page(p: int):
            url = f"https://archivebate.com/{endpoint}?page={p}" if p > 1 else f"https://archivebate.com/{endpoint}"
            try:
                r = self.session.session.get(url, timeout=12)
                if hasattr(r, "raise_for_status"):
                    r.raise_for_status()
                if "login" in str(getattr(r, "url", "")):
                    if not self.session.login():
                        raise RuntimeError("account_auth_failed")
                    r = self.session.session.get(url, timeout=12)
                    if hasattr(r, "raise_for_status"):
                        r.raise_for_status()
                    if "login" in str(getattr(r, "url", "")):
                        raise RuntimeError("account_auth_redirect")
                sections = re.findall(r'<section class="video_item">.*?</section>', r.text, re.DOTALL)
                result = []
                for section in sections:
                    parsed = self.parse_video_card(section)
                    if parsed:
                        result.append(parsed)
                return result
            except Exception as e:
                logger.error(f"Błąd pobierania {endpoint} strona {p}: {e}")
                if strict:
                    raise RuntimeError(f"account_fetch_failed:{endpoint}:{p}") from e
                return []

        with ThreadPoolExecutor(max_workers=10) as executor:
            page_results = list(executor.map(fetch_page, range(1, max_pages + 1)))

        all_videos = []
        seen_ids = set()
        for batch in page_results:
            for v in batch:
                if v and v.get("id") and v["id"] not in seen_ids:
                    seen_ids.add(v["id"])
                    all_videos.append(v)

        return sort_videos_newest_first(all_videos)

    def toggle_remote_save(self, video_id: str) -> bool:
        """Wysyła żądanie toggleSave do Archivebate dla podanego ID wideo."""
        try:
            watch_url = f"https://archivebate.com/watch/{video_id}"
            r = self.session.session.get(watch_url, timeout=10)
            for m in re.finditer(r'wire:id="([^"]+)" wire:initial-data="([^"]+)"', r.text):
                raw_data = m.group(2).replace('&quot;', '"')
                data = json.loads(raw_data)
                name = data['fingerprint']['name']
                if 'save-video' in name:
                    self.session.call_livewire(name, data['fingerprint'], data['serverMemo'], "toggleSave")
                    return True
        except Exception as e:
            logger.error(f"Błąd toggle_remote_save: {e}")
        return False
