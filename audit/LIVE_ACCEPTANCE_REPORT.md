# Archivebite — LIVE ACCEPTANCE TEST

Data testu: 2026-09-13 (Europe/Warsaw)
HEAD podczas testu: `d122a2208940366f3c749328886d58819f90ee2d`
Zakres: kontrolowana weryfikacja istniejącej implementacji, bez rozszerzania funkcji.

## Zasady bezpieczeństwa

Nie wykonywano zdalnych operacji favorites/block/save/sync, logowania, relogowania ani czyszczenia historii. Nie resetowano, nie usuwano i nie przywracano `data/user_store.json`, backupów, `data/catalog.db`, plików WAL/SHM ani `data/model_tags.json`. Nie wykonywano push, merge, tag ani operacji porządkujących Git.

Użyto wyłącznie odczytów providerów, publicznych metadanych, lokalnych endpointów, odtwarzania/range/thumbnail/storyboard oraz kontrolowanego startu, restartu i zatrzymania procesu. Dla testu uruchomieniowego ustawiono tylko procesowy override `DEEP_ARCHIVEBATE_DELAY=0.75`; wartość bazowa środowiska wynosiła `0.15`. Nie zmieniano konfiguracji repozytorium.

## Macierz akceptacyjna

| Area | Status | Evidence | Limitation |
|---|---|---|---|
| Archivebate provider | PARTIAL | HTTPS home `200`; parser Livewire zwrócił 36 kart; profil/watch/details działały, a thumbnail był `200 image/webp`. | Publiczny endpoint wyszukiwania zwrócił `403`; dla jednego publicznego direct-media range upstream zwrócił `403`. Nie traktuję tego jako izolowanej regresji aplikacji. |
| Camwhores provider | PASS | Odczyt latest zwrócił 29 elementów; profil/watch/details i thumbnail `200 image/jpeg` działały. | Direct-media zwrócił bezpieczny `302`; nie śledzono niezweryfikowanego redirectu do dalszego testu range. |
| Deep Archivebate | PARTIAL | Proces deep wznowił pracę po restartach. Stan SQLite wzrósł z 18 588 do 18 708 modeli, z 977 do 1 033 kompletnych modeli i z 57 715 do 58 441 elementów deep. Aktywna rewizja końcowa `100` była kompletna. | Pozostały `13 583` elementy pending, `406` split i `3` wpisy error kolejki; w modelach występowało `7` zapisanych błędów. Nie wykonywano resetu checkpointów. |
| Real CDN/media | PARTIAL | Real details `200`; thumbnail `200`; real stream z `Range: bytes=0-1023` zwrócił `206`, `video/mp4`, 1024 bajty. | Jeden inny, krótki/starszy card zakończył playback błędem, a Camwhores direct-media pozostał na redirect. |
| Storyboard quick | PASS | Demand/start zwróciły `200`; storyboard przeszedł `building → ready`; obraz quick zwrócił `206 image/jpeg` z poprawnym nagłówkiem JPEG i immutable cache headers. | Wynik dotyczy zweryfikowanego realnego elementu i trybu quick. |
| Storyboard segment | PARTIAL | Demand/start działały i stan przechodził do `building`. | Segmenty 0 i 1 przeszły do `error`: FFmpeg nie uzyskał poprawnych klatek; ręczna diagnostyka wskazała upstream `5XX` dla sekwencyjnego streamu HTTP. W jednym przebiegu sandbox dodatkowo wystąpił `WinError 5` dla katalogu tymczasowego. |
| Browser online/home | PASS | Retest po ukierunkowanej poprawce: realny frontend pokazał 280 kart, licznik `69 862` nagrań i 250 stron; nie pokazał błędu feedu ani `Błąd sesji`. Bezpośrednio na realnym katalogu pierwszy payload miał 16 kart (`page_complete=false`), a istniejący stream dostarczył 280 kart (`page_complete=true`). | Pierwotny przebieg miał 12–27 s dla pełnej strony; po poprawce pełna materializacja pozostaje kosztowna, ale nie blokuje pierwszego użytecznego renderu. Ograniczenia provider/CDN opisane w pozostałych wierszach pozostają bez zmian. |
| Browser local/UI | PASS | Lokalny search zwrócił 4 wyniki dla kontrolnego zapytania; realny search zwrócił 617 wyników. Sprawdzono pagination 1→2, grupowanie, otwarcie modala, playback realnego video oraz klawisze Enter i Escape. | Nie uznano pojedynczego błędu starego carda za regresję całej ścieżki, ponieważ niezależny realny card osiągnął `readyState=4`, odtwarzał się i nie miał błędu video. |
| pywebview/Desktop | BLOCKED_ENVIRONMENT | Uruchomiono `desktop_app.py`; warstwa CUA zwróciła `apps=[]`, a po zakończeniu procesu nie było listenera na porcie 8000. | Brak dostępnego, kontrolowalnego natywnego okna w tym środowisku uniemożliwił pełny test pywebview. |
| Lifecycle start/stop | PASS | Wielokrotny kontrolowany start backendu, odpowiedzi lokalnych endpointów `200`, następnie zatrzymanie procesu. Nie zabijano procesów obcych ani nie zwalniano zajętego portu siłowo. | W pierwszym przebiegu odziedziczony proxy `127.0.0.1:9` blokował providerów; po usunięciu proxy wyłącznie z procesu testowego odczyty providerów działały. Przy jednym shutdownie pojawił się przejściowy log race `catalog is shutting down`. |
| Restart/resume | PASS | Po restartach wznowiły się indeksowanie/deep; rewizje wzrosły do aktywnej kompletnej `100`, a liczniki deep rosły monotonicznie. Nie zaobserwowano resetu lokalnego stanu. | Nie wykonywano testu utraty zasilania ani brutalnego kill -9; zakres obejmuje kontrolowany restart procesu. |
| Soak 30–60 min | PARTIAL | Wykonano serię wielokrotnych odczytów, startów/restartów, operacji UI i generowania storyboardu podczas jednej sesji acceptance. | Pełny formalny soak 30–60 minut nie został wykonany w tym przebiegu. |
| Keyboard/accessibility | PARTIAL | AX tree zawierał nazwy/role dla głównych kontrolek; sprawdzono Enter w wyszukiwaniu i Escape do zamknięcia modala. | Nie wykonano pełnej sesji screen readera, kontrastu, reflow ani niezależnego audytu WCAG. |
| Remote account sync | BLOCKED_FOR_SAFETY | Nie otwierano Panelu Konta, nie wykonywano login/relogin ani synchronizacji zdalnej. Odczyt lokalnego statusu wskazywał skonfigurowane konto, bez drukowania danych wrażliwych. | Weryfikacja wymagałaby zdalnej synchronizacji/mutacji poza dozwolonym zakresem. |
| Security/data boundaries | PASS | Nie wywołano zdalnych favorites/block/save/sync; nie czyszczono historii; nie resetowano checkpointów. `data/model_tags.json` pozostał zastaną zmianą użytkownika i nie był stage'owany. Oba backupy user store nadal istnieją. | Pełny test konta i zdalnej mutacji pozostaje celowo niewykonany. |

## Stan przed i po

### Git

Stan bazowy i końcowy wskazywały ten sam HEAD: `d122a2208940366f3c749328886d58819f90ee2d`. Przed testem repo miało zastaną zmianę `data/model_tags.json` oraz lokalny `ARCHIVEBITE_NEXT_GENERATION_REVIEW.md`; po utworzeniu tego raportu dochodzi wyłącznie nowy, niestage'owany plik raportu.

Końcowy `git status --short`:

```text
 M data/model_tags.json
?? ARCHIVEBITE_NEXT_GENERATION_REVIEW.md
?? audit/LIVE_ACCEPTANCE_REPORT.md
```

Nie powstał nowy commit i nie wykonano push. `data/model_tags.json`, `data/user_store.json`, backupy, katalogowa baza danych oraz dane prywatne nie weszły do żadnego commita.

### Lokalne dane i monotoniczność

Odczyt agregatów `data/user_store.json` przed i po teście był zgodny: schema `2`, preferences `0`, favorites `710`, history `624`, following `758`, blocked models `1941`, remote outbox `0`. `data/user_store.json.last-good.bak` i `data/user_store.json.before_block_restore_20260911_024720.bak` pozostały obecne.

Snapshot SQLite przed testem miał aktywną kompletną rewizję `20` z `85 793` video. Końcowy odczyt read-only miał aktywną kompletną rewizję `100` z `86 519` video. Najnowsze utrwalone przebiegi providerów Archivebate/Camwhores pozostawały niekompletne, odpowiednio na cursorze `194`; jest to ograniczenie postępu, nie wykonany reset.

Podczas jednego odczytu endpointu statusu pojawiły się niższe liczniki account summary niż w bezpośrednim lokalnym odczycie user store. Nie wykonywano synchronizacji zdalnej w celu ich uzgodnienia, więc rozbieżność pozostaje jawnie nierozstrzygnięta i nie jest przypisywana lokalnej mutacji.

## Błędy, regresje i decyzja o poprawce

Pierwotnie zaobserwowano: blokujący proxy w pierwszym środowisku procesu, upstreamowe `403`/`302`, timeout feedu przy dużym katalogu, brak klatek dla storyboard segment oraz błędy kolejki deep. Po izolacji środowiska i ponownych przebiegach quick storyboard, realny stream, lokalny search i restart/resume działały.

Ukierunkowana diagnoza potwierdziła odizolowaną deltę produktu dla `Browser online/home`: aktywna ścieżka synchronnie materializowała i wzbogacała pełne 280 kart przed otwarciem istniejącego SSE, a lokalny status filtrował kolekcje przez 1 941 nazw blokowanych w koszcie O(n×m). Zastosowano małą poprawkę opartą na istniejącym kontrakcie: ograniczony pierwszy batch, `page_complete`, dalszy istniejący stream, zachowanie widocznego stanu przy błędzie odświeżenia, szybsza projekcja blokad oraz osobny limit handshake'u home; globalny timeout API pozostał bez zmian. Szczegóły i dowody retestu są w aneksie poniżej. Raport pozostaje lokalnym artefaktem audytu i nie jest automatycznie dodawany do commita poprawki.

## Aneks — ukierunkowana naprawa Browser online/home

### Root cause i decyzja

Nie stwierdzono race/cache/revision jako źródła opóźnienia. Odczyt SQLite nie czekał na writer; koszt był przed pierwszą odpowiedzią w synchronicznym zapytaniu i wzbogacaniu 280 kart (`_enrich_videos`). Istniejący `/api/feed/stream` był otwierany dopiero po zakończeniu tego pełnego `/api/feed`, więc frontendowy limit 12 s zamieniał zdrową, późną odpowiedź w fałszywy stan błędu. `/api/status` był lokalny, ale przy realnej projekcji 1 941 blokad wykonywał wielokrotne liniowe porównania.

Zmieniono wyłącznie tę ścieżkę: `/api/feed` może dostać `initial_items` (maksymalnie 16) i zwraca kompletne counts/revision oraz jawne `page_complete=false`; istniejący `/api/feed/stream` pobiera potem pełną stronę. Frontend zachowuje ochronę generacji, nieaktualnych odpowiedzi i rewizji, a błąd transportu po widocznym batchu zachowuje użyteczne karty i oferuje retry. Limit 30 s jest route-specific dla handshake'u home, nie jest zmianą globalnego klienta API. Status używa jednego zbioru znormalizowanych blokad, a timeout klienta nie jest już prezentowany jako dowód błędu sesji.

### Weryfikacja po poprawce

- Targeted frontend: `regression_loading_frontend.cjs`, `regression_catalog_partial_frontend.cjs`, `regression_frontend.cjs`, `regression_home_loading.cjs` i `regression_package_b_frontend.cjs`: **PASS**.
- Targeted backend/feed: `regression_feed.py`, `regression_feed_errors.py`, `regression_loading.py`, `regression_package_b.py`, odczyt bez blokady writera i monotoniczność rewizji: **PASS**. `regression_feed.py` wymagał uruchomienia poza sandboxem Windows z powodu ograniczenia tworzenia tymczasowych plików.
- Active-path fixture `127.0.0.1:8765`: 64 syntetyczne karty, poprawne liczniki, tryb anonimowy i brak fałszywego błędu feedu: **PASS**.
- Controlled live retest na realnym katalogu, bez panelu konta, logowania, synchronizacji, favorites/block/save ani operacji na danych użytkownika: **PASS**. Pierwszy payload `16` kart / około `159 ms` po rozgrzaniu; istniejący stream `280` kart / około `567 ms`; przeglądarkowy home zakończył się `280` kartami bez błędu. Lokalny `/api/status` po optymalizacji mierzył około `52 ms` zamiast wcześniejszych około `10,6 s`.

Nie zmieniono statusów innych obszarów macierzy. Nie wykonano push, merge ani tag; `data/model_tags.json`, `data/user_store.json`, backupy, `catalog.db` oraz WAL/SHM pozostały poza zakresem zmian.
