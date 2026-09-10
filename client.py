import requests
import re
import json
import logging
from typing import Optional, Dict, Any
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("archivebate_client")

class ArchivebateSession:
    BASE_URL = "https://archivebate.com"

    def __init__(self, email: str = "", password: str = ""):
        self.email = email
        self.password = password
        self.session = requests.Session()
        
        # Kontrolowana pula połączeń i ograniczony budżet retry, aby nie blokować pętli zdarzeń
        retries = Retry(total=1, connect=1, read=0, backoff_factor=0.2, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://archivebate.com/"
        })
        self.csrf_token: Optional[str] = None
        self.is_logged_in: bool = False
        self.last_login_error: str = ""

    def refresh_csrf(self) -> Optional[str]:
        """Pobiera świeży token CSRF ze strony głównej."""
        try:
            r = self.session.get(f"{self.BASE_URL}/login", timeout=10)
            match = re.search(r'name="_token"\s+value="([^"]+)"', r.text) or re.search(r'csrf-token"\s+content="([^"]+)"', r.text)
            if match:
                self.csrf_token = match.group(1)
                self.session.headers.update({"X-CSRF-TOKEN": self.csrf_token})
                return self.csrf_token
        except Exception as e:
            logger.error(f"Błąd odświeżania CSRF: {e}")
        return None

    def login(self) -> bool:
        """Loguje użytkownika do konta Archivebate i zachowuje czytelny powód błędu."""
        self.last_login_error = ""
        if not self.email or not self.password:
            self.last_login_error = "Brak danych logowania. Uruchom USTAW_KONTO.bat."
            logger.warning(self.last_login_error)
            self.is_logged_in = False
            return False

        logger.info(f"Logowanie kontem {self.email}...")
        self.refresh_csrf()
        if not self.csrf_token:
            self.last_login_error = "Nie udało się pobrać tokenu CSRF ze strony logowania."
            logger.error(self.last_login_error)
            self.is_logged_in = False
            return False

        login_url = f"{self.BASE_URL}/login"
        payload = {
            "_token": self.csrf_token,
            "email": self.email,
            "password": self.password,
            "remember": "on"
        }

        try:
            # Najpierw wykonujemy POST bez automatycznego redirectu. Laravel zwykle
            # odpowiada 302 po prawidłowym logowaniu; dzięki temu nie uzależniamy
            # sukcesu od obecności konkretnego napisu w HTML strony docelowej.
            r = self.session.post(login_url, data=payload, timeout=12, allow_redirects=False)
            location = str(r.headers.get("Location", ""))
            redirect_away_from_login = (
                r.status_code in (301, 302, 303, 307, 308)
                and location
                and "/login" not in location.lower()
            )

            # Weryfikacja na sekcji wymagającej konta. To jest stabilniejsze niż
            # wcześniejsze szukanie słów logout/watchlater/history w HTML POST-a.
            verify = self.session.get(f"{self.BASE_URL}/watchlater", timeout=12, allow_redirects=True)
            verify_url = str(verify.url).rstrip("/").lower()
            redirected_to_login = "/login" in verify_url
            html = verify.text.lower()
            authenticated_marker = (
                "logout" in html
                or "watchlater" in html
                or "watch later" in html
                or "history" in html
                or "following" in html
            )

            login_succeeded = (not redirected_to_login) and (
                verify.status_code < 400
                and (redirect_away_from_login or authenticated_marker)
            )

            if login_succeeded:
                self.is_logged_in = True
                # Token na stronie po zalogowaniu bywa odświeżony.
                match = re.search(r'csrf-token"\s+content="([^"]+)"', verify.text)
                if match:
                    self.csrf_token = match.group(1)
                    self.session.headers.update({"X-CSRF-TOKEN": self.csrf_token})
                logger.info(f"Zalogowano pomyślnie jako {self.email}!")
                return True

            # Laravel zwykle zwraca po błędnym logowaniu redirect z powrotem do /login.
            if redirected_to_login or "/login" in location.lower():
                self.last_login_error = "Archivebate odrzucił logowanie. Sprawdź email i hasło."
            else:
                self.last_login_error = f"Nie udało się potwierdzić sesji (HTTP {verify.status_code})."
            logger.warning(self.last_login_error)
            self.is_logged_in = False
            return False
        except requests.RequestException as e:
            self.last_login_error = f"Błąd połączenia z Archivebate: {e}"
            logger.error(self.last_login_error)
            self.is_logged_in = False
            return False
        except Exception as e:
            self.last_login_error = f"Błąd podczas logowania: {e}"
            logger.error(self.last_login_error)
            self.is_logged_in = False
            return False

    def get_status(self) -> Dict[str, Any]:
        """Zwraca aktualny status sesji."""
        return {
            "email": self.email,
            "logged_in": self.is_logged_in,
            "csrf_available": bool(self.csrf_token),
            "login_error": self.last_login_error
        }

    def call_livewire(self, component_name: str, fingerprint: dict, server_memo: dict, method: str, params: list = None) -> Optional[str]:
        """Wywołuje metodę komponentu Livewire z zachowaniem sesji."""
        url = f"{self.BASE_URL}/livewire/message/{component_name}"
        headers = {
            "Content-Type": "application/json",
            "X-CSRF-TOKEN": self.csrf_token or "",
            "X-Livewire": "true",
            "Accept": "text/html, application/xhtml+xml",
            "Referer": f"{self.BASE_URL}/"
        }
        payload = {
            "fingerprint": fingerprint,
            "serverMemo": server_memo,
            "updates": [
                {
                    "type": "callMethod",
                    "payload": {
                        "id": "lw-req-1",
                        "method": method,
                        "params": params or []
                    }
                }
            ]
        }
        try:
            r = self.session.post(url, json=payload, headers=headers, timeout=(3.0, 8.0))
            if r.status_code == 200:
                data = r.json()
                effects = data.get("effects", {})
                return effects.get("html", "")
        except Exception as e:
            logger.warning(f"Błąd wywołania Livewire ({component_name}->{method}): {type(e).__name__}")
        return None
