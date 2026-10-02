# Archivebite — szeroki audyt i plan poprawek

Data: 2 października 2026. Wynik zadania: analiza i plan wdrożenia. Kod aplikacji i dane użytkownika nie zostały zmienione.

**Najpierw trzeba poprawić spójność magazynu użytkownika i synchronizacji oraz granicę wyszukiwania „Mój katalog”.** Następne prace dotyczą przełączania widoków, informacji o błędach, dostępności, zależności Windows i odbioru Desktop. Nie ma podstaw do przebudowy całego produktu: istniejący katalog, mechanizmy bezpieczeństwa i duża część regresji działają.

## Zakres i aktualność dowodów

Audyt objął kontrakt wydania, launchery, składanie `runtime_app`, magazyn użytkownika, katalog i rewizje, synchronizację konta, wyszukiwanie, feed/SSE, grupy, miniatury, modal, `/watch`, storyboard/QoS, sterowanie zadaniami, dostępność i CI. Łączy przegląd bieżącego kodu, istniejące regresje, kontrprzykłady backendu/Node oraz obserwacje w rzeczywistym Edge.

- Punkt bazowy Git: `6c2065b0d2eee9a0fce3986185ff1731e3869991`, z zastanymi zmianami w `static/video-modal.js`, `static/video-prefetch.js`, `static/watch.html`, `audit/regression_loading_frontend.cjs` i `audit/regression_timeline_browser.py`. Audyt dotyczy tego katalogu roboczego, nie samego commitu.
- [Manifest 130 istotnych plików](repair_results_broad_2026-10-02/source_manifest.json) potwierdził identyczność źródeł z kopią używaną w pełnej lokalnej próbie. Raporty historyczne nie były dowodem PASS.
- Testy wykonano przez `audit/implementation_runner.py`, w osobnych sanitarnych kopiach, bez danych konta, `.env.local`, credentials i cache użytkownika. Serwery fixture miały wyłączony lifespan; odpowiedzi konta i źródeł w próbach Edge były syntetyczne, a nagranie neutralne i lokalne.
- Środowisko: Windows, Python 3.14.4, SQLite 3.50.4, Node 24.19.0. CI deklaruje także Linux, Python 3.13 i Node 22. Lokalny `--full-ci` wykonuje komendy Python/Node z poszczególnych jobs na jednym interpreterze; nie odtwarza tej macierzy ani instalacji zależności z CI.
- Binding agent-system 1.0.1 był dostępny. Hash APP `53ef3adf72737c47d97c6f9e4d6921aa18d1ff7a1c6bc787f0757fd6b6704bfa` i WEB `9deb331f4ae073cefc60999eca90338078dfdfce44a7d4ceb3da73b5074e36c6` zgadzały się z kontraktem repo. Zastosowano właściwe reguły stanu, cyklu życia, wydajności, dostępności i dowodów; publiczne SEO i commerce są poza zakresem produktu.

Nie wykonano logowania do prawdziwego konta, testów zewnętrznych dostawców ani odbioru natywnego pywebview. Nie potwierdzono manualnie czytnika ekranu, high contrast ani całej macierzy zoom/reflow. Te zakresy pozostają do odbioru.

## Co działa według aktualnych kontroli

[Pełna próba lokalna](repair_results_broad_2026-10-02/test_results.json) zakończyła się wynikiem **85/86 wykonanych komend**; były to **49 różne komendy**. Jedna regresja integracyjna była niestabilna, opisana poniżej. Kontrole obejmujące następujące kontrakty przeszły:

- zaufany Host, Origin i token mutacji, ochrona proxy, rozdzielenie odczytu storyboardu od uruchamiania budowy;
- anonimowy start, bezpieczny konflikt portu, rollback zapisu i recovery magazynu;
- składanie właściwego runtime, grouped fast path, podstawowe QoS i współdzielenie pracy storyboardu;
- rewizje katalogu, wznowienia, wykrywanie rzeczywistego końca źródła, sparse pagination, współbieżne czytelniki i alokacja rewizji;
- paginacja, filtrowanie online, odzyskiwanie miniatur, promowanie członka grupy oraz potwierdzanie niedostępności;
- podstawowe scenariusze frontendowe, tożsamość modalu i część obsługi klawiatury odtwarzacza.

`python -m pip check` przeszedł, a wszystkie 24 jawnie przypięte pakiety miały wersje zgodne z lockiem. Dodatkowe [negatywne kontrole Git](repair_results_broad_2026-10-02/negative_controls.json) wykazały 0 nieprzykładowych trafień dla kontroli danych logowania i `taskkill` z Windows CI. To wynik tych konkretnych kontroli, nie pełny audyt sekretów lub zależności.

W osobnej próbie Edge środkowy klik otworzył właściwe `/watch/audit_card0` i zachował stronę główną. Nie wykryto błędów JavaScript typu `pageerror` w końcowym zestawie obserwacji przeglądarkowych.

## Priorytety

P1: poprawić przed kolejnym wydaniem — błędny wynik mutacji, niespójne dane lub naruszenie podstawowego zakresu produktu. P2: zaplanować w najbliższej serii poprawek — błędny przepływ, brak informacji, dostępność, wydajność lub powtarzalność. P3: dalsze usprawnienie po zamknięciu usterek.

| ID | Priorytet | Obszar wymagający poprawy | Dowód |
| --- | --- | --- | --- |
| F01 | P1 | Zdalne ulubione przełączają stan zamiast osiągać stan docelowy | Backend, odtworzone |
| F02 | P1 | Synchronizacja przywraca oczekujące usunięcie i nie zmienia wersji preferencji | Backend, odtworzone |
| F03 | P1 | Odczyt magazynu widzi mutację przed nieudanym zapisem | Backend, odtworzone |
| F04 | P1 | Restore ponownie używa lub cofa wersję preferencji | API/storage, odtworzone |
| F05 | P1 | „Mój katalog” automatycznie uruchamia operacje na źródłach | Edge + kod backendu |
| F06 | P2 | Filtr polubionych nie działa w wyszukiwaniu lokalnym | API + call site |
| F07 | P2 | Uzgadnianie ulubionych bada tylko pierwsze 1000 wpisów | Node, odtworzone |
| F08 | P2 | Wejście do panelu konta nie kończy poprzedniej generacji widoku | Node + Edge |
| F09 | P2 | Niepełne i mylące informacje o zapisie, koncie i synchronizacji | Edge + kod |
| F10 | P2 | „Cofnij przez 10 sekund” znika po około 4 sekundach | Edge |
| F11 | P2 | Ucieczka fokusu, brak etykiet i ignorowanie reduced motion | Edge + DOM/CSS |
| F12 | P2 | Synchroniczna praca konta blokuje pętlę ASGI | Pomiar backendu + kod |
| F13 | P2 | Lock pomija warunkowe zależności Desktop na Windows | Metadata zainstalowanego pakietu + lock |

### F01 — wynik zdalnej mutacji ulubionych może być przeciwny do żądanego

W `main.py:809–865` lokalny stan jest przełączany i zapisywany jako `desired`, ale `scraper.py:1569–1588` zawsze wysyła zdalne `toggleSave`. Dowolna niepusta odpowiedź HTML uznawana jest za `confirmed`, bez sprawdzenia końcowego stanu. Przy stanie początkowym „lokalnie brak, zdalnie jest” odpowiedź ma `success: true`, lokalnie film zostaje dodany, a zdalnie usunięty.

**Zmiana:** w istniejącym scraperze odczytać zdalny stan, porównać go z trwałym stanem docelowym i potwierdzić wynik operacji. Powiązać wynik z konkretną operacją/tożsamością, aby starsza odpowiedź nie potwierdzała nowszego zamiaru. Lokalna zmiana i zapis zamiaru zdalnego powinny mieć wspólną granicę trwałego zapisu w `UserStorage`; obecnie są osobnymi commitami.

**Odbiór:** zgodność lokalnego i zdalnego stanu dla wszystkich kombinacji początkowych, dwóch kart, ponowienia po timeout oraz spóźnionej odpowiedzi. Niepewne wykonanie pozostaje `unknown`. Wykorzystać obecny outbox i `VideoKey`.

### F02 — merge synchronizacji wskrzesza lokalnie usunięte ulubione

`storage.py:489–527` scala zdalne kolekcje przez sumę, bez uwzględnienia `remote_outbox`. Kontrprzykład: dodanie, usunięcie, zamiar `desired: false/status: failed`, następnie zdalna lista zawierająca film. Film wraca do ulubionych, zamiar nadal mówi „usuń”, a `preferences_version` pozostaje `2 → 2`.

**Zmiana:** podczas merge uwzględniać nierozstrzygnięte zamiary i chronić je przed spóźnionym snapshotem synchronizacji. Zmiana ulubionych lub blokad musi podnosić wersję projekcji. Potwierdzenie outboxu powinno wynikać z obserwowanego stanu zdalnego.

**Odbiór:** synchronizacja równoległa z dodaniem/usunięciem nie odwraca nowszej decyzji użytkownika; zmiana projekcji unieważnia stare feedy/SSE. Sprawdzić również synchronizację po restarcie i po lokalnym sukcesie ze zdalnym błędem.

### F03 — magazyn udostępnia dane przed trwałym commitem

Dekorator `storage.py:70–82` blokuje pisarza i poprawnie cofa pamięć po błędzie. Getterzy, m.in. `get_favorites`, `is_favorite` i `preferences_version`, nie korzystają z tej samej blokady. Podczas zatrzymanego zapisu czytelnik zobaczył nowy film i wersję `1`; zapis następnie zgłosił `OSError`, a rollback przywrócił pustą listę. Czytelnik zdążył więc otrzymać stan, który nie został zapisany.

**Zmiana:** odczyty i eksporty projekcji powinny korzystać ze spójnego snapshotu pod blokadą właściciela. Z jednego snapshotu wyprowadzać kolekcje, liczniki i wersję. Po dodaniu synchronizacji odczytu praca blokująca musi pozostawać poza pętlą ASGI — razem z F12.

**Odbiór:** czytelnik równoległy z błędnym zapisem otrzymuje ostatni zatwierdzony stan; nie widzi przejściowych elementów lub wersji. Zachować istniejący rollback, writer lock i recovery.

### F04 — odtworzenie kopii psuje tożsamość preferencji

`storage.py:677–711` podstawia dane z pliku razem z jego `preferences_version`. Odtworzenie innej zawartości dało `1 → 1`; odtworzenie starszej kopii dało `2 → 1`. Odpowiedź z poprzedniej generacji może nadal wyglądać na aktualną. Ponadto `main.py:2807–2832` opisuje zastąpienie danych jako `destructive: false`, choć UI już poprawnie prosi o potwierdzenie zastąpienia magazynu.

**Zmiana:** restore nadaje świeżą, trwałą generację większą od wersji bieżącej i importowanej. Unieważnia powiązane widoki i cache. Preview ma uczciwie opisywać zastąpienie oraz istotne różnice w kolekcjach i blokadach. Zachować istniejącą kopię ostatniego poprawnego stanu i recovery source.

**Odbiór:** ta sama/starsza/nowsza wersja w pliku zawsze daje nową generację; stare żądania nie publikują poprzedniej projekcji; błąd zapisu nadal przywraca pamięć i plik.

### F05 — lokalny zakres uruchamia automatyczną pracę zdalną

`static/search-results.js:206–208` renderuje standardowe karty i uruchamia warmup. `static/video-prefetch.js:732–779` sprawdza widoczne karty niezależnie od zakresu. W Edge po jednym lokalnym wyszukiwaniu zaobserwowano GET `/api/thumb` i dwa POST `/api/video/availability`. Ten endpoint (`main.py:2370–2389`) uruchamia resolver/proxy i próbę nagłówków strumienia. Sam endpoint `/api/search/local` jest lokalny, ale pełny przepływ widoku przekracza jego kontrakt.

**Zmiana:** przekazać jawny zakres do istniejącego renderowania/prefetchu. W lokalnym widoku automatyczne miniatury mogą korzystać z już lokalnie dostępnego cache, z czytelnym fallbackiem; sprawdzanie źródła i przygotowanie mediów zaczyna się przy świadomej akcji odtwarzania. Zachować walidacje bezpieczeństwa proxy.

**Odbiór:** od wyszukania do przewijania i paginacji przepływ „Mój katalog” nie inicjuje zdalnych żądań, SSE wyszukiwania, probe dostępności ani budowy mediów. Sprawdzić zimny i ciepły cache, z osobnym przypisaniem pracy niezależnych producentów katalogu. Kliknięte odtwarzanie nadal działa.

### F06 — lokalne wyniki ignorują filtr polubionych

`static/search-results.js` buduje tytuł zawierający „Tylko polubieni” lub „Bez polubionych”, ale lokalne żądanie nie przekazuje `author_filter`. `main.py:1443` i `CatalogService.search_local` go nie obsługują. Dla fixture dwóch filmów, z jednym ulubionym, `all`, `only_fav` i `exclude_fav` zwróciły HTTP 200 i identyczne dwa wyniki.

**Zmiana:** zastosować tę samą semantykę autorów/tożsamości co w istniejącym katalogowym feedzie; filtrować przed liczeniem i paginacją. Uwzględnić wersję preferencji. Jeśli dany filtr nie jest wspierany w zakresie, UI musi jawnie przedstawiać ten zakres dostępności.

**Odbiór:** wyniki, licznik i strony odpowiadają wybranemu filtrowi; blokady i oba źródła pozostają poprawne; dane pochodzą z jednej rewizji, bez pobierania źródeł.

### F07 — po błędzie ulubionych UI może fałszywie potwierdzić usunięcie

`static/favorites.js:180–207` pobiera tylko `page=1&per_page=1000`. Brak filmu na tej stronie jest uznawany za brak w całej kolekcji. Przy 1001 ulubionych i nieudanej mutacji starego filmu UI ustawiło `is_favorite: false` oraz wyświetliło „Usunięto … lokalnie”, mimo że backend zachował film.

**Zmiana:** uzgadniać stan jednej tożsamości przez lokalny odczyt oparty na `UserStorage.is_favorite`, bez zależności od pierwszej strony. Wynik niepewny ma pozostać niepewny. Zachować istniejącą obsługę lokalnego commitu i osobnego stanu zdalnego.

**Odbiór:** kontrprzykłady z 1001+ elementami, błędem trwałego zapisu, timeoutem po lokalnym commicie oraz tym samym ID w obu źródłach.

### F08 — opóźniony feed renderuje się wewnątrz panelu konta

`static/account.js:172–186` ustawia `mode='account'` i czyści DOM, ale nie wywołuje `beginViewRequest`. Kontroler i SSE poprzedniego widoku pozostają aktywne. Edge potwierdził: generacja `1 → 1`, panel konta widoczny, a opóźniona odpowiedź tworzy pod nim dwie karty i paginację. [Zrzut neutralnej fixture](repair_results_broad_2026-10-02/account_late_feed.png) pokazuje skutek.

**Zmiana:** wejście do konta przechodzi przez istniejącego właściciela generacji, anulowania, SSE i renderowania chunków. Kończy również właściwą pracę prefetchu.

**Odbiór:** wejście do konta podczas feedu, SSE wyszukiwania i renderowania dużej siatki nie pozwala starej pracy zmienić widoku. Powrót do poprzedniej zakładki działa i rozpoczyna poprawną generację.

### F09 — informacje o operacjach są niepełne lub mylące

Odtworzone w Edge: POST historii z 503 nie daje informacji ani w modalu (`static/video-modal.js:593–606`), ani w `/watch` (`static/watch.html:1331`); `/watch` ignoruje `remote_state: failed` i pokazuje tylko lokalny stan serca (`1354–1369`). Dodatkowo `accountStatusBadge` istnieje w HTML i jest aktualizowany przez `account.js`, ale brakuje jego bindingu w `app-context.js` — pozostaje „Stan konta: ładowanie”.

Przegląd kodu wykazał także: relogin zgłasza „Zsynchronizowano pomyślnie” przed zakończeniem synchronizacji uruchomionej w tle; ręczny sync używa domyślnego timeoutu 12 s, a bootstrap 120 s; pobieranie sekcji konta ma limit 15 stron przy komunikacie „wszystkich stron”; `/api/jobs` nie zachowuje końcowego wyniku synchronizacji, tylko bieżący stan blokady.

**Zmiana:** ujednolicić w obecnych modułach prezentację `local_committed`, stanu zdalnego, błędu i czasu oczekiwania. Dodać brakujący binding, wyraźny stan „jeszcze niesynchronizowane” i końcowy wynik istniejącego zadania. W przypadku historii pokazać pojedynczy komunikat o niezapisanym obejrzeniu z możliwością odzyskania. Synchronizacja powinna zgłaszać kompletność lub ograniczenie, zgodnie z faktycznie pobranymi stronami.

**Odbiór:** po 503 odtwarzanie działa, a brak zapisu jest widoczny; `/watch` rozróżnia zapis lokalny od zdalnego błędu; relogin i sync mają rzeczywiste fazy oraz wynik. Sprawdzić wolne źródło i więcej niż 15 stron konta. Nie utożsamiać tych statycznych problemów synchronizacji z pomiarem prawdziwego dostawcy — jego odbiór pozostaje do wykonania.

### F10 — czas dostępności „Cofnij” jest krótszy od deklaracji

`static/blocked-models.js:102–110` daje 10 s na cofnięcie, natomiast `static/toast.js` usuwa każdy toast po 4 s plus przejście. Po 4,5 s w Edge nie było przycisku „Cofnij”, choć okres operacji jeszcze trwał.

**Zmiana:** toast z akcją powinien mieć jawny okres dostępności zgodny z `expiresAt`; wspólny komponent powiadomień zachowuje dotychczasowe wywołania. Akcja i etykieta muszą wygasać razem.

**Odbiór:** cofnięcie dostępne w deklarowanym okresie, również klawiaturą; poprawne wygaszenie, błąd odblokowania i szybkie blokowanie kolejnych autorów.

### F11 — dostępność wymaga domknięcia w rzeczywistym UI

W managerze blokad Shift+Tab przeniósł fokus do `pageJumpBtn` za otwartym dialogiem. `aria-modal` nie zastępuje zarządzania fokusem; aktualna obsługa managera reaguje tylko na Escape. Podczas `prefers-reduced-motion: reduce` shimmer skeletonu nadal miał animację `skeletonShimmer`, 1,5 s, `infinite`. Oba pola skoku strony mają 0 etykiet i brak `aria-label`/`aria-labelledby`.

**Zmiana:** wspólny kontrakt fokusu dla warstw, powrót do elementu otwierającego, ograniczenie dekoracyjnych animacji według preferencji systemu i semantyczne etykiety istniejących pól. Sprawdzić te same wymagania na stronie watch i w modalu.

**Odbiór:** keyboard/focus w Edge i pywebview, ekran o minimalnym rozmiarze Desktop, powiększenie tekstu/reflow, reduced motion/high contrast oraz manualny czytnik ekranu. Dotychczasowy Node/VM test dostępności nie zamyka tych kryteriów.

### F12 — async endpointy konta wykonują synchroniczne odczyty i wzbogacanie

`main.py:726`, `763`, `771`, `870`, `913` wykonują odczyty/kopie/sortowanie lub `_enrich_videos` bez przeniesienia ich do workera. Podobnie część diagnostyki i listy blokad robi synchroniczną pracę SQLite. Fixture 3000 ulubionych: zwrócenie 280 elementów zajęło 75,6 ms; heartbeat zaplanowany na 5 ms wykonał się dopiero po 75,8 ms. Jest to lokalny pomiar jednego scenariusza, nie p95 prawdziwej biblioteki.

**Zmiana:** odczytać jedną spójną projekcję, a blokującą pracę uruchamiać poza pętlą ASGI, korzystając z istniejącego `asyncio.to_thread`/synchronicznych handlerów. Najpierw zmierzyć odczyty i wzbogacanie osobno; ograniczyć powtórne kopie całych kolekcji. Wdrażać razem z F03.

**Odbiór:** wolny storage/SQLite nie zatrzymuje heartbeatów i innych endpointów; strona nie kopiuje zbioru wielokrotnie. Pomiar cold/warm i równoległych żądań na ustalonym sprzęcie, dla 3k oraz większej kontrolowanej kolekcji; wynik musi zachować spójność danych i wersji.

### F13 — zależności Windows są poza zamrożonym lockiem

Metadata `pywebview==6.2.1` wymaga `pythonnet` na `win32`, ale `requirements.lock.txt` nie przypina `pythonnet`, `clr-loader`, `cffi` ani `pycparser`. Na obecnym hoście są zainstalowane odpowiednio 3.1.0, 0.3.1, 2.1.1 i 3.0; dlatego `pip check` przechodzi. Czysta instalacja może rozwiązać je do innych wersji.

**Zmiana:** ponownie wygenerować lock z `requirements.in`, z właściwymi zależnościami i markerami platform, przez przyjęty generator. Dodać kontrolę kompletności zamrożenia Windows i sprawdzić rzeczywiste wymagania WebView2/FFmpeg w dystrybucji.

**Odbiór:** czyste środowiska Windows 3.13/3.14 i Linux 3.13, zgodne rozwiązywanie zależności, `pip check`, start runtime oraz natywnego Desktop w obsługiwanym środowisku. Lock pozostaje plikiem generowanym.

## Dodatkowe ryzyka i usprawnienia

**R01 — P2, cykl życia Desktop i gotowość launcherów.** `desktop_app.py` uruchamia `uvicorn.run` w wątku daemon. Brakuje uchwytu do serwera oraz jawnej ścieżki zamknięcia okna, `should_exit` i oczekiwania na zakończenie lifespan. Kod nie zapewnia kontrolowanego opróżnienia producentów i workerów przy zamknięciu Desktop. Nie zmierzono pozostawania procesów FFmpeg — to wymaga próby w pywebview. Ponadto `run.py` otwiera browser po stałych 1,2 s, bez potwierdzenia gotowości, co przy dłuższym starcie może otworzyć stronę błędu połączenia. Launcher PowerShell ma już oczekiwanie na marker runtime. Plan: wykorzystać ten kontrakt gotowości w pozostałych launcherach, zamykać wyłącznie własny serwer i jego zadania, sprawdzić zwolnienie portu, locków, czytelników WAL i procesów dzieci; następnie ponowny start.

**R02 — P2, niestabilne i niepełne bramki odbioru.** Pełna próba: `regression_integration.py` uruchomiła trzy quick builders zamiast jednego. [Osobne ponowienie](repair_results_broad_2026-10-02_integration_recheck/test_results.json) przeszło. Test mockuje cache jako stale pusty i builder bez trwałej publikacji, więc późniejsze wejście może zlecić nową budowę. Przyczynę produkcyjną trzeba rozstrzygnąć z wiernym cache i kontrolowanym współbieżnym wejściem; sam wynik nie dowodzi trzech równoległych procesów FFmpeg w aplikacji.

Regresja Edge ma dodatkowo dwa problemy warunków testu: sprawdza pusty początkowy URL popupu po `wait_for_load_state`, a inna próba próbowała hover na automatycznie ukrytych kontrolkach. Poprawić oczekiwanie na docelowy URL i realne ujawnienie kontrolek. Zachować weryfikację zakodowanych klatek/PTS i kosztu źródła. Włączyć istniejącą neutralną regresję browserową do odpowiedniego CI; obecnie workflow jej nie uruchamia. Część testów VM zastępuje renderer/prefetch stubami, co wyjaśnia niewykrycie F05 i F08. Każda nowa regresja powinna zamykać wskazaną lukę.

**U01 — P3, odporność UI i wspólne style.** Ikony są pobierane z CDN; w zimnym/offline scenariuszu ze zablokowanym CDN znikają z kontrolek, co widać na neutralnym zrzucie. Dostarczyć potrzebne zasoby lokalnie. Przy naprawach watch przenosić używane przez oba playery kolory, promienie i podstawowe kontrolki do istniejącego design systemu; `watch.html` nadal zawiera dużo niezależnego CSS i kodu inline. Zakres refaktoryzacji ograniczyć do zmienianych miejsc i rzeczywistych dwóch odbiorców.

**U02 — P3, wydajność dużego wyszukiwania lokalnego.** `CatalogService.search_local` filtruje `lower(raw_json) LIKE '%…%'`, a następnie osobno liczy i pobiera stronę. Profilować przy reprezentatywnej dużej rewizji, cold/warm cache i kilku kolejnych wyszukiwaniach. Na podstawie planu zapytania i pomiarów zdecydować o potrzebnej indeksacji pól wyszukiwania. Obecny audyt nie zmierzył latencji dużej biblioteki i nie stwierdza jej awarii. Zachować SQLite jako jedynego właściciela katalogu.

## Plan wdrożenia i zależności

| Etap | Zakres | Właściciele i najmniejsza kompletna zmiana | Warunek zamknięcia |
| --- | --- | --- | --- |
| 1. Spójne dane użytkownika | F01–F04, F07, backend F12 | `UserStorage`, scraper/session i istniejące endpointy konta; atomowy stan/intencja, odczyt zatwierdzonego snapshotu, świeża generacja, uzgadnianie jednej tożsamości | Kontrprzykłady z tego audytu stają się regresjami i przechodzą; błędy zapisu, restart oraz dwie karty zachowują spójność |
| 2. Zakres wyszukiwania i nawigacja | F05, F06, F08 | `app-context`, `search-results`, `video-views`, istniejący grid/prefetch i lokalne query katalogu | Brak automatycznego zdalnego IO inicjowanego przez lokalny widok; poprawne filtry/liczniki; spóźnione odpowiedzi nie zmieniają aktywnej powierzchni |
| 3. Widoczny wynik i dostępność | F09–F11, część U01 | `account`, `favorites`, oba playery, manager blokad, wspólny toast i CSS; poprawne bindingi i stany | Wynik lokalny/zdalny i błąd historii są czytelne; undo dotrzymuje okresu; odbiór keyboard/focus/reduced motion/labels w realnym UI |
| 4. Desktop i powtarzalna instalacja | F13, R01 | Generator locka, istniejące launchery, właściciel własnego serwera Desktop | Czysta instalacja; docelowy pywebview; zamknięcie i restart zwalniają wszystkie własne zasoby bez ingerencji w obce procesy |
| 5. Odbiór wydania i dalsze optymalizacje | R02, pozostałe F12/U01/U02 | Obecne regresje/fixture/CI i profilowanie wskazanych ścieżek | Stabilne wymagane kontrole na dokładnym kandydacie, odpowiednia macierz CI, browser i Desktop, zapisane aktualne wyniki oraz jawne ograniczenia |

R02 należy rozstrzygnąć przed użyciem bramek do odbioru pierwszych zmian. F03 i przeniesienie blokujących odczytów z F12 tworzą wspólną deltę. F01 poprzedza finalną semantykę merge/outbox z F02. F04 musi być gotowe przed opieraniem lokalnych filtrów na wersji preferencji. F05/F06 powinny korzystać ze wspólnego jawnego zakresu, a F08 z już istniejącego właściciela generacji.

W obrębie etapów przygotowywać osobne, przeglądalne zmiany funkcjonalne. Nie wymieniać frameworka frontendu, storage, SQLite ani mechanizmu QoS. Każdy etap ma określony właściciel danych i przejście od akcji użytkownika do trwałego wyniku.

### Zestawy weryfikacji do ponownego użycia

- Etap 1: `regression_audit_fixes.py`, `regression_next_generation.py`, `regression_v42_full.py`, `regression_loading_frontend.cjs`, `regression_frontend.cjs`; dodać tylko brakujące granice odtworzone w probes.
- Etap 2: `regression_local_search.cjs`, `regression_search_filters.cjs`, `regression_search_pagination.py`, `regression_loading_frontend.cjs`, `regression_pagination.cjs`, `regression_v43_runtime_wiring.py`; realny browser dla IO i generacji widoku.
- Etap 3: `regression_accessibility.cjs`, `regression_block_no_reload.cjs`, obecna neutralna fixture przeglądarkowa; manualny odbiór wskazanych kryteriów. Sprawdzać modal i `/watch`.
- Etap 4: kontrola locka/pip, `regression_security_v42.py`, `regression_v42_full.py`, runtime wiring oraz próba pywebview w izolacji.
- Finał: właściwe required checks z `.github/workflows/ci.yml` i `RELEASE_REQUIREMENTS.md` na dokładnym kandydacie. `--full-ci` pomaga lokalnie, ale nie zastępuje macierzy CI ani natywnego Desktop.

Nie uruchamiać importów/lifespan prób na bibliotece użytkownika. Każda niezależna próba ma własne `ARCHIVEBATE_ISOLATED_CHECKOUT` i `ARCHIVEBATE_AUDIT_OUTPUT`. Test sieciowy pozostaje opt-in i nie powinien wykonywać mutacji prawdziwego konta jako domyślna regresja. Końcowe kontrole ponawiać po zmianie istotnych wejść, bez ponownego wykonywania już aktualnych dowodów bez powodu.

## Dowody i status odbioru

| Próba | Wynik | Znaczenie |
| --- | --- | --- |
| [Pełny lokalny runner](repair_results_broad_2026-10-02/test_results.json) | 85/86, exit 1 | Wymagana regresja integracyjna nie przeszła w tym przebiegu |
| [Ponowienie integracji](repair_results_broad_2026-10-02_integration_recheck/test_results.json) | 1/1, exit 0 | Potwierdza niestabilność pierwszego wyniku; nie zastępuje odbioru pełnego zestawu |
| [Kontrprzykłady backendu](repair_results_broad_2026-10-02/backend_observations.json) | Zebrane, runner exit 0 | Reprodukcje F01–F04 i pomiar F12 |
| [Kontrprzykłady Node](repair_results_broad_2026-10-02/frontend_observations.json) | Zebrane, runner exit 0 | Reprodukcje F07/F08 |
| [Lokalne filtry przez API](repair_results_broad_2026-10-02/local_filter_observations.json) | Zebrane, runner exit 0 | Reprodukcja F06 na osobnym katalogu fixture |
| [Obserwacje rzeczywistego Edge](repair_results_broad_2026-10-02/browser_observations.json) | Zebrane, runner exit 0 | F05/F08/F09/F10/F11 i działający środkowy klik |
| [Oryginalny browser test poza sandboxem](repair_results_broad_2026-10-02_browser_unrestricted/test_results.json) | 0/1, exit 1 | Zatrzymał się przy początkowym pustym URL popupu |
| [Dodatkowa próba z istniejącymi przypadkami timeline](repair_results_broad_2026-10-02_browser_probes_v2/test_results.json) | 0/1, exit 1 | Hover na niewidocznych kontrolkach; R02 pozostaje otwarte |
| Zgodność wersji z 24 pinami i `pip check` | PASS lokalnie | Pozostają nieprzypięte zależności Windows z F13 |
| Desktop, czytnik ekranu, pełna macierz CI, prawdziwi dostawcy | NOT_RUN | Wymagają odrębnego odbioru we właściwych warunkach |

Powodzenie skryptów probes oznacza poprawne zebranie kontrprzykładów, a nie poprawność aplikacji. Pierwsze nieudane uruchomienie własnego probe miało błąd składni/stubu i zostało poprawione; te błędy harnessu nie są usterkami produktu. Pierwsza próba Playwright w sandboxie nie miała prawa uruchomić procesu; właściwe obserwacje zebrano po uruchomieniu poza sandboxem.

Skrypty reprodukcji są zapisane przy JSON-ach: `probe_backend.py`, `probe_frontend.cjs`, `probe_local_filters.py`, `probe_browser.py`. Powtarzać je przez istniejący runner na świeżej kopii i z osobnym katalogiem wyników.

**Status:** audyt i plan ukończone. Gotowość do wydania nie jest potwierdzona: pozostają wskazane P1, niestabilna kontrola integracyjna, niewydolne warunki browser gate oraz brak odbioru Desktop. Wdrożenie poprawek jest następnym rodzajem zadania.
