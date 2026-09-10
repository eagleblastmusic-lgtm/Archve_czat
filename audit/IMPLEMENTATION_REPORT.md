# Raport wdrożenia optymalizacji — 2026-09-08

Zmiany są lokalne. Nie wykonano commita ani pusha. Zastane zmiany pliku planu i historycznych wyników audytu zachowano. Nie modyfikowano biblioteki użytkownika ani danych logowania; testy korzystają z mocków i własnych katalogów.

## Wdrożone zmiany

| Etap | Implementacja | Weryfikacja |
|---|---|---|
| E0/E1 | Testy regresji, pomiary deduplikacji i cache, ograniczony bufor metryk UI; generacje widoku/playera/siatki, anulowanie JSON i SSE, sprzątanie observera, hoverów i listenerów; wspólne pole zapytania i stan filtrów zakładki | Node: stary render, body timeout, cancellation; przeglądarka: modal, zmiana filmu, watch |
| E2 | Liniowa deduplikacja według źródła i ID, bez scalania po autorze/dacie/długości; wzbogacanie kopii; dowolna stara strona cache bez blokującego dopełniania; nowy surowy cache źródeł | 0/24/280 rekordów, grupowanie, osobne ID, scalanie brakujących metadanych bez mutacji wejścia |
| E3 | Nowy `/api/feed` + SSE `/api/feed/stream`, trwałe snapshoty, kursory i nadmiar, porcjowanie przed zebraniem 280 kart, stała kolejność snapshotu; 4 współdzielone workery źródeł i 2 snapshotów; brak pracy po odłączeniu odbiorców poza bieżącą partią | 720 ID na 3 stronach bez pominięć i duplikatów, filtry, grupowanie, odczyt zapisu po odtworzeniu serwisu, źródło 100 ms przed źródłem 2 s |
| E4 | Wspólny limit 4 spekulacyjnych transferów do końca body, wstrzymanie przy starcie/waiting/stalled; mniej warmupu; wyłączony automatyczny prefetch następnej strony; poprawne błędy obrazów, ujemny cache 20 s, LRU 64 MiB/600 wpisów, atomowy pojedynczy plik miniatury; hashe zawartości JS/CSS w HTML | Limit transferów z wolnym body, błąd miniatury 502/no-store, test wersjonowanego zasobu immutable |
| E6 | Jeden singleflight dla cold miss/force/odświeżenia odrzuconego URL, prawidłowe unieważnienie źródła, krótki cache braku URL; jawna dostępność; 416 i Content-Range, identity encoding, zamykanie upstream, diagnostyka błędu iteracji | Dokładne bajty 200/206/416; 10 requestów z odrzuconym URL daje jedno odświeżenie |
| E5 | GET status bez uruchamiania budowy, osobny POST; odczyt ready przed resolverem; atomowa kolejka 64 zleceń, 1 worker i maks. 2 FFmpeg; QUICK ma pierwszeństwo przed oczekującym FULL; dzierżawy odbiorców eliminują niepotrzebną oczekującą pracę; manifest v5 z indeksami zastępstw, immutable sprite i atomową publikacją manifestu; wspólny renderer w modalu/watch/kafelku, kontrolowany fallback seeker | Lokalny numerowany film, wymuszone braki ekstrakcji, poprawne czasy zastępcze; 10 odbiorców daje QUICK+FULL jednego generatora; anulowanie wspólnego obrazu nie psuje drugiego odbiorcy; przeglądarka: sprite w modalu |
| E7 | Blokujące operacje historii/ulubionych/synchronizacji poza event loop, logowanie w wątku; historia po rozpoczęciu odtwarzania; pomijanie rozbudowanych pól grup/playlist w nowych wpisach; wspólny attach storyboardu i seeker; dokumentacja | Test typów endpointów, istniejące testy lokalne, watch przełącza film bez błędu przypisania do const |
| Kontynuacja | Bootstrap trwałego indeksu z ciągłych stron raw cache, wznowienie workerów od następnej strony, wybór najlepszego częściowego katalogu podczas publikacji; częściowy katalog jest dostępny natychmiast z osobnym strumieniem postępu; rozdzielony cache metadanych/URL; koordynacja QUICK→FULL między kartami | `regression_catalog_bootstrap.py`, `regression_storyboard_cross_tab.cjs`, testy kontraktów API |

## Wyniki i polecenia

- `python audit/regression_checks.py`: PASS. 20 próbek deduplikacji po 1000 rekordów: p50 **1,44 ms**, p95 **3,76 ms** w ostatnim pomiarze. Oba pomiary są syntetyczne; nowy pomiar zawiera również utworzenie listy wejściowej.
- Mock cache: p50 **18,14 ms**, p95 **30,99 ms**. To test logiki szybkiej ścieżki przy mockowanym wzbogacaniu, nie pomiar całego rzeczywistego endpointu.
- `node audit/regression_frontend.cjs`: PASS.
- `node audit/regression_storyboard_client.cjs`: PASS.
- `node audit/regression_storyboard_cross_tab.cjs`: PASS (dwa konteksty kart, jeden polling QUICK→FULL, wspólny manifest).
- `node audit/regression_catalog_partial_frontend.cjs`: PASS (częściowe karty są widoczne od razu, postęp tej samej rewizji aktualizuje licznik, a publikacja przełącza rewizję).
- `python audit/regression_catalog_bootstrap.py`: PASS (seed świeżych stron cache i wznowienie od kolejnej strony).
- `python audit/regression_feed.py`: PASS.
- `python audit/regression_integration.py`: PASS.
- `python -m unittest test_suite -q`: 2 PASS, 1 jawnie pominięty test sieciowy.
- Kontrola składni Python/JS i `git diff --check`: PASS.

Wersje środowiska: `audit/environment_versions.txt`. Testy FFmpeg i feedu wymagają katalogów tymczasowych tworzonych przez Python `tempfile`; w bieżącym sandboxie Windows część takich katalogów zwracała `PermissionError`, dlatego wynik tych uruchomień oddzielono od regresji kodu. Pozostałe testy izolowane nie wymagają tej ścieżki.

Weryfikacja UI używała `audit/serve_ui_fixture.py` na 127.0.0.1:8765. Sprawdzono 64 syntetyczne karty, otwarcie modalu, nawigację do drugiego filmu, użycie sprite'a, stronę watch i zmianę filmu klawiaturą. Brak błędów JS w sprawdzonych działaniach. Serwer i karta testowa zostały zamknięte. Nie korzystano z rzeczywistych materiałów dostawców.

## Granice odbioru i pozostałe punkty planu

To nie jest potwierdzenie wszystkich kryteriów wydajnościowych planu:

- Nie wykonano pełnego E0: 20 powtórzeń startu filmu w każdym wariancie cache, ograniczonego łącza, bajtów przed pierwszą klatką i p95 wpływu generatora na rzeczywisty CDN. Nie potwierdzono progów +10% ani 50 ms interakcji w obciążonej przeglądarce.
- Nie wykonano pełnego scenariusza 50 zmian widoku w obciążonej przeglądarce ani pomiaru dwóch realnych zakładek. Dodano deterministyczny test dwóch kontekstów JS, który potwierdza jednego właściciela pollingu i dostarczenie manifestu do obu kart.
- Stary `/api/videos` pozostaje jako zgodny endpoint pomocniczy, ale zwraca ten sam kontrakt rewizji SQLite co `/api/feed`; strona główna korzysta wyłącznie z `/api/feed`.
- Zamiast nowego LRU prefetcha JSON wyłączono automatyczny prefetch następnej strony. Scheduler obejmuje spekulacyjne żądania; natywne ładowanie widocznych obrazów pozostaje zarządzane przez przeglądarkę.
- Metadane i URL są rozdzielone na `data/details_cache` oraz `data/stream_cache`; stary format jest promowany leniwie przy odczycie. Metadane zachowują TTL 24 h, a URL odtwarzania 30 min.
- Nie przepisano historycznych wpisów magazynu. Nowe wpisy są zwarte; starsze zachowano bez utraty danych.
- Polling upgrade'u jest współdzielony przez odbiorców tego samego filmu w jednym kontekście JS oraz między kartami przez `BroadcastChannel` i krótką dzierżawę `localStorage`; karta bez dostępnych API wraca do bezpiecznego watchera lokalnego.
- Rozpoczęty FFmpeg kończy się w swoim limicie czasu; anulowanie usuwa zapotrzebowanie i pomija oczekujące zadania. Nie zabija już działającego procesu potrzebnego innemu odbiorcy.
- Liczniki globalnego katalogu poza nowym feedem nadal są estymacjami. Checkpoint ma ID/snapshot/filtry; po wygaśnięciu snapshotu dawny numer strony jest tylko wskazówką.

Historyczne skrypty `audit/performance_checks.py` i `audit/frontend_checks.cjs` celowo opisują starą wersję. Nie zmieniano ich asercji na pozorny sukces; odbiór nowego kodu opiera się na osobnych testach regresji.

## Kontynuacja

- Awaria źródła kończy bieżący stream, ale nie oznacza dokładnej liczby wszystkich filmów. Odpowiedź zachowuje `total_is_estimate` i zwraca `retryable`; ponowienie wymaga odświeżenia snapshotu.
- Przy pierwszym uruchomieniu bez pełnej rewizji `/api/feed` zwraca już zapisane karty z `catalog_complete=false`; `/api/feed/stream` śledzi zmiany częściowej rewizji i przełącza się atomowo na nową pełną rewizję.
- Wspólny watcher QUICK→FULL izoluje anulowanie odbiorców i usuwa listenery po zakończeniu. Gotowy FULL z pamięci nie rejestruje dzierżawy. Ponowne użycie QUICK uruchamia brakujący upgrade; błędy przygotowania zwalniają dzierżawę.
- Nowe testy `regression_feed_errors.py` i `regression_storyboard_watchers.cjs`: PASS. Ponownie przeszły testy frontendu, współdzielonego obrazu, feedu oraz integracji. Test generatora sprawdza teraz zarówno brak pracy bez dzierżawy, jak i jeden QUICK+FULL dla współbieżnych żądań z aktywnym odbiorcą.
- Przy tej kontynuacji plik `PLAN_OPTYMALIZACJI_DLA_AGENTA.md` nie był dostępny w katalogu projektu. Zakres odtworzono z powyższej listy pozostałych prac; pełny odbiór względem oryginału wymaga jego przywrócenia.

## Pakiet C: Niezawodność i determinizm odtwarzacza (2026-09-08)

Zrealizowano wyłącznie zakres **PAKIETU C** z pliku `PLAN_NAPRAWY_REGRESJI_V2.md`. Wszystkie zmiany przetestowano lokalnie bez modyfikacji bazy danych użytkownika.

### Zrealizowane punkty:
1. **Instrumentacja i pomiary sesji otwarcia odtwarzacza**:
   - Wdrożono `startPlaybackSession` w `static/performance.js` mierzący etapy: click → URL resolve → upstream connect → first byte → metadata → first presented frame (`requestVideoFrameCallback` z fallbackiem na `loadeddata`).
   - Rejestracja `owner` (`player`, `timeline`, `karta`, `indeks`, `profil`), `priority`, `reason`, `host`. Bezpieczne logowanie bez tokenów/podpisów.
   - Wyliczanie percentyli p50/p95 (`calculatePercentiles`).
2. **Budżet ponowień i eliminacja automatycznych retries adaptera**:
   - `stream_session` w `main.py` skonfigurowany z `Retry(total=0, connect=0, read=0)` w krytycznej ścieżce proxy strumienia.
   - Rozdzielono timeouty na connect (3.5s) i read (12.0s) dla strumienia oraz connect (3.0s) i read (8.0s) w `client.py` (`call_livewire`).
3. **Odporność na odmowę połączenia upstream**:
   - Obsługa `requests.exceptions.ConnectionError` (w tym WinError 10061) przed rozpoczęciem czytania body.
   - Maksymalnie 1 próba odświeżenia przez resolver singleflight.
   - Zwrot kontrolowanego błędu HTTP 502/504 z nagłówkiem `Server-Timing: upstream_connect;dur=...` oraz przyciskiem "Ponów próbę" w UI modalu i strony watch.
4. **Ograniczony cache awarii hosta i współdzielenie błędów resolvera**:
   - Zaimplementowano bezpieczny obwód awarii hostów (`_RESOURCE_FAILURES`, cooldown 15s) z limitem 128 wpisów.
   - Współdzielenie negatywnych wyników odświeżania (`_DETAILS_REFRESH_FAILURES`) wewnątrz blokady `_details_fetch_lock`, chroniące przed lawiną zapytań ze współbieżnych żądań Range/generatora.
5. **Rozdzielenie świeżości metadanych od direct_url strumienia**:
   - Metadane wideo zachowują TTL 24h, natomiast `direct_url` jest sprawdzany pod kątem świeżości z limitem 30 minut (`direct_url_fetched_at`).
   - Unieważnianie zapamiętanych przekierowań `invalidate_cached_redirect(rejected_url)` przy odświeżaniu linku.
   - Brak URL ma 20-sekundowy negatywny cache, aby równoległe odtwarzacze nie uruchamiały kolejnych resolverów.
6. **Koordynator zajętości odtwarzacza (bufor 5s)**:
   - Funkcje `getBufferedAhead(video)` oraz `updatePlaybackBuffer(video, 5.0)` wyliczające bufor w przód.
   - Endpoint `/api/playback/status` oraz pomocnik backendu `is_playback_active(window=5.0)`.
   - Zwalnianie zasobów i pauzowanie procesów tła (`feed_service.py`, `fast_scan.py`) dopóki odtwarzacz nie zbuforuje min. 5 sekund w przód.
7. **Limity prefetchu kandydatów i weryfikacja nagłówka Range**:
   - Ograniczenie prefetchu detali do maks. 1-2 kandydatów na podstawie intencji użytkownika (debouced 250ms hover na karcie, `focusin`, następny film w playliście).
   - `prefetchCandidateStreamChunk`: testowy request Range (np. 64–256 KiB) z natychmiastowym przerwaniem (`ac.abort()`), jeśli serwer zignorował nagłówek Range i zwrócił kod 200 zamiast 206.
8. **Ścisła walidacja autora i eliminacja spekulacyjnych Livewire**:
   - Identyfikator autora pozyskiwany wyłącznie z `profile_url` lub dedykowanego pola. Całkowicie wyeliminowano zgadywanie słów z tytułu ("hot", "squirt", "solo").
   - `scraper.py`: walidacja nazwy użytkownika (`^[a-zA-Z0-9_\-\.]+$`), blokada nazw zarezerwowanych ("Model", "unknown", itp.), pomijanie wywołań Livewire przy kodzie HTTP >= 400.

### Wyniki weryfikacji Pakietu C:
- `python audit/regression_package_c.py`: **PASS**. (10 współbieżnych żądań przy odmowie połączenia wywołuje dokładnie 1 odświeżenie resolvera, 10 kontrolowanych odpowiedzi 502, cooldown 15s blokuje lawinę, rozdzielenie TTL metadanych 24h vs stream 30 min, koordynacja `/api/playback/status`, brak zgadywania słów kluczowych autora).
- `node audit/regression_package_c_frontend.cjs`: **PASS**. (Telemetria kamieni milowych sesji, anulowanie A→B po zmianie filmu `superseded`, reguła 5s bufora `playbackBusy`, natychmiastowe przerwanie prefetchu Range przy statusie 200, frontendowa walidacja `getEffectiveVideoUsername`).
- `python audit/regression_checks.py`: **PASS**.
- `python audit/regression_feed.py`: **PASS**.
- `python audit/regression_feed_errors.py`: **PASS**.
- `python audit/regression_integration.py`: **PASS**.
- `node audit/regression_frontend.cjs`: **PASS**.
- `node audit/regression_storyboard_client.cjs`: **PASS**.
- `node audit/regression_storyboard_watchers.cjs`: **PASS**.

## Pakiet D: Podgląd rzeczywistej sekundy zamiast 48 kadrów całego filmu (2026-09-08)

Zrealizowano wyłącznie zakres **PAKIETU D** z pliku `PLAN_NAPRAWY_REGRESJI_V2.md`. Wszystkie zmiany przetestowano lokalnie bez modyfikacji bazy danych użytkownika.

### Zrealizowane punkty:
1. **Gęsty cache segmentów osi czasu (30s, 1 fps)**:
   - Wdrożono segmentację osi czasu na wycinki 30s (`SEGMENT_DURATION = 30.0`), 1 klatka na sekundę (`SEGMENT_FPS = 1`), z precyzyjnym manifestem rzeczywistych znaczników czasu `times[]`.
   - Zachowano rzadki storyboard QUICK jako natychmiastowy wstępny podgląd, z wyraźnym rozróżnieniem statusu i braku oszukiwania interpolacją klatek.
2. **Ekstrakcja segmentu jednym wywołaniem FFmpeg**:
   - `_extract_segment_frames` w `storyboard_service.py` wykonuje dokładnie jeden proces dekodowania przedziału czasowego (`-ss <start> -t <duration> -vf fps=1,...`), zamiast 30 osobnych procesów i seeków.
   - Odczyt dotyczy wyłącznie potrzebnego 30-sekundowego fragmentu.
3. **Wyszukiwanie najbliższej klatki po `times[]`**:
   - Zastąpiono sztywne `floor(pos * count)` precyzyjnym wyszukiwaniem binarnym `findNearestIndex(board.times, targetTime)` w `static/youtube-storyboard.js` oraz `static/player-core.js`.
   - Aktualizacja pozycji kursora i renderowanie w `requestAnimationFrame`.
4. **Hierarchiczny podgląd (Cold vs Warm)**:
   - Gotowy segment (Warm): zmiana klatki bez zapytań Range i bez FFmpeg, 0ms narzutu, unikalny kadr dla każdej sekundy.
   - Zimny segment (Cold): natychmiastowe wyświetlenie dostępnego zgrubnego kadru (QUICK/FULL/plakat) z dyskretnym wskaźnikiem statusu ("Przygotowywanie dokładnego podglądu..."), zapotrzebowanie wysyłane w tle.
   - Zabezpieczenie przed pokazywaniem starego wyniku seekera jako nowej sekundy (`{ isLatest }`).
5. **Zarządzanie zapotrzebowaniem, kolejka priorytetowa i anulowanie**:
   - Kolejka priorytetowa (`PriorityQueue`): aktywny segment kursora (prio 0) > sąsiedni segment (prio 1) > pełny upgrade storyboardu (prio 2).
   - Zintegrowane dzierżawy (`demand(videoId, consumer)`): opuszczenie osi czasu (`pointerleave`) lub zmiana filmu unieważnia zapotrzebowanie (`AbortController`), eliminując zbędną pracę FFmpeg w tle.
6. **Współdzielony mechanizm w modalu i `/watch`**:
   - Spójny kontroler w `static/modal-player-controls.js` oraz `static/watch.html`.
   - Podglądy kafelków korzystają z gotowych sprite'ów i nie generują spekulacyjnie segmentów dla każdego filmu na liście.
   - Pamięć podręczna LRU w JS (`segmentMemory`, limit 32 segmentów) oraz bezpieczny atomowy zapis i `trim_cache_directory` na dysku SSD.

### Wyniki weryfikacji Pakietu D:
- `python audit/regression_package_d.py`: **PASS**. (10 klatek wyodrębnionych jednym FFmpeg, manifest times[] z błędem <0.01s, endpointy /api/storyboard/segment i segment/image z nagłówkiem immutable, porzucanie zadań bez dzierżawy, 0 zapytań strumienia dla gotowego segmentu).
- `node audit/regression_package_d_frontend.cjs`: **PASS**. (findNearestIndex po times[], przeciągnięcie kursora przez 10 kolejnych sekund daje 10 unikalnych kadrów, błąd czasowy w gęstym cache = 0s [wymóg <= 1s], benchmark aktualizacji klatki p50 = 0.005ms, p95 = 0.020ms [wymóg <= 50ms], cold fallback z ładowaniem, warm segment z cache, skok na odległy czas, abort przy zmianie filmu).
- `node audit/regression_storyboard_client.cjs`: **PASS**.
- `node audit/regression_storyboard_watchers.cjs`: **PASS**.
- `python audit/regression_integration.py`: **PASS**.
- `node audit/regression_frontend.cjs`: **PASS**.
- `node audit/regression_package_c_frontend.cjs`: **PASS**.
- `python audit/regression_package_c.py`: **PASS**.
- `node audit/regression_package_b_frontend.cjs`: **PASS**.
- `python audit/regression_package_b.py`: **PASS**.
- `python audit/regression_checks.py`: **PASS**.
- `python audit/regression_feed.py`: **PASS**.
- `python audit/regression_feed_errors.py`: **PASS**.
