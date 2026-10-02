# Plan poprawek dla GPT Luna — Archivebite

## Cel i punkt startowy

Naprawić błędne ukrywanie działających nagrań/grup, ponawianie odtwarzania i odświeżanie katalogu, następnie skrócić rzeczywiste opóźnienia stron i mediów. Zachować bibliotekę, ulubione, historię, blokady autorów, trwałe rewizje i zabezpieczenia sieciowe.

Projekt: `C:\Projekty\Aplikacje\Archivebite_Czat`. Audytowany HEAD: `704e1ac15c82e650992aaccaf197ca287eaab880`. Najpierw przeczytaj `audit/AUDYT_2026-09-24.md`, `RELEASE_REQUIREMENTS.md`, lokalne AGENTS i aktualny diff. Sprawdź deltę względem tego audytu; nie zakładaj, że kod się nie zmienił.

Ten dokument zawiera plan oraz końcową mapę wykonania. Sekcje etapów opisują wymagania, a aktualny stan, dowody i luki są zapisane w tabeli na końcu. Pliki `audit/audit_20260924_*` są diagnostyką audytu; ich asercje miejscami potwierdzają istniejące wady. Po naprawie należy odwrócić odpowiednie oczekiwania w docelowych testach regresji, nie utrwalać wad jako pożądanego kontraktu.

## Zasady wykonania

1. Przed etapem oznacz wymagania `ALREADY_CLOSED`, `DELTA_REQUIRED` lub `SPEC_BLOCKED`, z krótkim dowodem. Edytuj rzeczywistą deltę.
2. Korzystaj z obecnych modułów i testów. Nie twórz równoległego managera odtwarzacza, kwarantanny, kolejki FFmpeg czy katalogu.
3. Testy importujące `main`, `storage` lub `catalog_service` uruchamiaj w kopii bez danych użytkownika. Samo ustawienie innego user store nie izoluje cache, którego ścieżki są związane z katalogiem źródeł. Runner audytu pokazuje sposób izolacji.
4. Nie usuwaj DB/WAL, nie resetuj biblioteki i nie odblokowuj wszystkich autorów. Przy migracji DB użyj spójnej kopii SQLite przez backup API; kopiowanie samego `.db` przy aktywnym WAL nie jest poprawnym backupem.
5. Po każdej spójnej zmianie wykonaj wskazane testy i uzupełnij tabelę wykonania na końcu. Po zaliczeniu końcowej bramki nie powtarzaj jej bez nowej przesłanki.
6. Zmiany API sprawdzaj od producenta do wszystkich konsumentów: modal, `/watch`, hover, next-video, runtime i testy. Nie usuwaj POST/token gate, SSRF, limitów Range ani anulowania, aby uzyskać PASS.

## Kolejność i klasyfikacja

| Etap | Zakres | Stan początkowy | Priorytet | Zależność |
|---|---|---|---|---|
| 1 | Kontrakt dostępności i migracja kwarantanny | DELTA_REQUIRED | P1 | Brak |
| 2 | Izolacja członka grupy, promocja lidera i filtry listy | DELTA_REQUIRED | P1 | Kontrakt etapu 1 |
| 3 | Retry modalu i `/watch`, współdzielenie resolvera | DELTA_REQUIRED | P1/P2 | Brak, uzgodnić dostępność z 1 |
| 4 | Rewizja odświeżenia i cache strony | DELTA_REQUIRED | P1/P2 | Brak |
| 5 | Przyspieszenie grup i zapytań z filtrami | DELTA_REQUIRED | P1 | Zachować kontrakty 2 i 4 |
| 6 | Miniatury, prefetch i priorytet odtwarzacza | Istniejące cache/QoS ALREADY_CLOSED; lokalne delty do pomiaru | P2 | Najpierw 1–5 |
| 7 | SQLite/WAL i zależności środowiska | DELTA_REQUIRED dla wersji; diagnoza WAL do wykonania | P2 | Przed testem długiej sesji |
| 8 | Testy kontraktowe, CI i odbiór live | DELTA_REQUIRED; dowód live SPEC_BLOCKED do uzyskania sesji | P1 | Całość |

## Etap 1 — dostępność i kwarantanna

**Pliki:** `scraper.py:get_video_details`, `main.py:_normalize_video_details`, `_fetch_and_cache_details`, `_fetch_details_singleflight`, `static/video-prefetch.js`.

1. Wprowadź jawny wynik dostępności z przyczyną: `available`, `private`, `unavailable` oraz `unknown`. `unavailable` rezerwuj dla zweryfikowanego trwałego braku materiału na właściwej stronie źródła/hostingu. Timeout, problem DNS, 401/403, 429, 5xx, challenge/logowanie, brak parsera i brak direct URL przy działającym embedzie to `unknown` albo `private` zależnie od dowodu. Sam HTTP 404 CDN z wygasłego adresu nie potwierdza usunięcia nagrania.
2. Zachowaj stan pobrania na wyjściu scrapera. Sprawdzaj HTTP oraz właściwość treści przed parsowaniem. Dodaj pola o jednoznacznym znaczeniu, np. `availability_reason`, `checked_at`, `retryable`; nazwy uzgodnij w jednym kontrakcie API. Jeśli źródło nie dostarcza wiarygodnego sygnału trwałego usunięcia, zwracaj `unknown`.
3. Normalizacja nie może wyprodukować `unavailable` z `{}` lub samego braku `direct_url`. Stare dokumenty cache bez dowodu usunięcia również interpretuj jako nieznane. Negatywny wynik chwilowy nie powinien wypierać poprawnych metadanych; brak odtwarzalnego URL ma mieć krótki retry/backoff, nie sześciogodzinną pewność niedostępności.
4. `confirmUnavailableVideo` ma potwierdzać tylko stan z takim dowodem. Wymuś POST istniejącym API, pomiń cache dostawcy i rozdziel próby w czasie. Drugi błąd transmisji nie potwierdza pierwszego wyniku. Odpowiedź po anulowaniu lub starsza od pozytywnego rozpoznania nie może ponownie ukryć filmu.
5. Klucz rejestru: źródło + provider ID, zgodnie z `video_identity.py`. Dodaj powód, czas próby, wygaśnięcie i możliwość „Pokaż / sprawdź ponownie”. Zapisuj tylko minimalne dane lokalne, bez adresów strumieni i tokenów.
6. Migracja v1→v2: zachowaj opcjonalny lokalny eksport starego rejestru diagnostycznego i unieważnij stare nieudokumentowane kwarantanny. Nie kopiuj ślepo dotychczasowych wpisów jako potwierdzonych usunięć. Po migracji odtwórz widok z katalogu. Nie używaj `localStorage.clear()` ani resetu danych konta.

**Weryfikacja:** rozbuduj obecne testy ładowania/backendu zamiast dodawać równoległe odpowiedniki. Pokryj timeout→timeout, 403/challenge, 429, 500, embed bez direct URL, błąd parsera, private oraz potwierdzony trwały brak. Tylko ostatni przypadek może ukryć materiał. Osobno: anulowana odpowiedź, spóźnione negatywne rozpoznanie po pozytywnym, TTL i migracja v1. Brak usunięć z biblioteki i zmian blocked_models.

**Odbiór:** działający film nie znika wskutek samej awarii odczytu. Dwie próby muszą mieć znaczenie dowodowe, nie tylko zwracać tę samą błędną etykietę.

## Etap 2 — zachowanie grupy i sprawny wybór reprezentanta

**Pliki:** `static/video-prefetch.js:pruneUnavailableGroupedMember/removeUnavailableCardInstances`, `static/video-grid.js:isGroupedVideo/filterKnownUnavailableVideos`, `static/video-card.js:createVideoCard/_updateCard/loadLazyGroupMembers`, `fast_grouped_feed_v2.py:_selected_items`, `main.py:get_catalog_group_members`, `catalog_service.py:query_group_members`.

1. Na samym początku przycinania ustal związek zdarzenia z grupą. Właściwy lider albo rzeczywiście załadowany członek o pełnym kluczu może zmieniać grupę. Obce ID ma powodować ścisłe no-op: bez dopisywania `_unavailableMemberIds`, zmiany liczby i DOM. Dla niezaładowanego członka wymagaj zweryfikowanej przynależności, nie zgaduj po ID.
2. Ujednolić rozpoznanie agregatu w prefetch i grid. `group_members_lazy` i stabilna tożsamość autora/grupy mają przetrwać spadek licznika do 1. Niedostępność reprezentanta nie jest dostępnością całej grupy. Nie usuwaj grupy, której pozostali członkowie są nieznani.
3. Odejmuj wyłącznie konkretny, potwierdzony element, najwyżej raz dla danej rewizji/projekcji. Nie mieszaj liczby z serwera z lokalnie przefiltrowaną długością fragmentu. Obsłuż współdzielony obiekt stanu/karty oraz odrębne obiekty po deduplikacji. Ponowne SSE nie może wielokrotnie odejmować tego samego filmu.
4. Gdy lista jest już dostępna, wybierz pierwszego niekwarantannowanego członka. Nie nazywaj go „potwierdzonym aktywnym”, jeśli jego odtwarzanie nie zostało sprawdzone. Gdy lista jest leniwa, pozostaw klikalną grupę i pobierz istniejący endpoint członków z małym limitem, współdzieląc jedno żądanie per grupa. Nie pobieraj wszystkich stron ani detali setek nagrań.
5. Dobierz kolejnego reprezentanta z zachowaniem źródła, filtrów i rewizji. Timeout pobrania członków zostawia grupę z retry. Zamknięcie widoku, zmiana filtrów/rewizji i nowsza odpowiedź unieważniają poprzednią próbę. Pusta pierwsza partia po kwarantannie nie dowodzi pustki całej leniwej grupy; obsłuż `has_more`.
6. Zmień tylko dane i media zależne od reprezentanta. `_updateCard` musi anulować stary hover/storyboard i przeliczyć czas, timeline, miniaturę, zbiór członków oraz aktywne linki. Obecne `const timelinePrefix`, `durationSec` i `groupedVideosList` są związane z konstrukcją karty. Nie pozostawiaj ich dla poprzedniego filmu.
7. Odśwież już otwartą szufladę, aby nie zostawały stare wiersze i stare liczniki. Dla 2→1 badge/przycisk ma przedstawiać rzeczywisty stan, a dostęp do ostatniego członka pozostaje sprawny.
8. Zachowaj scope przy pobieraniu członków. Producent `group_members_url` lub jawne parametry klienta muszą nieść `source`, `revision` i wymagany filtr autorów/ulubionych. W razie rozszerzenia endpointu wykorzystaj tę samą semantykę filtrów co feed. Sprawdź analogicznego producenta w bazowym `CatalogService`, jeżeli również zwraca leniwe grupy.

**Weryfikacja:** rozszerz istniejący scenariusz grup w `regression_loading_frontend.cjs` o niezależne kontrprzykłady audytu: obca grupa 100→100, grupa `[dead,live]` → jeden dostępny członek, leniwa grupa 2→1 widoczna po render/reconcile, ponowione zdarzenie, kolejne martwe ID, te same surowe ID różnych źródeł, odpowiedź SSE po kwarantannie, już otwarta szuflada, wolna odpowiedź po zmianie widoku. Test grupowanego backendu: identyczny autor w obu źródłach + filtr tylko jednego źródła, zgodność licznika i członków.

**Odbiór:** aktywny/niezweryfikowany członek nie znika wskutek awarii innego członka; obca grupa nie zmienia stanu; właściwy przycisk odtwarza właściwy film.

## Etap 3 — retry i czas startu odtwarzacza

**Pliki:** `static/video-modal.js:openVideoModal`, `static/watch.html:initWatch`, `static/video-prefetch.js`, `main.py:_fetch_details_singleflight/stream_video_proxy`.

1. Wszystkie wymuszone próby detali przełącz na `ArchivebateAPI.postJSON('/api/video/details/refresh?id=…', {}, options)`. Zwykły GET zostaw dla zwykłych odczytów. Usuń każdą osiągalną gałąź `GET …force_refresh=true`, także fallback.
2. Zachowaj mutation token oraz `AbortSignal`. W modalu i `/watch` resetuj loader przy retry, odtwórz po świeżym wyniku i zabezpiecz odpowiedzi generacją. Zwykłe detale nie mogą resetować już uruchomionego strumienia. Zamknięcie/zmiana filmu zatrzymuje starą sesję.
3. Napraw współdzielenie nakładających się odświeżeń: obecny lock jest dobrym prymitywem, ale `force=True` po uzyskaniu locka zawsze ponownie odpytuje źródło. Wykorzystaj generację zakończonego odświeżenia lub wynik świeższy od rozpoczęcia oczekiwania. Nie opieraj rozstrzygnięcia wyłącznie na mtime o niskiej rozdzielczości. Zależność `rejected_url` musi nadal wykluczać ponowne użycie odrzuconego URL.
4. Współdziel również rezultat błędu równoległych prób, z krótkim backoffem, bez oznaczania trwałego usunięcia. Nowe świadome retry po zakończeniu poprzedniej próby ma nadal móc wykonać świeży odczyt. Sprawdź ścieżkę równoległą: player `/stream` + pobieranie metadanych + retry.

**Weryfikacja:** rozbuduj `regression_package_c.py` i `regression_loading.py` o dwa kontrolowanie nakładające się `force=True`: dokładnie jeden resolver. Dodaj przypadek błędu i odrzuconego URL. Test UI powinien używać kontraktu rzeczywistego routera lub adaptera zapisującego metodę: GET force=405, POST z tokenem=200, POST bez tokena=403. Dotychczasowy mock, który akceptuje GET force, należy poprawić. Testuj modal i `/watch`, szybkie A→B, zamknięcie podczas retry oraz brak podwójnego `load()`.

## Etap 4 — odświeżenie strony i spójność cache

**Pliki:** `static/video-views.js:loadHomeVideos/homePageCache/prefetchHomePage`, miejsca zmiany preferencji w `static/blocked-models.js`, `static/favorites.js`, kontrakty `main.py:refresh_catalog/progressive_feed/progressive_feed_stream`.

1. Zachowaj wynik POST `/api/catalog/refresh`, odczytaj `refresh_revision` i przekaż go jawnie do GET/SSE. Nie oczekuj tego tokena w kolejnym GET — obecny backend zwraca tam `refresh_revision=null`.
2. Zachowuj poprzedni poprawny widok do czasu danych nowej rewizji; pokaż odświeżanie i ewentualny błąd nowej rewizji. Nie kończ sukcesem tylko dlatego, że poprzednia rewizja była kompletna.
3. Oddziel „Ponów ładowanie” po błędzie transportu od świadomego przebudowania całego katalogu. Pierwsza operacja ponawia pobranie, druga używa POST i śledzi nowy token. Obecne `force` łączy te znaczenia.
4. W kluczu cache strony i prefetchu uwzględnij wersję preferencji lub jawnie unieważnij cache po zmianie blokady/ulubionych. Obecne klucze zawierają tylko źródło, filtr, grupowanie i numer strony, a TTL wynosi 90 s. Zmiana preferencji musi również unieważnić trwający prefetch, aby stary wynik nie uzupełnił ponownie cache.

**Weryfikacja:** `regression_loading_frontend.cjs`, `regression_home_loading.cjs`, `regression_block_no_reload.cjs`, `regression_feed_errors.py`. Kontrprzykład 8→POST(9)→GET starej 8 musi zakończyć się śledzeniem 9. Osobno: failure rewizji 9, 409 wygasłego snapshotu bez rekursji, sieciowe retry bez POST rebuild, zmiana filtra w trakcie oraz cache następnej strony po zmianie preferencji.

## Etap 5 — usunięcie kosztownego skanu przy pierwszym ekranie

**Pliki:** `fast_grouped_feed_v2.py`, indeksy/migracje `catalog_service.py`; istniejący szybki algorytm liderów pozostaje punktem wyjścia.

1. Odtwórz reprezentatywny katalog co najmniej 440 tys. wpisów oraz filtry rzędu 1 950 blokad i kilkuset ulubionych. Dane syntetyczne mają zachować rozkład autorów, nie tylko liczbę wierszy. Alternatywa: spójny lokalny backup obecnej bazy. Zmiany indeksów wykonuj najpierw na kopii.
2. Mierz oddzielnie `_filtered_counts`, `_ensure_leaders`, `_selected_items`, enrichment, JSON oraz oczekiwanie na worker. Zapisz `EXPLAIN QUERY PLAN`, SQLite/runtime, rewizję i stan cache. Punkt bazowy audytu: pełny pierwszy odczyt 12,15 s; sam agregat po rozgrzaniu ~2,04 s.
3. Najpierw sprawdź najmniejszą zmianę: indeks pokrywający używane pola, np. `(revision, author_clean, source, video_id)`, i ewentualny wariant kolejności pod filtr źródła. Porównaj z istniejącymi indeksami; zbędnych nie duplikuj. Sprawdź plan po statystykach SQLite. To kandydat do pomiaru, nie gwarantowana poprawa.
4. Zachowaj dokładne liczby i filtry. Nie przyspieszaj przez pominięcie ulubionych, zaniżanie licznika ani usunięcie `COUNT(DISTINCT)` bez równoważnego wyniku. Zabezpiecz invalidację cache po rewizji i preferencjach.
5. Jeżeli sam indeks nie daje odpowiedniego czasu, dopiero wtedy rozdziel pierwszą partię kart od dokładnego agregatu: istniejące SSE może dostarczyć liczniki później. Wymaga to jawnego `counts_pending`/dokładności, bez fałszywych zer, skoków na złą stronę i naruszenia snapshotu. Przed tym większym wariantem zapisz wynik eksperymentu z indeksem.
6. Przetestuj również daleką stronę: cache liderów skanuje od początku do potrzebnego offsetu. Optymalizuj wyłącznie, jeśli pomiar potwierdzi problem; nie zastępuj działającego cursor scan bez potrzeby.

**Weryfikacja:** `regression_v43_grouped_fast.py`, `regression_grouped_query_batch.py`, `regression_catalog_read_concurrency.py`, `regression_catalog_v41.py`, regresje rewizji i paginacji. Rozszerz obecny benchmark o duży rozkład oraz „Bez polubionych”; wymagaj identycznych członków, kolejności i liczników przed/po. Zmierz także czas importu i rozmiar DB po nowym indeksie.

**Docelowy budżet do odbioru na tej samej maszynie:** pierwszy odczyt 16 kart z filtrami poniżej 1 s lokalnie, rozgrzany poniżej 100 ms, co najmniej 3× poprawa kosztownej ścieżki bez istotnego pogorszenia importu. Są to cele, nie obecne wyniki. Po zebraniu reprezentatywnej serii podaj medianę i p95; nie wpisuj jednego pomiaru jako p95. Jeśli budżet nieosiągnięty, raportuj rzeczywisty wynik i przeszkodę.

## Etap 6 — miniatury i prefetch bez pogorszenia odtwarzania

**Istniejące mocne elementy:** 8 początkowych miniatur eager, 4 z wysokim priorytetem; IntersectionObserver; cache RAM/SSD; ograniczony warmup; QoS generatora storyboardów. Zachowaj je.

**Małe delty do sprawdzenia:**

1. `scheduleThumbnailWarmup` w `static/video-prefetch.js` odczytuje zmienną kontrolera dopiero w odroczonym callbacku. Uchwyć lokalny kontroler/generację danej operacji i sprawdzaj anulowanie przed startem. `beginViewRequest` odwołuje się do `global.thumbnailWarmupController`, a moduł przechowuje go lokalnie. Udostępnij minimalne `cancelThumbnailWarmup()` i wywołuj je z istniejącego cyklu widoku. Test: A→B przed idle nie pobiera obrazów A.
2. `v452-next-video-prefetch.js` nie powinien uznawać zwróconego `null` po zajęciu limitu dwóch żądań za skutecznie obsłużonego kandydata. Ponów ograniczoną próbę po zwolnieniu miejsca. Nie uruchamiaj kolejki wszystkich filmów. Sprawdź też drugą ścieżkę prefetchu z modalu przed rozpoczęciem odtwarzania: jedna intencja powinna mieć jednego właściciela, bez zerwania wspólnej próby przez sygnał poprzedniego filmu.
3. Zmierz żądania miniatur pierwszego ekranu i przewijania oraz czas oczekiwania playera przy równoczesnym cold cache. Sam znacznik `priority=high` w URL nie rezerwuje workera. Starlette współdzieli ograniczoną pulę wątków dla ścieżek synchronicznych. Jeśli pomiar potwierdzi zagłodzenie, wydziel ograniczoną obsługę pobrań miniatur/streamów, zachowując anulowanie i backpressure; nie zwiększaj bez pomiaru samego limitu globalnego.
4. Zmniejszanie obrazów/WebP wprowadź tylko po pomiarze za dużych obrazów względem wyświetlanego rozmiaru. Klucz cache musi zawierać wariant rozmiaru/jakości i źródło, a szuflada/modal otrzymać właściwy wariant. Obecna mediana ~45 KB nie uzasadnia automatycznego przebudowania całego cache.
5. Ogranicz dodatkowe skanowanie DOM przy scrollu tylko po profilu pokazującym koszt. Runtime resilience jest istniejącą poprawką pustych miniatur; nie usuwaj go bez testu odłączenia i ponownego podłączenia observera.

**Odbiór:** brak pobrań nieaktualnego widoku, brak lawiny retry, brak pustych miniatur po powrocie i scrollu, pierwsza klatka i zacięcia nie pogarszają się po włączeniu warmupu. Porównaj modal i `/watch`, zimny i ciepły cache, hover podczas odtwarzania oraz szybkie A→B→C. Testy: `regression_home_loading.cjs`, `regression_v452_frontend_qos.cjs`, `regression_v43_storyboard_scheduler.py`, `regression_storyboard_client.cjs`, `regression_storyboard_cross_tab.cjs`.

## Etap 7 — utrzymanie SQLite i środowiska

1. Zapisz faktycznie używany `sqlite3.sqlite_version` dla launcherów, desktopu i CI. Lokalnie wykryto 3.50.4. Zaplanuj aktualizację dystrybucji Pythona/SQLite do wydania z poprawką WAL-reset (np. 3.50.7 lub 3.51.3 i odpowiednich późniejszych wersji). Sam pip lock nie pinjuje stdlib SQLite. Nie wymieniaj ręcznie przypadkowej DLL.
2. Najpierw uruchom pełne testy i odczyt spójnej kopii bazy na nowym runtime. Nie przypisuj istniejących błędów UI uszkodzeniu SQLite: `quick_check` obecnej bazy dał `ok`.
3. Dodaj lekką diagnostykę rozmiaru WAL, liczby aktywnych readerów i wyników checkpointu. Checkpoint wykonuj przez istniejącego właściciela zapisu, poza żądaniem pierwszego ekranu. Obecny kod już ma `wal_autocheckpoint=1000` i PASSIVE przy retencji; diagnozuj przyczynę przed dodaniem kolejnego mechanizmu.
4. Nie usuwaj `-wal` ani `-shm`. Nie uruchamiaj VACUUM podczas odtwarzania. Około 25,7% wolnych stron może zostać ponownie użyte; kompaktowanie jest opcjonalną operacją konserwacji po backupie i zamknięciu połączeń. Sam rozmiar 1,02 GB WAL nie dowodzi aktywnej zaległości checkpointu.

## Etap 8 — naprawa dowodów i końcowa bramka

1. Napraw mocki POST we wszystkich ścieżkach `regression_loading_frontend.cjs`. Zachowaj już sprawdzane właściwości, ale zamień oczekiwanie GET force na właściwy POST. Dopiero potem dołóż konkretne kontrprzykłady 1–4. Nie kasuj testu tylko dlatego, że ujawnia regresję.
2. `regression_storyboard_watchers.cjs`: korzystaj z aktualnego publicznego API albo obecnego fixture modułu. Nie wycinaj funkcji po nieistniejącym delimiterze. Zachowaj kontrakt wspólnego żądania i niezależnego anulowania odbiorców.
3. `regression_v43_runtime_wiring.py`: zaktualizuj kontrolę do obecnego runtime V4.5.2 i testuj rzeczywistą instalację/iniekcję, zamiast wymagać historycznych QUICK=2 i `?v=7`. Nie zmieniaj parametrów produkcji wyłącznie dla starego testu.
4. `regression_v43_home_scope.py`: jego bazowe hashe odpowiadają obecnej treści po LF. Napraw przenośność CRLF. Ponieważ zadanie świadomie zmienia home, zastąp historyczne zamrożenie plików aktualnymi kontraktami zachowania albo jasno zaktualizowanym zakresem wydania; nie wpisuj nowych hashy bez opisania zmiany zakresu. Wykorzystaj zgodę na poprawki zawartą w zadaniu, nie pytaj o rutynową zmianę starego ograniczenia zakresu.
5. Dodaj do odpowiednich jobów CI brakujące testy `regression_loading.py` i `regression_loading_frontend.cjs` po ich naprawie. Zachowaj testy Linux oraz Windows/Python 3.14. Dołącz rzeczywisty smoke `runtime_app`, bo test samego `main` nie dowodzi wstrzyknięcia runtime.
6. Wykonaj pełny aktualny zestaw CI/offline z `RELEASE_REQUIREMENTS.md` na końcowym stanie źródeł. Zapisz SHA/digest, wersje zależności, komendy, exit codes i pominięcia. Punktem bazowym audytu jest 46/50 PASS po korekcie kopii testowej, nie kompletne PASS.
7. Odbiór live: uruchom właściwy launcher z markerem runtime, zweryfikuj pierwszy ekran i scroll, filtr „Bez polubionych”, zmianę strony, retry, refresh rewizji, modal i `/watch`, odtwarzanie po seeku, zmianę A→B i hover przy grającym filmie. Użyj kilku dostępnych nagrań różnych źródeł, nie jednego szczęśliwego przykładu. Błędy CDN oddziel od regresji lokalnej aplikacji.
8. Zmierz kliknięcie→pierwsza przedstawiona klatka, czas miniatur pierwszego ekranu, long tasks, liczbę/transfer żądań, stall count i bufor. Wykorzystaj istniejący `ArchivebatePerf` oraz metryki QoS. Porównuj na tych samych materiałach i warunkach. Nie obiecuj stałego startu <1 s dla niekontrolowanego zewnętrznego hostingu.
9. Jeżeli brakuje konta/dostępnego materiału/sesji przeglądarki, zostaw dokładnie ten dowód jako `SPEC_BLOCKED`, z gotową instrukcją odbioru. Dokończ pozostałe niezależne etapy; nie deklaruj „wszystko działa”.

## Dokumentacja techniczna wykorzystana przy planie

- [SQLite query planner](https://www.sqlite.org/queryplanner.html): indeks pokrywający może ograniczyć odczyty tabeli; wariant należy sprawdzić na planie zapytania i pomiarze.
- [SQLite WAL](https://www.sqlite.org/wal.html): checkpoint, współbieżne odczyty i poprawione wersje WAL-reset; sam rozmiar pliku nie wystarcza do diagnozy zaległości.
- [Starlette thread pool](https://www.starlette.io/threadpool/): domyślna pula 40 tokenów jest współdzielona przez operacje synchroniczne. To uzasadnia pomiar konkurencji miniatur i playera, nie dowodzi jej wystąpienia w tej sesji.
- [web.dev: lazy loading obrazów](https://web.dev/articles/browser-level-image-lazy-loading): obrazy pierwszego ekranu powinny być dostępne wcześnie, dalsze mogą być leniwe. Projekt już częściowo realizuje tę zasadę.

## Mapa wykonania do uzupełniania

| Etap | Zmiana / commit | Aktualne testy i wynik | Pozostałe ograniczenia |
|---|---|---|---|
| 1 | `DELTA_REQUIRED` — dostępność źródła ma jawne wyniki `ok/empty/retryable_error/permanent_error`; kwarantanna V2 jest scoped po źródle, rewizji i dowodzie, ma TTL, podwójne potwierdzenie POST oraz unieważnienie starego cache. | `regression_loading.py`, `regression_loading_frontend.cjs`, `regression_v42_full.py`: PASS w izolowanej kopii. | Nie zmieniano biblioteki użytkownika; zewnętrzne źródła nie były odpytywane w odbiorze live. |
| 2 | `DELTA_REQUIRED` — grupy zachowują `source/revision`, martwy lider jest zastępowany przez dostępnego członka, członkowie współdzielą refcountowane żądanie i zachowują scope filtrów; odświeżenie otwartej szuflady jest idempotentne. | `regression_loading_frontend.cjs`, `regression_v43_grouped_fast.py`, `regression_grouped_query_batch.py`, `regression_catalog_read_concurrency.py`: PASS w izolacji. | Brak live sesji z rzeczywistymi nagraniami różnych źródeł. |
| 3 | `DELTA_REQUIRED` — wymuszenie detali/feedu używa POST z tokenem, GET `force_refresh` zwraca 405; zachowano token mutacji, `AbortSignal`, generację i współdzielenie wyniku retry/błędu. | `regression_v42_full.py`, `regression_loading.py`, `regression_loading_frontend.cjs`, `regression_v43_runtime_wiring.py`: PASS w izolacji; kontrakt GET 405/POST 403/200 sprawdzony. | Nie wykonano zewnętrznego odtwarzania CDN w tej sesji. |
| 4 | `DELTA_REQUIRED` — wynik POST `/api/catalog/refresh` niesie `refresh_revision`; GET/SSE śledzi tę rewizję, zachowuje poprzedni widok do nowych danych, odróżnia retry transportu od rebuild oraz unieważnia cache/prefetch po zmianie preferencji. | `regression_loading_frontend.cjs`, `regression_home_loading.cjs`, `regression_block_no_reload.cjs`, `regression_catalog_partial_frontend.cjs`, `regression_audit_fixes.py`: PASS w izolacji. | Pełna zmiana rewizji nie została potwierdzona na aktywnym koncie. |
| 5 | `DELTA_REQUIRED` — dodano najmniejszy indeks pokrywający `(revision, author_clean, source, video_id)` po porównaniu wariantów; nie zmieniono semantyki filtrów, `COUNT(DISTINCT)` ani kolejności. | Izolowany benchmark 440 000 wierszy, rozkład Zipf 1,08, 1 950 blokad i 300 autorów ulubionych: dokładna zgodność sygnatur; wybrany wariant: `_filtered_counts` p50/p95 553/593 ms, API E2E 800/834 ms dla `Bez polubionych`, import 55 ms (+17%), DB +20,7 MB. Log: `audit/implementation_2026-09-24/001_benchmark_python_audit_regression_v43_grouped_fast_py_--benchmark-440k.log`. | To pomiar syntetyczny na SQLite 3.50.4; nie wykonano migracji ani pomiaru na realnej bazie użytkownika. Wariant bez filtrów ulubionych ma niewielką zmianę czasu, więc nie usuwano istniejących indeksów. |
| 6 | `ALREADY_CLOSED` dla eager 8/priority 4, observera, cache RAM/SSD i limitów QoS; `DELTA_REQUIRED` dla przechwycenia kontrolera warmupu, anulowania i ograniczonego retry prefetchu następnego filmu. | `regression_home_loading.cjs`, `regression_v452_frontend_qos.cjs`, `regression_v43_storyboard_scheduler.py`, `regression_storyboard_client.cjs`, `regression_storyboard_watchers.cjs`, `regression_storyboard_cross_tab.cjs`: PASS w izolacji. | Brak porównania cold/warm cache z realnym CDN; czas pierwszej klatki i stall count pozostają `SPEC_BLOCKED`. |
| 7 | `DELTA_REQUIRED` — diagnostyka raportuje wersję SQLite, rozmiar WAL, aktywnych readerów i ostatni PASSIVE checkpoint; CI zapisuje faktyczny runtime. Istniejący właściciel zapisu nadal wykonuje checkpoint przy retencji. | `regression_catalog_read_concurrency.py`, `regression_v43_runtime_wiring.py`, pełna bramka offline: PASS w izolacji. Zapisane: Python 3.14.4 / SQLite 3.50.4 / Node v24.19.0. | `SPEC_BLOCKED`: lokalny SQLite 3.50.4 jest poniżej wersji z poprawką WAL-reset; aktualizacja dystrybucji Pythona nie była wykonywana, nie usuwano `-wal/-shm`. |
| 8 | `DELTA_REQUIRED` — zaktualizowano mocki POST, test watcherów do aktualnego API, smoke `runtime_app`, test scope home oraz wszystkie trzy joby CI o loading/runtime. Dodano izolowany runner z digestem, wersjami i logami. | Końcowa bramka offline/Windows/Python 3.14: `85/85 PASS` w izolowanej kopii; `pip check` i negatywne kontrole destrukcyjnego launchera/sekretów: PASS; targeted benchmark i smoke także PASS. Dowód: `audit/implementation_2026-09-24/test_results.json`. | `SPEC_BLOCKED`: live acceptance (launcher, konto, CDN, kilka nagrań, metryki pierwszej klatki) nie był możliwy — brak dostępnej sesji przeglądarki/materiału; nie deklaruję odbioru live. |

## Prompt startowy dla GPT Luna

> Zaimplementuj `audit/PLAN_POPRAWEK_GPT_LUNA_2026-09-24.md` w projekcie `C:\Projekty\Aplikacje\Archivebite_Czat`. Najpierw przeczytaj audyt, plan, AGENTS i kontrakt wydania oraz sprawdź aktualny stan repo. Wykonuj etapy kolejno, klasyfikując ALREADY_CLOSED / DELTA_REQUIRED / SPEC_BLOCKED; wykorzystuj istniejące moduły i modyfikuj wyłącznie potrzebną deltę. Zacznij od fałszywej kwarantanny i znikających grup, następnie napraw retry oraz śledzenie rewizji, potem wydajność SQL i mediów. Rozszerz istniejące testy o wskazane kontrprzykłady i zweryfikuj rzeczywiste metody API. Izoluj testy od danych użytkownika. Nie kasuj ani nie resetuj biblioteki, blokad czy bazy. Uzupełniaj mapę wykonania dowodami; po etapie przechodź do kolejnego. Na końcu wykonaj wymaganą bramkę regresji i odbiór live, jeśli środowisko go umożliwia. Raportuj wyniki rzeczywiste i pozostałe luki, bez zastępowania testów zmniejszonym zakresem lub samymi kontrolami tekstu.
