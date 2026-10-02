# Archivebite — Raport Kwalifikacji Release Candidate

Data kwalifikacji: **3 października 2026**.  
Punkt odniesienia: **Release Candidate 1** (`release/archivebite-2026-10-03-rc1`).  
Wszystkie testy, bramki automatyczne i pomiary wykonano na dokładnym SHA kandydata.

---

## 1. Identyfikacja wydania i stan Git

- **Gałąź:** `release/archivebite-2026-10-03-rc1`
- **Commit SHA:** `b950c040ebe2c98019ba0f2a34f6e1fa85e4edcb`
- **Tree SHA:** `9976ed3042270ce0c09aa866d5665b2809bbc8ae`
- **Punkt bazowy (parent):** `6c2065b0d2eee9a0fce3986185ff1731e3869991` (`origin/main`)
- **Git status w momencie kwalifikacji:**
  ```text
  On branch release/archivebite-2026-10-03-rc1
  nothing to commit, working tree clean
  ```
- **Zdalna gałąź:** `origin/release/archivebite-2026-10-03-rc1` (wypchnięta do GitHub)

---

## 2. Zakres wdrożenia i weryfikacja architektury

Implementacja objęła usterki i usprawnienia **F01–F13**, **R01–R02** oraz **U01–U02** zdefiniowane w `audit/AUDYT_I_PLAN_POPRAWEK_2026-10-02.md` i zrealizowane w `audit/WYKONANIE_PLANU_2026-10-03.md`.

| Identyfikator | Priorytet | Właściciel kodu | Kluczowa ścieżka wykonania | Status wdrożenia | Dowód / regresja |
| --- | --- | --- | --- | --- | --- |
| **F01** | P1 | `scraper.py`, `main.py`, `storage.py` | Stan docelowy (desired) zamiast ślepego przełącznika; odczyt zdalnego Livewire przed i po mutacji; CAS z `operation_id`; status `unknown` w przypadku braku potwierdzenia | **WDROŻONE** | `audit/regression_audit_fixes.py`, `audit/regression_loading.py` |
| **F02** | P1 | `storage.py` | `merge_remote_data` chroni nierozstrzygnięte zamiary i tombstones; snapshot nie wskrzesza usuniętych ulubionych; monotoniczne `_bump_preferences()` przy zmianie | **WDROŻONE** | `audit/regression_user_projection.py`, `audit/regression_audit_fixes.py` |
| **F03** | P1 | `storage.py` | `_read_method` z `self._lock` dla wszystkich geterów; `projection_snapshot()` z memoizacją unieważnianą przy mutacji; brak czytania stanu przejściowego | **WDROŻONE** | `audit/regression_user_projection.py` |
| **F04** | P1 | `storage.py`, `main.py` | `restore_snapshot` nadaje monotoniczne `preferences_version` i `restore_generation`; preview raportuje zastąpienie i delty kolekcji; rollback przy błędzie | **WDROŻONE** | `audit/regression_next_generation.py`, `audit/regression_user_projection.py` |
| **F05** | P1 | `search-results.js`, `video-prefetch.js`, `video-card.js` | Jawny `_mediaScope: 'local_catalog'`; miniatury w widoku lokalnym używają wyłącznie lokalnego cache (`cache_only=true`); brak automatycznych żądań do źródeł, SSE i availability probes | **WDROŻONE** | `audit/regression_timeline_browser.py` (`local_scope`: `provider_requests: []`), `audit/regression_local_search.cjs` |
| **F06** | P2 | `catalog_service.py`, `main.py`, `search-results.js` | `author_filter` (`all`, `only_fav`, `exclude_fav`) filtrowany w SQLite przed `COUNT(*)` i paginacją z uwzględnieniem `preferences_version` | **WDROŻONE** | `audit/regression_search_filters.cjs`, `audit/regression_local_search.cjs` |
| **F07** | P2 | `main.py`, `favorites.js`, `storage.py` | Dedykowany endpoint `/api/account/favorites/state` sprawdzający stan pojedynczej tożsamości `VideoKey` bez limitu pierwszych 1000 wpisów | **WDROŻONE** | `audit/regression_loading_frontend.cjs`, `audit/regression_loading.py` |
| **F08** | P2 | `account.js`, `video-views.js` | Wejście do panelu konta wywołuje `beginViewRequest()`; unieważnienie `viewGeneration`, anulowanie kontrolerów fetch, zamknięcie SSE i odcięcie prefetchu; brak late-render pod panelem | **WDROŻONE** | `audit/regression_loading_frontend.cjs`, `audit/regression_timeline_browser.py` |
| **F09** | P2 | `account.js`, `video-modal.js`, `watch.html`, `main.py` | Raportowanie faz synchronizacji i terminalnego wyniku; relogin nie symuluje zakończenia synchronizacji; czytelne rozdzielenie `local_committed` od zdalnego błędu; retry historii z dedykowanym komunikatem | **WDROŻONE** | `audit/regression_timeline_browser.py`, `audit/regression_audit_fixes.py` |
| **F10** | P2 | `toast.js`, `blocked-models.js` | Toast z akcją przechowuje `expiresAt`; cofnięcie blokady dostępne przez pełne 10 sekund i wygaszane wspólnie z akcją | **WDROŻONE** | `audit/regression_timeline_browser.py` (`undo`: `available_after_four_seconds: true`, `expires_after_ten_seconds: true`) |
| **F11** | P2 | `dom-utils.js`, `style.css`, `index.html` | Wspólny focus manager `manageDialogFocus` (trap fokusu, Shift+Tab, Escape, przywracanie fokusu otwierającemu); semantyczne etykiety `aria-label`; reguły `@media (prefers-reduced-motion)` i `@media (forced-colors)` | **WDROŻONE** | `audit/regression_accessibility.cjs`, `audit/regression_timeline_browser.py` |
| **F12** | P2 | `main.py`, `storage.py` | Praca blokująca odczytów konta, sortowania i wzbogacania przeniesiona poza pętlę ASGI przez `asyncio.to_thread(_account_page, ...)`; lekka projekcja diagnostyczna | **WDROŻONE** | `audit/profile_user_and_local_search.py`, `audit/regression_user_projection.py` |
| **F13** | P2 | `requirements.in`, `requirements.lock.txt` | Kompletność domknięcia zależności Windows (`pythonnet==3.1.0`, `clr-loader`, `cffi`, `pycparser` z markerami `sys_platform == "win32"`) w zamrożonym locku | **WDROŻONE** | `audit/regression_dependency_lock.py`, `python -m pip check` |
| **R01** | P2 | `runtime_readiness.py`, `desktop_app.py`, `run.py` | Launcher web i Desktop oczekują na marker gotowości `/api/runtime/v43`; Desktop używa własnej instancji `uvicorn.Server` ze sterowanym `should_exit`, join wątku serwera, brak zabijania obcych procesów | **WDROŻONE** | `audit/regression_desktop_lifecycle.py` |
| **R02** | P2 | `storyboard_service.py`, `fast_storyboard_quick.py`, `ci.yml` | Rzeczywisty single-flight generatora quick storyboardu; bramka browserowa Playwright dodana do Linux CI z uploadem artefaktów dowodowych | **WDROŻONE** | `audit/regression_integration.py`, `.github/workflows/ci.yml`, GitHub Actions run `37076896780` |
| **U01** | P3 | `static/vendor/fontawesome`, `index.html`, `style.css` | Lokalne zasoby Font Awesome 6.5.1 (CSS i webfonts WOFF2) w `static/vendor/` bez zależności od zewnętrznego CDN; wspólne tokeny stylów | **WDROŻONE** | `audit/regression_timeline_browser.py`, inspekcja statyczna |
| **U02** | P3 | `catalog_service.py` | Leniwy, pochodny indeks SQLite FTS5 trigram z triggerami `INSERT`, `UPDATE`, `DELETE`; `LIKE` jako autorytet semantyczny; bezpieczny fallback skanu przy zbyt szerokich zapytaniach | **WDROŻONE** | `audit/profile_user_and_local_search.py`, `audit/regression_local_search.cjs` |

---

## 3. Zestawienie bramek jakościowych (Quality Gates)

### A. Bramki lokalne na dokładnym SHA RC (`b950c040ebe2c98019ba0f2a34f6e1fa85e4edcb`)

| Bramka | Środowisko | Narzędzie / Komenda | Liczba testów | Wynik | Czas |
| --- | --- | --- | --- | --- | --- |
| Składnia i kompilacja | Windows (Python 3.14) | `python -m compileall -q .` | Repo-wide | **PASS** | 0.22 s |
| Spójność zależności | Windows | `python -m pip check` | Wszystkie zainstalowane | **PASS** | 0.49 s |
| Domknięcie locka platformy | Windows | `python audit/regression_dependency_lock.py` | 1/1 | **PASS** | 0.47 s |
| Spójność projekcji użytkownika | Windows | `python audit/regression_user_projection.py` | 1/1 | **PASS** | 2.16 s |
| Pełny lokalny CI runner | Windows (izolacja) | `python audit/implementation_runner.py --full-ci` | **93/93** | **PASS** | 148 s |
| – *offline-regression* | Windows (izolacja) | 48 komend Python/Node | 48/48 | **PASS** | ~60 s |
| – *windows-contracts* | Windows (izolacja) | 24 komendy Python/Node | 24/24 | **PASS** | ~35 s |
| – *windows-python314* | Windows (izolacja) | 20 komend Python/Node | 20/20 | **PASS** | ~28 s |
| – *browser-acceptance* | Windows (Edge/Chromium) | `python audit/regression_timeline_browser.py` | 1/1 | **PASS** | 72.01 s |
| Profil F12/U02 (440 tys. wierszy) | Windows (izolacja) | `python audit/profile_user_and_local_search.py` | 1/1 | **PASS** | 60.84 s |
| Cykl życia Desktop (pywebview/WebView2) | Windows (izolacja) | `python audit/regression_desktop_lifecycle.py` | 1/1 (2 pełne restarty) | **PASS** | 21.01 s |

### B. Bramki zdalne (Linux & Windows CI w GitHub Actions)

- **Workflow Run:** [CI Run 37076896780](https://github.com/eagleblastmusic-lgtm/Archve_czat/actions/runs/37076896780)
- **Gałąź:** `release/archivebite-2026-10-03-rc1`
- **Commit SHA:** `b950c040ebe2c98019ba0f2a34f6e1fa85e4edcb`
- **Status ogólny:** **SUCCESS** (wszystkie zadania zielone)

| Zadanie (Job) | Platforma / Runtime | Identyfikator zadania | Wynik | Czas |
| --- | --- | --- | --- | --- |
| **`browser-acceptance`** | Ubuntu Latest / Python 3.13 / Chromium Playwright | `111068725780` | **PASS** | 1m 45s |
| **`offline-regression`** | Ubuntu Latest / Python 3.13 / Node 22 | `111068725791` | **PASS** | 58s |
| **`windows-contracts`** | Windows Latest / Python 3.13 / Node 22 | `111068725802` | **PASS** | 1m 13s |
| **`windows-python314`** | Windows Latest / Python 3.14 / Node 22 | `111068725848` | **PASS** | 1m 01s |

---

## 4. Szczegółowe wyniki bramek krytycznych

### A. Bramka Browser Acceptance (Playwright na Linux CI i lokalnym Chromium)
- **Linux CI Job:** `browser-acceptance` (ID `111068725780`)
  - Instalacja Chromium przez `python -m playwright install --with-deps chromium` zakończona sukcesem.
  - Test wykonał wszystkie scenariusze odtwarzacza `/watch` i modalu dla obu dostawców (`archivebate` i `camwhores`).
  - Zweryfikowano:
    - Rzeczywiste klatki i PTS w osadzonym materiale neutralnym (samples dla 60s, 70s, 80s, 90s).
    - Brak niekontrolowanych zapytań o strumienie przy hoverze timeline.
    - Zabezpieczenie przed late-render po przejściu do panelu konta.
    - Odzyskiwanie miniatur i brak zapytań do zewnętrznych źródeł w zakresie „Mój katalog” (`provider_requests: []`).
    - Działanie klawiatury (Shift+Tab, Tab, Escape, powrót fokusu).
    - Zachowanie 10 sekund okna Undo z automatycznym wygaszeniem.
    - Dedykowane komunikaty błędów historii z opcją ponowienia.
    - 0 błędów typu `pageerror`.
  - Artefakt dowodowy: `neutral-browser-evidence` (ID `11256314268`, 134 703 bajty) zawiera `timeline_browser_result.json` oraz zrzuty ekranu UI.
- **Lokalny bieg Windows:** `audit/regression_timeline_browser.py` zakończył się wynikiem **PASS** (72.01 s).

### B. Bramka Desktop Lifecycle (Natywny pywebview / WebView2)
- Wykonano w izolacji test `audit/regression_desktop_lifecycle.py` z dwoma kolejnymi cyklami na tym samym dynamicznie przydzielonym porcie:
  - **Cykl 1:** Start okna WebView2, weryfikacja DOM i etykiet (`pageLabel: "Numer strony"`, `accountBadge: "Anonimowe"`), neutralne odtwarzanie mediów, fokus klawiatury w natywnym oknie, sygnał zamknięcia okna, zatrzymanie workerów i FFmpeg (`active_ffmpeg_at_close: true` -> `ffmpeg_exited: true`), zwolnienie portu i blokad w czasie **5.242 s**.
  - **Cykl 2 (restart na tym samym porcie):** Błyskawiczny restart, ponowna walidacja gotowości, zwolnienie portu i zasobów w czasie **4.326 s**.
  - Brak wiszących procesów dzieci (`FFmpeg`, serwer `uvicorn`, crawler `deep_archivebate`).
  - Trwały zapis i ponowne otwarcie bazy `UserStorage` po restarcie (`fixture_blocked` zachowany).

### C. Profil wydajnościowy F12 i U02
- Zmierzono na 440 000 rekordów w SQLite:
  - Pierwsza budowa indeksu FTS5 trigram + zapytanie: **25.33 s** (jednorazowy koszt pierwszego użycia).
  - Wyszukiwanie ciepłe frazy rzadkiej: **p50 0.46 ms**.
  - Wyszukiwanie bez wyników: **p50 0.83 ms**.
  - Wyszukiwanie autora z 777 trafieniami: **p50 70.01 ms**.
  - Zapytanie równoległe (4 jednoczesne żądania): heartbeat zachowany poniżej **55 ms**.

---

## 5. Checklista manualnego odbioru UI (Kryteria nieweryfikowalne automatycznie)

Poniższa checklista stanowi specyfikację odbioru manualnego. Testy automatyczne potwierdziły poprawność kontraktów HTML/DOM/CSS/JS, natomiast fizyczna weryfikacja percepcyjna wymaga udziału testera / użytkownika.

| Obszar | Kryterium manualne | Status automatyczny | Status odbioru manualnego | Uwagi do weryfikacji |
| --- | --- | --- | --- | --- |
| **Klawiatura** | Pełna nawigacja bez myszy (Tab / Shift+Tab) | **PASS** (kontrakt) | `MANUAL_REQUIRED` | Sprawdzić kolejność fokusu w widoku siatki |
| **Focus Trap** | Dialog managera blokad i modal playera zatrzymują fokus wewnątrz | **PASS** (kontrakt) | `MANUAL_REQUIRED` | Zweryfikować zapętlenie Tab na pierwszym/ostatnim przycisku |
| **Restore Focus** | Zamknięcie dialogu klawiszem Escape przywraca fokus na przycisk otwierający | **PASS** (kontrakt) | `MANUAL_REQUIRED` | Przetestować w oknie Desktop i Edge |
| **Etykiety pól** | Pola skoku strony posiadają czytelne `aria-label="Numer strony"` | **PASS** (DOM) | `MANUAL_REQUIRED` | Zweryfikować odczyt przez NVDA / Narrator |
| **Czytnik ekranu** | Komunikaty toastów i stanu konta anonsowane jako `role="status"` | **PASS** (DOM) | `MANUAL_REQUIRED` | Sprawdzić syntezę mowy podczas blokowania profilu |
| **Zoom / Reflow** | Skalowanie widoku do 200% w przeglądarce i oknie Desktop | **PASS** (CSS) | `MANUAL_REQUIRED` | Brak ucinania tekstu i nakładania kontrolek |
| **Minimalny rozmiar** | Zmniejszenie okna Desktop do 960x640 | **PASS** (min_size) | `MANUAL_REQUIRED` | Pasek odtwarzacza i kontrolki mieszczą się w oknie |
| **Reduced Motion** | Systemowa flaga `prefers-reduced-motion: reduce` wyłącza shimmery i animacje | **PASS** (CSS media) | `MANUAL_REQUIRED` | Potwierdzić brak animacji szkieletu ładowania |
| **Forced Colors** | Tryb Windows High Contrast / Canvas renderuje czytelne obramowania i tekst | **PASS** (CSS media) | `MANUAL_REQUIRED` | Zweryfikować widoczność obramowań kart i przycisków |
| **Widoczność synchronizacji** | Rozróżnienie stanu lokalnego serca od błędu zdalnej synchronizacji | **PASS** (kontrakt) | `MANUAL_REQUIRED` | Sprawdzić widoczność ikony synchronizacji przy braku sieci |
| **Retry historii** | Błąd zapisu historii w `/watch` i modalu daje czytelny przycisk ponowienia | **PASS** (kontrakt) | `MANUAL_REQUIRED` | Wywołać sztuczny błąd 503 i kliknąć retry |
| **Undo 10 sekund** | Przycisk „Cofnij” w toaście blokady jest klikalny przez pełne 10 sekund | **PASS** (timer/DOM) | `MANUAL_REQUIRED` | Odczekać 8 sekund i kliknąć przycisk |

---

## 6. Znane ograniczenia i charakterystyka operacyjna

1. **Koszt pierwszej budowy indeksu FTS5:** Indeks trigramowy tabeli katalogowej jest budowany leniwie przy pierwszym lokalnym zapytaniu wyszukiwania; dla biblioteki rzędu 440 tys. rekordów jednorazowy czas budowy wynosi ~18–25 sekund. Kolejne zapytania wykonują się w czasie poniżej 1–70 ms.
2. **Autorytatywność `LIKE`:** FTS5 służy do szybkiej selekcji kandydatów; ostatecznym autorytetem dopasowania semantycznego pozostaje `LIKE ? ESCAPE '\'`.
3. **Izolacja danych:** Katalog `data/catalog.db` i `data/user_store.json` nie były modyfikowane podczas kwalifikacji; testy wykonywano na kopiach syntetycznych i sanitarnych fixtures.
4. **Zewnętrzne testy sieciowe:** Testy prawdziwych zewnętrznych serwisów (Archivebate / Camwhores) pozostają opt-in (`ARCHIVEBATE_NETWORK_TESTS=1`) w celu ochrony przed blokadami IP oraz ochroną prywatności konta użytkownika.

---

## 7. Decyzja kwalifikacyjna

### Status automatyczny: **RELEASE READY**
Wszystkie wymagane automatyczne bramki jakościowe (kompilacja, lock zależności, 93 testy lokalne, Linux Chromium Playwright CI, Windows CI 3.13/3.14, natywny Desktop lifecycle oraz profil FTS) zakończyły się wynikiem **PASS (100%)**.

### Status formalny wydania: **RELEASE CANDIDATE 1 ZWERYFIKOWANY**
Wydanie na commicie `b950c040ebe2c98019ba0f2a34f6e1fa85e4edcb` jest technicznie i automatycznie zamknięte. Przed ostateczną dystrybucją binarną do użytkowników końcowych zaleca się przeprowadzenie manualnej checklisty ułatwień dostępu (czytnik ekranu NVDA/Narrator, 200% zoom).
