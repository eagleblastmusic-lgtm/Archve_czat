# Archivebate — FULL OPTIMIZED

## Wydajność
- persistent cache strony głównej na SSD w trybie stale-while-revalidate;
- równoległe pobieranie źródłowych stron pozostaje aktywne;
- persistent cache detali wideo, używany również przez `/api/video/stream?id=...`;
- RAM + SSD cache miniaturek z limitem ok. 750 MB i przybliżonym LRU;
- frontendowy LRU detali (180 wpisów);
- priorytetyzacja miniaturek widocznych w viewport;
- agresywny, ale ograniczony prefetch miniaturek bieżącej i następnej strony;
- porcjowane renderowanie kafelków i `content-visibility` z poprzedniej wersji SPEED;
- GZip dla większych odpowiedzi;
- cache walidacji DNS hostów proxy, żeby ochrona SSRF nie spowalniała każdej miniatury.

## Player / timeline
- wspólny `player-core.js` dla modalnego playera i `/watch`;
- dokładny latest-request-wins seek bez `fastSeek`;
- podgląd działa także podczas przeciągania timeline;
- persistent storyboard w IndexedDB dla Archivebate;
- storyboard jest generowany dopiero po ustabilizowaniu bufora, żeby nie konkurować z pierwszym startem filmu;
- kafelki wykorzystują gotowy storyboard zamiast otwierać kolejny pełny stream, jeśli storyboard już istnieje;
- Camwhores nadal korzysta z natywnych storyboardów, gdy są dostępne.

## Stabilność / bezpieczeństwo
- atomowy zapis `user_store.json` (temp + fsync + os.replace) oraz RLock dla mutacji;
- dane logowania usunięte z kodu; konfiguracja przez `.env.local`, ENV lub `data/credentials.local.json`;
- `USTAW_KONTO.bat` do lokalnej konfiguracji konta;
- ograniczony CORS do lokalnej aplikacji;
- ochrona proxy przed localhost/private/link-local i walidacja redirectów;
- poprawiona walidacja powodzenia logowania;
- czytelniejsza obsługa timeout/offline/HTTP przez `api-client.js`;
- cache mają limity zamiast nieograniczonego wzrostu.

## Modularizacja
- `static/api-client.js` — komunikacja API i błędy;
- `static/performance.js` — LRU, idle work, prefetch;
- `static/player-core.js` — wspólny mechanizm timeline seek;
- `static/storyboard-cache.js` — persistent storyboard IndexedDB;
- `cache_store.py` — atomowy cache, trim i ochrona URL;
- `config.py` — konfiguracja danych konta.

## Świadomie NIE dodano
- zapisywania pozycji filmu / resume playback;
- oznaczeń obejrzenia ani procentowego paska obejrzenia na kafelkach.
