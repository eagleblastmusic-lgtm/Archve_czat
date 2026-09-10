# Archivebate Video Browser 🎥

Nowoczesna aplikacja desktopowo-webowa GUI do przeglądania i wyszukiwania materiałów z serwisu **Archivebate.com** w kafelkowym układzie (Grid View) z automatycznym logowaniem kontem użytkownika, wyszukiwarką tagów oraz wbudowanym odtwarzaczem.

## ✨ Funkcje
- **Automatyczne logowanie:** dane konta są czytane bezpiecznie z `.env.local`, zmiennych środowiskowych albo `data/credentials.local.json` — nie są zapisane w kodzie.
- **Kafelkowy interfejs (Dark Mode):** responsywna siatka z animowanymi podglądami wideo, czasem trwania, liczbą wyświetleń i platformą.
- **Wyszukiwarka po tagach i profilach:** wyszukiwanie fraz takich jak `#trans`, `#teen`, `#couple`, `#female`, `#lovense`, nazw modelek itp.
- **Pasek szybkich tagów:** klikalne pigułki/tagi ułatwiające filtrowanie.
- **Wbudowany odtwarzacz wideo:** modal z odtwarzaczem Mixdrop iframe, opcją pobierania i bezpośrednim linkiem.
- **Przeglądanie profili:** szybkie przejście do wszystkich archiwalnych nagrań wybranej modelki.

## 🚀 Jak uruchomić?

Wystarczy dwukrotnie kliknąć plik:
```bash
start.bat
```
lub uruchomić w konsoli:
```bash
python run.py
```
Aplikacja automatycznie otworzy się w domyślnej przeglądarce pod adresem: `http://127.0.0.1:8000`.


## 🔐 Konfiguracja konta

Skopiuj `.env.example` jako `.env.local` i wpisz:

```env
ARCHIVEBATE_EMAIL=twoj_email@example.com
ARCHIVEBATE_PASSWORD=twoje_haslo
```

`.env.local` jest ignorowany przez Git. Bez danych logowania aplikacja uruchomi się w trybie anonimowym, a funkcje konta mogą być niedostępne.

## ⚡ Cache i szybkość

- feed strony głównej jest trwale cache'owany na dysku i działa w trybie stale-while-revalidate;
- miniatury używają RAM + SSD LRU oraz prefetchu;
- metadane detali i krótkotrwałe adresy strumieni mają osobne cache SSD;
- storyboard timeline jest zapisywany w cache backendu i współdzielony między kartami;
- odległe kafelki są renderowane i pobierane leniwie.

## Timeline w stylu YouTube

Podgląd osi czasu dla Archivebate jest teraz generowany jako jeden cache'owany sprite JPG z wieloma klatkami. Przy pierwszym otwarciu filmu storyboard może być przez chwilę przygotowywany; każde kolejne otwarcie korzysta z cache SSD i zmiana klatek podczas ruchu kursora jest natychmiastowa. Generator korzysta z `imageio-ffmpeg`, więc nie wymaga osobnej ręcznej instalacji FFmpeg.

Jeżeli logowanie było wcześniej skonfigurowane przez `USTAW_KONTO.bat`, nowa wersja poprawnie obsługuje także pliki `.env.local` zapisane z BOM. Przycisk ponownego logowania wczytuje plik ponownie bez konieczności restartu aplikacji.


## Optymalizacje lokalne (2026-09-08)

Strona główna korzysta z `/api/feed` i `/api/feed/stream`. Trwały indeks SQLite
przechowuje ostatnią kompletną rewizję, więc liczba nagrań i stron jest stała
podczas aktualizacji. Przy pierwszym uruchomieniu aplikacja zasila indeks z
lokalnych stron cache i wznawia pobieranie od kolejnej strony; do czasu publikacji
pełnej rewizji pokazuje dostępne karty oraz stan „Przygotowanie katalogu”.
Snapshot zachowuje kolejność, rewizję i filtry, a zmiana preferencji nie kasuje
surowych stron.

Podgląd korzysta z QUICK jako natychmiastowego obrazu oraz z gęstych segmentów
30-sekundowych (klatka co 1 s) dla dokładnego czasu osi. Gotowe segmenty są
odtwarzane z cache bez nowych żądań wideo. Upgrade QUICK → FULL jest współdzielony
między odbiorcami w jednej karcie i między kartami tej samej aplikacji.
Pierwsza budowa wymaga FFmpeg i transferu; globalna kolejka ogranicza liczbę
procesów, a praca ustępuje aktywnemu odtwarzaczowi. Historia jest zapisywana po
rozpoczęciu odtwarzania.

Testy offline (bez zewnętrznych źródeł i modyfikacji biblioteki użytkownika):

```text
python audit/regression_checks.py
node audit/regression_frontend.cjs
node audit/regression_storyboard_client.cjs
node audit/regression_storyboard_cross_tab.cjs
node audit/regression_catalog_partial_frontend.cjs
python audit/regression_feed.py
python audit/regression_catalog_bootstrap.py
python audit/regression_integration.py
python -m unittest test_suite -q
```

Testy feedu i FFmpeg używają własnych katalogów tymczasowych. Skrypty
`performance_checks.py` i `frontend_checks.cjs` pozostają historyczną diagnostyką
błędów i nie są testami odbioru nowego kodu. Test sieciowy starego zestawu
wymaga jawnego `ARCHIVEBATE_NETWORK_TESTS=1`.

`ArchivebatePerf.timings` zawiera ostatnie 200 lokalnych pomiarów pierwszej
porcji feedu i startu modalu; nie zapisuje ani nie wysyła telemetrii.
Pełny zakres weryfikacji i pozostałe ograniczenia: `audit/IMPLEMENTATION_REPORT.md`.

## Naprawy po audycie skonsolidowanym

Ta generacja usuwa osadzone dane logowania, destrukcyjne `taskkill`, fałszywe potwierdzanie zapisu i synchronizacji, blokowanie event loop przez auto-sync, błędną semantykę kompletności/odświeżania katalogu oraz niezweryfikowane oznaczanie precyzji storyboardu.

Dodatkowa kwalifikacja:

```text
python audit/regression_audit_fixes.py
python audit/regression_package_d.py
```

Historyczne `frontend_results.json` i `performance_results.json` zostały przeniesione do `audit/historical/` i nie są release evidence. Bieżące testy są wykonywane przez GitHub Actions dla konkretnego SHA. Kontrakt wydania znajduje się w `RELEASE_REQUIREMENTS.md`.
