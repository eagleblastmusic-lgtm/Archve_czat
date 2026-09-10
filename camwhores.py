import requests
import re
import html
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger("camwhores")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.camwhores.tv/"
}
from requests.adapters import HTTPAdapter
from concurrent.futures import ThreadPoolExecutor, as_completed

class CamwhoresScraper:
    def __init__(self):
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(DEFAULT_HEADERS)
        self._cache = {}
        self._details_cache = {}
        self._url_cache = {}
        self._query_last_page = {}

    def _clean_title(self, raw_title: str) -> str:
        """Czyści encje HTML i zbędne znaki z tytułu."""
        if not raw_title:
            return ""
        decoded = html.unescape(raw_title)
        decoded = re.sub(r'&#\d+;', '', decoded)
        decoded = re.sub(r'[\U00010000-\U0010ffff]', '', decoded)
        return re.sub(r'\s+', ' ', decoded).strip()

    def _extract_username(self, title: str, slug: str) -> str:
        """Wyodrębnia nazwę modelki z tytułu lub adresu URL."""
        if title:
            # Często tytuł to "ModelName - Opis" lub "ModelName Stripchat ..."
            if " - " in title:
                candidate = title.split(" - ")[0].strip()
                if candidate and len(candidate.split()) <= 2:
                    return candidate
            m = re.match(r'^([a-zA-Z0-9_\-]+)\s+(?:chaturbate|stripchat|onlyfans|camsoda|myfreecams|bongacams|streamate|flirt4free)', title, re.IGNORECASE)
            if m:
                return m.group(1)
            # Jeśli tytuł zawiera myślniki bez spacji (np. "lustflare1-2026.01.05-footjob")
            if "-" in title:
                part0 = title.split("-")[0].strip('-_.,')
                if re.match(r'^[a-zA-Z0-9_]{3,25}$', part0) and part0.lower() not in ["the", "hot", "cum", "cam", "sex", "live", "show", "video", "best", "new", "real", "watch", "free"]:
                    return part0
            # Jeśli pierwszy wyraz tytułu wygląda jak nazwa modelki (np. "melodyxlove 9")
            words = title.split()
            if words:
                first_w = words[0].strip('-_.,')
                if re.match(r'^[a-zA-Z0-9_]{3,25}$', first_w) and first_w.lower() not in ["the", "hot", "cum", "cam", "sex", "live", "show", "video", "best", "new", "real", "watch", "free"]:
                    return first_w
        if slug:
            parts = slug.split('-')
            if parts and re.match(r'^[a-zA-Z0-9_]{3,25}$', parts[0]) and parts[0].lower() not in ["the", "hot", "cum", "cam", "sex", "live", "show", "video", "best", "new", "real", "watch", "free"]:
                return parts[0]
            parts_under = slug.split('_')
            if parts_under and re.match(r'^[a-zA-Z0-9_]{3,25}$', parts_under[0]) and parts_under[0].lower() not in ["the", "hot", "cum", "cam", "sex", "live", "show", "video", "best", "new", "real", "watch", "free"]:
                return parts_under[0]
        return "Model"

    def parse_video_card(self, item_html: str) -> Optional[Dict[str, Any]]:
        """Parsuje pojedynczy kafelek z HTML Camwhores.tv."""
        try:
            # Odrzucamy filmy prywatne (niedostępne dla zwykłych użytkowników)
            if 'class="item private' in item_html or 'ico-private' in item_html or 'line-private' in item_html or 'private ' in item_html:
                return None

            # URL i ID
            url_m = re.search(r'href=["\'](https://www\.camwhores\.tv/videos/(\d+)/([^"\']*)/?)["\']', item_html)
            if not url_m:
                return None
            watch_url = url_m.group(1)
            raw_id = url_m.group(2)
            slug = url_m.group(3)
            video_id = f"cw_{raw_id}"
            self._url_cache[video_id] = watch_url
            self._url_cache[raw_id] = watch_url

            # Tytuł
            title_m = re.search(r'title=["\']([^"\']+)["\']', item_html)
            raw_title = title_m.group(1) if title_m else ""
            title = self._clean_title(raw_title)

            # Miniatura
            thumb_m = re.search(r'data-original=["\']([^"\']+)["\']', item_html) or re.search(r'src=["\']([^"\']+)["\']', item_html)
            thumbnail = thumb_m.group(1) if thumb_m else ""
            if "data:image" in thumbnail:
                # fallback na inną miniaturę
                data_orig_m = re.search(r'data-original=["\'](https://[^"\']+)["\']', item_html)
                thumbnail = data_orig_m.group(1) if data_orig_m else ""

            # Modelka
            username = self._extract_username(title, slug)

            # Czas trwania
            dur_m = re.search(r'<div class="duration">([^<]+)</div>', item_html)
            duration = dur_m.group(1).strip() if dur_m else "N/A"

            # Wyświetlenia
            views_m = re.search(r'<div class="views">([^<]+)</div>', item_html)
            views = views_m.group(1).strip() if views_m else ""

            # Data
            date_m = re.search(r'<div class="added">\s*<em>([^<]+)</em>', item_html)
            date = date_m.group(1).strip() if date_m else "Niedawno"

            # Wykrywanie platformy
            platform = "Camwhores.tv"
            title_lower = (title + " " + slug).lower()
            for p in ["Chaturbate", "Stripchat", "Onlyfans", "Camsoda", "Myfreecams", "Bongacams", "Streamate", "TikTok"]:
                if p.lower() in title_lower:
                    platform = p
                    break

            # Wykrywanie timeline klatek zrzutów ekranu
            cnt_m = re.search(r'data-cnt=["\'](\d+)["\']', item_html)
            timeline_count = int(cnt_m.group(1)) if cnt_m else 15

            timeline_prefix = ""
            if thumbnail and "/" in thumbnail:
                timeline_prefix = thumbnail[:thumbnail.rfind('/') + 1]

            preview_video = ""

            card_dict = {
                "id": video_id,
                "raw_id": raw_id,
                "url": watch_url,
                "thumbnail": thumbnail,
                "poster": thumbnail,
                "preview_video": preview_video,
                "timeline_prefix": timeline_prefix,
                "timeline_count": timeline_count,
                "duration": duration,
                "username": username,
                "title": title,
                "profile_url": f"https://www.camwhores.tv/search/{username}/",
                "date": date,
                "views": views,
                "platform": platform,
                "source": "camwhores"
            }
            try:
                from scraper import extract_video_tags
                card_dict["tags"] = extract_video_tags(card_dict)
            except Exception:
                card_dict["tags"] = ["Female"]
            if "Camwhores" not in card_dict["tags"]:
                card_dict["tags"].append("Camwhores")
            return card_dict
        except Exception as e:
            logger.debug(f"Błąd parsowania kafelka Camwhores: {e}")
            return None

    def get_latest_videos(self, page: int = 1, strict: bool = False) -> List[Dict[str, Any]]:
        """Pobiera najnowsze filmy ze strony głównej Camwhores.tv (z automatycznym fallbackiem na mirror)."""
        cache_key = f"latest:{page}"
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            import time
            if time.time() - entry["time"] < 180 and entry.get("data"):
                return entry["data"]

        urls_to_try = [
            f"https://www.camwhores.tv/latest-updates/{page}/" if page > 1 else "https://www.camwhores.tv/",
            f"https://www.camwhores.co/latest-updates/{page}/" if page > 1 else "https://www.camwhores.co/",
        ]

        valid_response = False
        for url in urls_to_try:
            try:
                r = self.session.get(url, timeout=10)
                if r.status_code != 200:
                    continue
                
                valid_response = True
                raw_items = re.findall(r'(<div class="item\s*[^"]*">.*?)(?=<div class="item\s*[^"]*"|class="pagination"|$)', r.text, re.DOTALL)
                videos = []
                seen_ids = set()
                for it in raw_items:
                    v = self.parse_video_card(it)
                    if v and v["id"] not in seen_ids:
                        seen_ids.add(v["id"])
                        videos.append(v)

                if videos:
                    import time
                    self._cache[cache_key] = {"data": videos, "time": time.time()}
                    return videos
            except Exception as e:
                logger.debug(f"Błąd pobierania wideo z {url}: {e}")
                continue

        if strict and not valid_response: raise RuntimeError("Source did not return a successful response")
        return []

    def get_query_last_page(self, query: str) -> int:
        """Zwraca wykrytą lub oszacowaną maksymalną liczbę stron Camwhores dla danego zapytania."""
        clean_q = query.replace("#", "").strip().lower()
        if hasattr(self, "_query_last_page") and clean_q in self._query_last_page:
            return self._query_last_page[clean_q]
        known_tags = {
            'trans': 1465, 'milf': 3200, 'anal': 2100, 'asian': 1800, 'latina': 1200,
            'teen': 4500, 'bbw': 800, 'blonde': 1500, 'brunette': 2000, 'ebony': 1100,
            'couple': 900, 'squirt': 1300, 'pawg': 600, 'big-tits': 1700
        }
        clean_tag = clean_q.replace(' ', '-').replace('_', '-')
        return known_tags.get(clean_tag, 60)

    def search_videos(self, query: str, page: int = 1) -> List[Dict[str, Any]]:
        """Wyszukuje filmy w Camwhores.tv dla danego tagu lub nazwy modelki (z fallbackiem)."""
        clean_q = query.replace("#", "").strip()
        if not clean_q:
            return []

        cache_key = f"search:{clean_q.lower()}:{page}"
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            import time
            if time.time() - entry["time"] < 180 and entry.get("data"):
                return entry["data"]

        clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '-', clean_q.lower())
        known_tag_names = {'trans', 'bbw', 'anal', 'milf', 'asian', 'latina', 'teen', 'blonde', 'brunette', 'ebony', 'couple', 'squirt', 'pawg', 'big-tits', 'pussy', 'masturbation', 'dildo', 'amateur', 'blowjob', 'creampie'}
        is_tag_search = query.startswith("#") or clean_slug in known_tag_names
        from_str = f"{page:02d}"

        if is_tag_search:
            urls_to_try = [
                f"https://www.camwhores.tv/tags/{clean_slug}/{page}/" if page > 1 else f"https://www.camwhores.tv/tags/{clean_slug}/",
                f"https://www.camwhores.tv/search/{clean_q}/?from_videos={from_str}" if page > 1 else f"https://www.camwhores.tv/search/{clean_q}/",
                f"https://www.camwhores.co/tags/{clean_slug}/{page}/" if page > 1 else f"https://www.camwhores.co/tags/{clean_slug}/",
                f"https://www.camwhores.co/search/{clean_q}/?from_videos={from_str}" if page > 1 else f"https://www.camwhores.co/search/{clean_q}/",
            ]
        else:
            urls_to_try = [
                f"https://www.camwhores.tv/search/{clean_q}/?from_videos={from_str}" if page > 1 else f"https://www.camwhores.tv/search/{clean_q}/",
                f"https://www.camwhores.tv/tags/{clean_slug}/{page}/" if page > 1 else f"https://www.camwhores.tv/tags/{clean_slug}/",
                f"https://www.camwhores.co/search/{clean_q}/?from_videos={from_str}" if page > 1 else f"https://www.camwhores.co/search/{clean_q}/",
                f"https://www.camwhores.co/tags/{clean_slug}/{page}/" if page > 1 else f"https://www.camwhores.co/tags/{clean_slug}/",
            ]

        for url in urls_to_try:
            try:
                r = self.session.get(url, timeout=10)
                if r.status_code != 200:
                    continue
                
                # Zapisujemy wykrytą maksymalną liczbę stron
                last_m = re.search(r'class="last".*?from[^\"]*:(\d+)', r.text, re.DOTALL)
                if last_m:
                    try:
                        lp = int(last_m.group(1))
                        if not hasattr(self, "_query_last_page"):
                            self._query_last_page = {}
                        self._query_last_page[clean_q.lower()] = max(lp, self._query_last_page.get(clean_q.lower(), 1))
                    except Exception:
                        pass

                raw_items = re.findall(r'(<div class="item\s*[^"]*">.*?)(?=<div class="item\s*[^"]*"|class="pagination"|$)', r.text, re.DOTALL)
                videos = []
                seen_ids = set()
                for it in raw_items:
                    v = self.parse_video_card(it)
                    if v and v["id"] not in seen_ids:
                        seen_ids.add(v["id"])
                        videos.append(v)

                if videos:
                    import time
                    self._cache[cache_key] = {"data": videos, "time": time.time()}
                    return videos
            except Exception as e:
                logger.debug(f"Błąd wyszukiwania w Camwhores ({url}): {e}")
                continue

        return []

    def search_videos_multi(self, query: str, pages: List[int] = None) -> List[Dict[str, Any]]:
        """Pobiera równolegle wiele stron wyników z Camwhores (szybki transfer 150-300 wideo)."""
        if not pages:
            pages = [1, 2, 3]
        all_vids = []
        seen = set()
        with ThreadPoolExecutor(max_workers=min(len(pages), 10)) as executor:
            future_to_p = {executor.submit(self.search_videos, query, p): p for p in pages}
            for f in as_completed(future_to_p):
                try:
                    for v in (f.result() or []):
                        vid = str(v.get("id"))
                        if vid and vid not in seen:
                            seen.add(vid)
                            all_vids.append(v)
                except Exception:
                    pass
        return all_vids

    def fetch_search_bundle(self, query: str, app_page: int = 1) -> List[Dict[str, Any]]:
        """Pobiera zoptymalizowaną paczkę wideo dla danej strony aplikacji (150-250 wideo)."""
        clean_q = query.replace("#", "").strip()
        if not clean_q:
            return []

        clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '-', clean_q.lower())
        known_tag_names = {'trans', 'bbw', 'anal', 'milf', 'asian', 'latina', 'teen', 'blonde', 'brunette', 'ebony', 'couple', 'squirt', 'pawg', 'big-tits', 'pussy', 'masturbation', 'dildo', 'amateur', 'blowjob', 'creampie'}
        is_tag_search = query.startswith("#") or clean_slug in known_tag_names

        all_vids = []
        seen = set()

        if is_tag_search:
            # Dla tagów łączymy strony tagu (24 vids/strona) i strony wyszukiwarki (40 vids/strona)
            tag_pages = list(range((app_page - 1) * 6 + 1, app_page * 6 + 1))
            search_pages = [(app_page - 1) * 2 + 1, app_page * 2]
            with ThreadPoolExecutor(max_workers=8) as executor:
                futs = [executor.submit(self.search_videos, '#' + clean_q, p) for p in tag_pages]
                futs += [executor.submit(self.search_videos, clean_q, p) for p in search_pages]
                for f in as_completed(futs):
                    try:
                        for v in (f.result() or []):
                            vid = str(v.get("id"))
                            if vid and vid not in seen:
                                seen.add(vid)
                                all_vids.append(v)
                    except Exception:
                        pass
        else:
            # Dla haseł ogólnych lub nazw modelek
            search_pages = list(range((app_page - 1) * 4 + 1, app_page * 4 + 1))
            all_vids = self.search_videos_multi(clean_q, search_pages)

        return all_vids

    def get_video_details(self, video_id_or_url: str) -> Dict[str, Any]:
        """Wyciąga bezpośredni strumień MP4 ze strony wideo na Camwhores.tv."""
        if str(video_id_or_url).startswith("http"):
            watch_url = video_id_or_url
            raw_id_m = re.search(r'/videos/(\d+)/', watch_url)
            raw_id = raw_id_m.group(1) if raw_id_m else "0"
        else:
            raw_id = str(video_id_or_url).replace("cw_", "").strip()
            watch_url = self._url_cache.get(raw_id) or self._url_cache.get(f"cw_{raw_id}")

        if not watch_url:
            watch_url = f"https://www.camwhores.tv/videos/{raw_id}/video/"
        elif watch_url.endswith(f"/{raw_id}/") or watch_url.endswith(f"/{raw_id}"):
            watch_url = watch_url.rstrip("/") + "/video/"

        if raw_id in self._details_cache:
            import time
            entry = self._details_cache[raw_id]
            if time.time() - entry["time"] < 1800 and entry["data"].get("direct_url"):
                return entry["data"]

        urls_to_try = [
            watch_url,
            watch_url.replace("www.camwhores.tv", "www.camwhores.co"),
            f"https://www.camwhores.tv/videos/{raw_id}/video/",
            f"https://www.camwhores.co/videos/{raw_id}/video/"
        ]

        seen_urls = []
        for u in urls_to_try:
            if u not in seen_urls:
                seen_urls.append(u)

        r = None
        for u in seen_urls:
            try:
                resp = self.session.get(u, timeout=10)
                if resp.status_code == 200 and ("video_url" in resp.text or "get_file" in resp.text):
                    r = resp
                    watch_url = u
                    break
            except Exception:
                continue

        if not r or r.status_code != 200:
            # Sprawdź czy to film oznaczony jako prywatny na Camwhores
            try:
                check_resp = self.session.get(watch_url, timeout=6)
                if check_resp.status_code == 200 and ("private video" in check_resp.text.lower() or "login-required" in check_resp.text.lower() or "no-player" in check_resp.text.lower()):
                    return {
                        "id": f"cw_{raw_id}",
                        "url": watch_url,
                        "direct_url": "",
                        "embed_url": watch_url,
                        "source": "camwhores",
                        "is_private": True,
                        "error_message": "Ten film jest prywatny na Camwhores (dostępny wyłącznie dla zarejestrowanych członków)."
                    }
            except Exception:
                pass
            return {"id": f"cw_{raw_id}", "url": watch_url, "direct_url": "", "embed_url": watch_url, "source": "camwhores"}

        try:
            # Bezpośredni URL MP4
            video_url_m = re.search(r'video_url:\s*[\'"]([^\'"]+)[\'"]', r.text)
            direct_url = video_url_m.group(1) if video_url_m else ""

            if not direct_url:
                gf_m = re.search(r'(https?://(?:www\.)?camwhores\.(?:tv|co)/get_file/[^\s"\'<>]+\.mp4[^\s"\'<>]*)', r.text)
                if gf_m:
                    direct_url = gf_m.group(1)

            # Plakat / Miniatura
            poster_m = re.search(r'preview_url:\s*[\'"]([^\'"]+)[\'"]', r.text) or re.search(r'property="og:image"\s+content="([^"]+)"', r.text)
            poster = poster_m.group(1) if poster_m else ""

            # Tytuł
            title_m = re.search(r'property="og:title"\s+content="([^"]+)"', r.text) or re.search(r'<title>([^<]+)</title>', r.text)
            title = self._clean_title(title_m.group(1)) if title_m else ""

            # Słowa kluczowe / tagi ze strony Camwhores
            kw_m = re.search(r'name="keywords"\s+content="([^"]+)"', r.text)
            keywords = [k.strip() for k in kw_m.group(1).split(",")] if kw_m else []

            # Dokładne tagi z linków /tags/ i /categories/
            cw_tags = set()
            for t_m in re.finditer(r'href=["\']https://(?:www\.)?camwhores\.(?:tv|co)/(?:tags|categories)/([^/"]+)/["\']', r.text):
                ct_clean = t_m.group(1).strip().lower()
                if ct_clean in ["trans", "transgender", "tranny", "transsexual", "ts", "shemale", "she-male", "ladyboy"]:
                    cw_tags.add("Trans")
                elif ct_clean in ["milf", "teen", "anal", "squirt", "ebony", "latina", "asian", "bbw", "couple", "feet", "dildo", "cosplay"]:
                    cw_tags.add(ct_clean.capitalize())

            slug_m = re.search(r'/videos/\d+/([^/]+)', watch_url)
            slug_val = slug_m.group(1) if slug_m else ""
            model_username = self._extract_username(title, slug_val)
            if (not model_username or model_username.lower() == "model") and keywords:
                for kw in keywords:
                    k_clean = kw.strip().lower()
                    if k_clean and 3 <= len(k_clean) <= 25 and k_clean not in ["chaturbate", "stripchat", "onlyfans", "camwhores", "feet", "anal", "blowjob", "couple", "milf", "teen", "trans", "footjob", "toes", "soles", "dildo"]:
                        if (title and k_clean in title.lower()) or (slug_val and k_clean in slug_val.lower()):
                            model_username = kw.strip()
                            break
            if model_username and cw_tags:
                try:
                    from model_tags import model_tag_manager
                    gender_val = "Trans" if "Trans" in cw_tags else None
                    model_tag_manager.set_model(model_username, gender=gender_val, tags=list(cw_tags))
                except Exception:
                    pass

            result = {
                "id": f"cw_{raw_id}",
                "url": watch_url,
                "embed_url": watch_url,
                "direct_url": direct_url,
                "thumbnail": poster,
                "poster": poster,
                "title": title,
                "username": model_username,
                "keywords": keywords,
                "tags": list(cw_tags),
                "source": "camwhores",
                "platform": "Camwhores.tv"
            }

            if direct_url:
                import time
                self._details_cache[raw_id] = {"data": result, "time": time.time()}
            return result
        except Exception as e:
            logger.error(f"Błąd pobierania detali Camwhores ({watch_url}): {e}")
            return {"id": f"cw_{raw_id}", "url": watch_url, "direct_url": "", "embed_url": watch_url, "source": "camwhores"}

# ============================================================
# INTELIGENTNA DEDUPLIKACJA (ANTI-DUPLICATE)
# ============================================================
def normalize_model_name(username: str) -> str:
    """Normalizuje nazwę modelki do małych liter i cyfr (usuwa spacje, podkreślniki itp.)."""
    if not username:
        return ""
    return re.sub(r'[^a-z0-9]', '', str(username).lower())

def extract_date_signature(text: str) -> Optional[str]:
    """Wyciąga datę kalendarzową (np. 2026-08-21 lub 21.08.2026)."""
    if not text:
        return None
    # YYYY-MM-DD lub YYYY_MM_DD
    m1 = re.search(r'(\d{4})[-_](\d{2})[-_](\d{2})', text)
    if m1:
        return f"{m1.group(1)}-{m1.group(2)}-{m1.group(3)}"
    # DD.MM.YYYY
    m2 = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', text)
    if m2:
        return f"{m2.group(3)}-{m2.group(2)}-{m2.group(1)}"
    return None

def video_identity(video):
    """Source-scoped identity; descriptive metadata never proves equality."""
    ident = str(video.get("id") or "").strip()
    source = "camwhores" if ident.startswith("cw_") or video.get("source") == "camwhores" else (video.get("source") or "archivebate")
    if source == "camwhores" and ident.startswith("cw_"):
        ident = ident[3:]
    if ident:
        return (source, "id", ident)
    url = str(video.get("url") or "").strip().split("#")[0].rstrip("/")
    return (source, "url", url) if url else None


def is_duplicate(video_a, video_b):
    key = video_identity(video_a)
    return key is not None and key == video_identity(video_b)


def deduplicate_videos(videos):
    """Linear, stable deduplication. Fill missing metadata without mutating input."""
    result, seen = [], {}
    for video in videos or []:
        if not isinstance(video, dict):
            continue
        key = video_identity(video)
        if key is not None and key in seen:
            existing = seen[key]
            for field, value in video.items():
                if not existing.get(field) and value:
                    existing[field] = value
            continue
        item = dict(video)
        result.append(item)
        if key is not None:
            seen[key] = item
    return result

def merge_and_deduplicate(primary_videos: List[Dict[str, Any]], secondary_videos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Łączy wideo z Archivebate (primary) oraz Camwhores.tv (secondary), bez dublowania filmów."""
    combined = (primary_videos or []) + (secondary_videos or [])
    return deduplicate_videos(combined)

camwhores_scraper = CamwhoresScraper()
