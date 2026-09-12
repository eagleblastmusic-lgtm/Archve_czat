import json
import os
from pathlib import Path
from typing import Dict, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOCAL_CREDENTIALS_FILE = DATA_DIR / "credentials.local.json"
ENV_FILE = BASE_DIR / ".env.local"


def _read_env_file(path: Path) -> Dict[str, str]:
    """Czyta prosty .env. utf-8-sig celowo usuwa BOM z Windows PowerShell 5.x."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    try:
        content = path.read_text(encoding="utf-8-sig")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip().lstrip("\ufeff")
            if not key or not all(ch.isalnum() or ch == "_" for ch in key):
                continue

            # Strip comments only outside quotes. Unquoted values may contain spaces; this is
            # important for quoted Windows secrets and passphrases with punctuation.
            quote = None
            value_chars = []
            for index, char in enumerate(raw_value.strip()):
                if char in {"'", '"'}:
                    if quote is None:
                        quote = char
                    elif quote == char:
                        quote = None
                if char == "#" and quote is None and (index == 0 or raw_value.strip()[index - 1].isspace()):
                    break
                value_chars.append(char)
            value = "".join(value_chars).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
    except OSError:
        pass
    return values


def get_archivebate_credentials() -> Tuple[str, str]:
    """Czyta dane logowania bez utrwalania ich w procesie.

    Kolejność:
    1) prawdziwe zmienne środowiskowe,
    2) aktualna zawartość .env.local (można zmienić bez restartu),
    3) data/credentials.local.json,
    4) brak kompletnej jawnej konfiguracji: tryb anonimowy (puste dane).
    """
    env_email = os.getenv("ARCHIVEBATE_EMAIL", "").strip()
    env_password = os.getenv("ARCHIVEBATE_PASSWORD", "")
    if env_email and env_password:
        return env_email, env_password

    file_values = _read_env_file(ENV_FILE)
    email = str(file_values.get("ARCHIVEBATE_EMAIL", "")).strip()
    password = str(file_values.get("ARCHIVEBATE_PASSWORD", ""))
    if email and password:
        return email, password

    if LOCAL_CREDENTIALS_FILE.exists():
        try:
            data = json.loads(LOCAL_CREDENTIALS_FILE.read_text(encoding="utf-8-sig"))
            email = str(data.get("email", "")).strip()
            password = str(data.get("password", ""))
            if email and password:
                return email, password
        except (OSError, ValueError, TypeError):
            pass

    # Brak kompletnej jawnej konfiguracji zawsze oznacza tryb anonimowy.
    # Nie wybieramy żadnej domyślnej tożsamości i nie inicjujemy zdalnego loginu.
    return "", ""
