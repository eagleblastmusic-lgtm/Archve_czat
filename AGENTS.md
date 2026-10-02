# Archivebite — kontrakt repozytorium

## Produkt i runtime

Lokalna przeglądarka nagrań Archivebate i Camwhores: feed/profil lub wyszukiwanie → filtrowana, opcjonalnie grupowana siatka → podgląd → odtwarzanie w modalu albo `/watch/{video_id}` → ulubione/historia. „Online” i „Mój katalog” są odrębnymi zakresami wyszukiwania; drugi czyta wyłącznie lokalne metadane opublikowanej rewizji, bez fetchowania źródeł i SSE wyszukiwania.

`python run.py` oraz `python desktop_app.py` uruchamiają **`runtime_app:app`** na `127.0.0.1:8000`. `main:app` pomija składanie runtime: `runtime_app.py` instaluje grouped feed v2 i QUICK przed importem `main`, następnie QoS oraz transformację HTML. Kontroluj rzeczywisty runtime przez `/api/runtime/v43`; nazwa markera `v4.3-fast2` współistnieje z kontraktami v4.5.2. Desktop używa pywebview, storyboardy FFmpeg z `imageio-ffmpeg`. Konflikt portu nie upoważnia do kończenia procesów.

## Właściciele domeny i danych

- `video_identity.VideoKey` jest wspólną tożsamością storage/katalog/API: źródło + ID dostawcy; `cw_` jest prefiksem transportowym Camwhores. Samo ID bez źródła nie wystarcza do trwałego zapisu. `fetch_contract.FetchResult` rozróżnia błąd, pustą stronę i potwierdzony koniec; nie zastępuj błędu pustą listą.
- `storage.UserStorage` posiada ulubione, historię, obserwowanych, blokady, `preferences_version` i outbox operacji zdalnych w `data/user_store.json`. Mutacje przechodzą przez jego metody: blokada pisarza, atomowy zapis, rollback pamięci i recovery są częścią kontraktu. Lokalny zapis i zdalna synchronizacja mają osobne wyniki.
- `catalog_service.CatalogService` posiada schemat i rewizje `data/catalog.db`; `deep_archivebate.DeepArchivebateService` posiada trwałe odkrywanie profili i tabelę odkrytych nagrań w tej samej bazie, publikuje je partiami do nowych rewizji. Wspólna alokacja rewizji odbywa się transakcyjnie przez `allocate_revision_in_transaction`. Opublikowane snapshoty są niezmienne; odczyt metadanych/liczników/kart korzysta z jednego snapshotu SQLite i osobnych czytelników WAL.
- Standardowy rebuild (`main._catalog_fetchers`) obejmuje oba źródła, niezależnie od aktywnego filtra UI. `feed_service.Snapshot` jest ścieżką fallback, nie drugim autorytatywnym katalogiem. Cache z `cache_store.py` nie posiada preferencji użytkownika. `model_tags.py` scala śledzony `data/model_tags.seed.json` z runtime `data/model_tags.local.json`; szybki skan wzbogaca tagi, głęboki crawler katalog.
- Zdalne sesje i parsowanie należą do `client.py`, `scraper.py` i `camwhores.py`; endpointy oraz proxy mediów do `main.py`. Konfiguracja konta: środowisko → aktualny `.env.local` → `data/credentials.local.json`, według `config.py`.

## UI i rzeczywiste miejsca zmian

- `static/app-context.js` posiada wspólny stan i DOM; `app.js` inicjalizuje moduły oraz utrzymuje globalne adaptery używane przez call sites. Widoki przełącza `video-views.js`, wyszukiwanie `search-results.js`, zdarzenia `app-events.js`; paginacja ma jednego właściciela w `pagination.js`.
- Karty/renderowanie: `video-card.js` → `video-grid.js`; modal: `video-modal.js` + `modal-player-controls.js`. `player-core.js` współdzieli mechanizmy z `watch.html`, który ma również własny kod inline. Przy zmianie playera sprawdzaj obie powierzchnie. Zachowuj generacje widoku/playera, anulowanie żądań, zamykanie SSE i zwalnianie sesji.
- Tokeny kolorów, promieni, fontu i przejść są w `static/style.css :root`. Wykorzystuj istniejące komponenty; kolejność klasycznych skryptów w `index.html` i skrypty dopinane przez runtime są zależnościami wykonawczymi. LocalStorage przechowuje m.in. filtry/checkpoint, IndexedDB cache storyboardów — nie zastępują backendowego stanu konta.

## Kontrakty szczególnie podatne na regresje

Kontrakt wydania posiada `RELEASE_REQUIREMENTS.md`; nie zastępuj go dawnymi raportami. Dodatkowo:

- `/api/feed` i SSE wiążą stronę z rewizją/snapshotem i wersją preferencji; refresh przez POST `/api/catalog/refresh` zwraca żądaną rewizję. Nie mieszaj starej i nowej generacji. Niepełny bootstrap może pokazać karty, ale nie dowodzi końca źródła.
- Grouped fast path zmienia `CatalogService.query_page` w runtime (`fast_grouped_feed_v2.install`); zwykłe strony deleguje do oryginału. Uwzględniaj lazy members i promowanie nowego lidera po usunięciu członka grupy.
- Odtwarzanie ma pierwszeństwo przed storyboardem/prefetchem. `storyboard_service.py`, `fast_storyboard_quick.py`, `player_qos_runtime.py` oraz `youtube-storyboard.js` i runtime `v43-timeline-fallback-v7.js` współdzielą pracę i jej odbiorców. Nie przywracaj dodatkowego pełnego strumienia `<video>` do seekowania timeline. Precyzję wyznaczają rzeczywiste PTS, nie żądany czas seeku.
- Błąd miniatury, timeout, prywatność lub brak `direct_url` nie dowodzą usunięcia nagrania. Kwarantanna w `video-prefetch.js` wymaga dowodu backendu; `stream_file_not_found` wymaga dwóch potwierdzeń. Nie usuwaj całej grupy z powodu niedostępności lidera.
- Mutacje wymagają zaufanego Host, dozwolonego Origin, jeśli podany, i `X-Archivebate-Mutation-Token` (`api-client.js` pobiera go z meta HTML). GET storyboardu odczytuje stan, POST uruchamia budowę. Zachowuj walidację URL/redirectów/peer IP proxy. CSP dla inline playera licz z końcowego HTML po transformacjach runtime.

## Weryfikacja i chronione materiały

Komendy i wymagane zestawy testów posiada `.github/workflows/ci.yml` (Linux Python 3.13, Windows 3.13/3.14, Node 22); odbiór wydania określa `RELEASE_REQUIREMENTS.md`. Instalacja: `python -m pip install -r requirements.lock.txt`, kontrola zależności: `python -m pip check`, składnia: `python -m compileall -q .`. Nie ma osobnego zadeklarowanego builda frontendu, lint ani typecheck.

Lokalnie używaj istniejącego `audit/implementation_runner.py` na sanitarnej kopii:

```text
python audit/implementation_runner.py --only "python audit/regression_v43_runtime_wiring.py"
python audit/implementation_runner.py --only "node audit/regression_frontend.cjs"
python audit/implementation_runner.py --only "python -m unittest test_suite -q"
python audit/implementation_runner.py --full-ci
```

Dobierz istniejącą regresję dla zmienianego kontraktu z CI. `--full-ci` uruchamia komendy na lokalnym interpreterze; nie odtwarza macierzy OS/Python. Import backendu inicjalizuje singletony/dane, a lifespan startuje producentów sieciowych: nie testuj na bibliotece użytkownika. Runner wyklucza dane i credentials; użyj osobnych `ARCHIVEBATE_ISOLATED_CHECKOUT` i `ARCHIVEBATE_AUDIT_OUTPUT` dla niezależnej próby. Samo przekierowanie `ARCHIVEBATE_USER_STORE`/`ARCHIVEBATE_CATALOG_DB` nie izoluje pozostałych cache.

`audit/regression_timeline_browser.py` sprawdza rzeczywisty browser na neutralnym media fixture w izolacji; wymaga Playwright poza lockiem aplikacji. Node/VM nie dowodzi renderu ani działania pywebview. Desktop odbieraj w jego docelowym runtime. Test sieciowy `test_suite` jest opt-in przez `ARCHIVEBATE_NETWORK_TESTS=1`.

Nie edytuj jako źródeł ani nie nadpisuj podczas testów `.env.local`, lokalnych credentials, danych konta i backupów, katalogu/WAL ani runtime cache. `requirements.lock.txt` jest generowany z `requirements.in`; `requirements.txt` wskazuje lock. Kopie `audit/isolated_implementation_*`, wyniki `repair_results_*`, `audit/historical/` i `*.bak_*` nie są bieżącą implementacją ani aktualnym dowodem PASS. Wyklucz je z rekurencyjnego discovery; część kopii jest zagnieżdżona i ma niedostępne katalogi.

## Binding agent-system

Wydanie **1.0.1**; zasoby tylko do odczytu, poza repo. APP dotyczy lokalnej aplikacji/browsera i pywebview; WEB dobieraj do dotkniętego kontraktu przeglądarkowego, bez zakładania publicznego SEO/commerce. Ładuj potrzebne reguły i zależności na żądanie, bez preloadu całych Canonów.

- [SYSTEM.md](C:/Projekty/AGENTS.md/agent-system/SYSTEM.md) — identyfikacja wydania i zasady pilotażu.
- [APP 1.10.2026](C:/Projekty/AGENTS.md/agent-system/skills/premium-ui/references/canon-app.md), SHA-256 `53ef3adf72737c47d97c6f9e4d6921aa18d1ff7a1c6bc787f0757fd6b6704bfa`.
- [WEB 1.0 / 1.10.2026](C:/Projekty/AGENTS.md/agent-system/skills/premium-ui/references/canon-web.md), SHA-256 `9deb331f4ae073cefc60999eca90338078dfdfce44a7d4ceb3da73b5074e36c6`.

To lokalne ścieżki bindingu, nie instalacja/discovery skilla. Zmiana maszyny wymaga potwierdzenia dostępu i tych samych hashy.
