# Plan naprawy regresji: stabilna lista, pełna paginacja, szybki film i dokładny podgląd

Data: 2026-09-08. Podstawa: pięć zgłoszonych objawów, załączone logi i aktualny kod po wdrożeniu poprzednich optymalizacji. To plan dla agenta wdrażającego; aplikacja nie została zmieniona w tej analizie.

**Cel:** ekran nie migocze podczas doładowywania, od początku pokazuje liczbę stron i nagrań ostatniego kompletnego katalogu, odtwarzanie ma pierwszeństwo przed pracami w tle, a obraz pod kursorem odpowiada wybranej sekundzie filmu.

Poprzedni kierunek z licznikiem „poznanych pozycji” i ujawnianiem kolejnych stron nie spełnia oczekiwania pełnej paginacji. Nie wracać jednak do arbitralnych liczb 70 000/50 stron. Potrzebny jest trwały indeks katalogu, niezależny od pobierania filmów i od aktualnie wyświetlanej strony.

## 1. Ustalenia — punkt wyjścia, bez ponawiania całego audytu

| ID | Problem i dowód | Miejsce |
|---|---|---|
| R1 | Każda aktualizacja feedu wywołuje `renderVideoGrid()`. Funkcja przerywa preview, odłącza observer i wykonuje `innerHTML = ''`, po czym odtwarza wszystkie karty. To mechanizm powodujący migotanie i utratę stanu istniejących kart. | [app.js:2162](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/static/app.js:2162), [app.js:3451](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/static/app.js:3451) |
| R2 | `Snapshot.read()` oblicza liczbę stron z już pobranych pozycji i potencjalnej kolejnej strony. `complete` dotyczy zapełnienia strony, nie całego katalogu. UI ukrywa przycisk „Ostatnia” przy `feedHasMore`. | [feed_service.py:47](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/feed_service.py:47), [app.js:2710](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/static/app.js:2710) |
| R3 | Statystyka katalogu jest nadpisywana `data.known_count` po każdej partii. To licznik stopniowo pobieranych rekordów, a nie wolny odczyt gotowej pełnej liczby. `/api/stats` ma nadal osobne estymacje. | [app.js:2168](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/static/app.js:2168), [main.py:1305](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/main.py:1305) |
| R4 | Logi pokazują powtarzane odmowy połączenia z tym samym hostem filmu i timeouty stron profili/Livewire. Proxy odświeża adres po wybranych kodach HTTP, ale nie po `ConnectionError`. Adapter powtarza próbę tego samego połączenia; kolejne żądania playera/generatora mogą powtórzyć cały cykl. | [main.py:1011](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/main.py:1011), [main.py:1017](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/main.py:1017), [client.py:21](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/client.py:21) |
| R5 | Storyboard jest już podłączony, ale ma 8 klatek QUICK i maksymalnie 48 FULL. `applyFrame()` wybiera jedną z nich. Dla godziny nagrania FULL to średnio jedna klatka co 75 s, QUICK co 450 s. Nie jest to podgląd bieżącej sekundy. | [storyboard_service.py:18](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/storyboard_service.py:18), [youtube-storyboard.js:223](C:/Users/Skarabeusz/Downloads/Archivebate_TIMELINE_INSTANT_V3/static/youtube-storyboard.js:223) |

**Sprawdzenie izolowane wykonane podczas tej analizy:** snapshot 280 pozycji z niezakończonym źródłem zwrócił `last_page=2`, `complete=true`, `total_is_estimate=true`. Symulowana odmowa połączenia w aktualnej funkcji proxy dała HTTP 500 i zero wywołań odświeżenia resolvera. Bez zewnętrznych połączeń i bez zapisu danych konta.

**Granice ustaleń:** około 3 s do startu to pomiar zgłoszony przez użytkownika; nie rozdzielono jeszcze tych sekund na etapy. WinError 10061 nie dowodzi wygaśnięcia URL — może oznaczać niedostępny serwer. Nazwy profili takie jak „hot”/„squirt” w logu uzasadniają sprawdzenie pochodzenia identyfikatorów, ale same nie dowodzą błędu parsera. Nie uruchamiano ponownie całego zestawu wcześniejszych testów ani rzeczywistych filmów.

## 2. Kontrakt produktu, który agent ma zachować

- Zwykła paginacja po 280 kart, z „Strona X z Y”, skokiem do numeru, przyciskiem ostatniej strony oraz stałym licznikiem katalogu. Nawigacja dostępna również nad siatką; nie zależy od dotarcia do końca scrolla.
- Liczba Y i liczba nagrań odnoszą się do tej samej kompletnej rewizji katalogu, źródła oraz filtrów. W grupowaniu osobno `video_count` i `group_count`; stronę liczymy z liczby grup, nie wszystkich nagrań.
- Katalog oznacza kompletny, lokalnie zindeksowany zakres wybranych źródeł według ostatniej zakończonej synchronizacji. Nie utożsamiać go z liczbą filmów na całym świecie ani ze sztuczną estymacją.
- Pierwsze uruchomienie bez kompletnego indeksu jest wyjątkiem: pokazać dostępne karty i jeden stan „Przygotowanie katalogu”, a postęp osobno. Dokładnej liczby wszystkich filtrowanych wyników nie da się uzyskać natychmiast bez indeksu lub wiarygodnych danych źródła. Po zakończeniu tej inicjalizacji liczby są trwałe i dostępne od startu.
- „Podgląd bieżącej sekundy” oznacza rzeczywisty kadr o znanym czasie, z docelowym odstępem próbek ≤1 s; płynne przesuwanie wskaźnika nie zastępuje aktualizacji obrazu. Krótki zwiastun bez mapowania do filmu nie spełnia wymagania.

## 3. Pakiety wdrożenia i odbiór

### A. Usunięcie migotania — najpierw, niezależnie od indeksu

Naprawia R1. Zmiany ograniczyć do aktualizacji siatki i jej stanu.

1. Rozdzielić `replaceView()` dla rzeczywistej zmiany strony/filtra od `reconcilePage()` dla kolejnej porcji tego samego widoku. Przechowywać mapę `source:id → element`; w grupowaniu stabilny klucz grupy zgodny z backendem.
2. Dla istniejących kart aktualizować tylko zmienione pola. Dopisywać nowe elementy w `DocumentFragment`; usuwać wyłącznie rzeczywiście usunięte rekordy. Aktualizować model powiązany z kartą, żeby callback kliknięcia nie korzystał ze starych danych grupy/ulubionych.
3. Porcja SSE nie może wykonywać `innerHTML=''`, resetować kontrolera całej siatki, ponownie ustawiać niezmienionego `img.src`, zatrzymywać aktywnego hovera ani odtwarzać animacji wejścia istniejących kart. Pusta początkowa odpowiedź nie usuwa gotowego widoku tej samej strony.
4. Łączyć aktualizacje w jedną operację na klatkę UI i ignorować już zastosowane rewizje. Po pełnej stronie nie aktualizować DOM, jeśli zmienił się tylko postęp indeksowania. Zachować pozycję scrolla, fokus i kotwicę aktualnego kafelka.
5. Skeletony stosować w pustych miejscach podczas pierwszego ładowania. Odświeżenie już pokazanej strony sygnalizować małym wskaźnikiem, bez zasłaniania treści. Rezerwować wymiary obrazów i panelu statystyk.

**Odbiór:** fixture wysyła 20 porcji co 100 ms, w tym duplikaty rewizji i aktualizację jednej karty. Pierwsze elementy zachowują tożsamość DOM, `img.src` i aktywny podgląd. Zero wymian całej siatki podczas porcji; brak zauważalnego błysku w oknie desktopowym. Zmiana filtra nadal unieważnia poprzednie odpowiedzi.

### B. Pełna paginacja i licznik z trwałego indeksu

Naprawia R2/R3. Miejsca: `feed_service.py`, endpointy feedu/statystyk, paginacja UI. Zalecane wydzielenie `catalog_service.py` i indeksu SQLite; biblioteka standardowa wystarczy. Magazynu konta nie migrować przy tej zmianie.

1. Indeks przechowuje minimalne metadane listy: kanoniczny klucz, źródło, ID, autor, czas publikacji, czas trwania, poster, URL strony i przynależność do rewizji. Dodatkowo stan przebiegu po stronach każdego źródła oraz `complete/failed/updated_at`. Budować go ze stron listujących filmy, bez pobierania detali, strumieni i pełnych profili tylko w celu policzenia katalogu.
2. Zachować surowy cache i poprawioną deduplikację. Zaimportować dostępne wpisy jako częściowy indeks; nie oznaczać ich jako kompletnych. Zweryfikować rzeczywisty koniec paginacji dostawcy. Timeout, błąd parsera i pusty dokument błędu nie mogą oznaczać końca źródła.
3. Budować kolejną rewizję w tle i publikować ją atomowo po zakończeniu. Przy awarii zachować poprzednią pełną rewizję i jej licznik, umożliwić wznowienie. Dostępny stary katalog pokazywać natychmiast ze znacznikiem czasu aktualizacji.
4. Jedno zapytanie/ta sama definicja filtrów zasila licznik i listę. `page_count=ceil(item_count/280)`. Przewidzieć źródło, zablokowanych, ulubionych i grupowanie. Kolejność stabilna: data publikacji malejąco oraz kanoniczny klucz jako rozstrzygnięcie remisów; nie sortować źródeł według szybkości odpowiedzi sieci.
5. API strony zwraca wspólny kontrakt: `catalog_revision`, `page`, `page_size`, `video_count`, `group_count`, `page_count`, `items`, `catalog_complete`, `updated_at`. Postęp aktualizacji ma osobny kanał/pole, np. `indexing_progress`, i nie nadpisuje licznika katalogu.
6. UI zachowuje rewizję podczas nawigacji, dzięki czemu „Ostatnia” i skok do strony działają od razu. Nową rewizję zastosować przy świadomym odświeżeniu lub bezpiecznym przejściu; nie przetasowywać aktywnej strony w trakcie oglądania. Zmiana preferencji przelicza lokalną projekcję i liczbę stron.
7. Oddzielić pobieranie kart strony od lazy-loading obrazów. Nawet przy niepobranych miniaturach paginacja ma pełne liczby. Przy dwóch źródłach z niepełną synchronizacją nie publikować częściowej sumy jako pełnego katalogu.
8. Indeksowanie w tle ma małą współbieżność i ustępuje aktywnemu playerowi. Nie wykonywać pełnego skanowania przy każdym starcie ani po każdym przewinięciu. Ponowne odświeżanie korzysta z kursorów, cache i zapisanej rewizji.

**Odbiór:** kompletny fixture 721 filmów pokazuje od startu 3 strony, rozmiary 280/280/161 i działający skok do ostatniej. Wynik identyczny po restarcie i odłączeniu źródła. Aktualizacja w tle nie zmienia licznika przed publikacją nowej rewizji. Powtórzyć dla filtrów i grup, sprawdzając zgodność sum z rekordami. Na fixture 70 tys. metadanych mierzyć 20 odczytów pierwszej strony z licznikami; cel roboczy p95 ≤200 ms na lokalnym komputerze.

### C. Opanowanie powtórzeń połączeń i skrócenie startu filmu

Naprawia R4. Miejsca: `main.py` — proxy, resolver, sesja HTTP; `client.py` — polityka retry; oba odtwarzacze i scheduler. Nie przypisywać całych zgłoszonych 3 s jednej przyczynie bez pomiaru.

1. Dodać pomiar jednej sesji otwarcia: kliknięcie → wybór/resolving URL → połączenie upstream → pierwszy bajt → metadane → pierwsza zaprezentowana klatka. Rejestrować powód, priorytet i właściciela żądania: player, timeline, karta, indeks, profil. Logować ID/host i kategorię błędu; nie powielać podpisów dostępu URL ani danych konta.
2. Zastosować jawny wspólny budżet prób dla jednego otwarcia, zamiast mnożenia retry adaptera przez ponowienia playera i ekstrakcje klatek. W krytycznej ścieżce aktywnego filmu ograniczyć automatyczne retry adaptera; krótkie rozdzielone connect/read timeouty dobrać do pomiarów, nie obniżać ich globalnie dla wszystkich zadań.
3. Obsłużyć odmowę połączenia i błędy połączenia przed rozpoczęciem body: dla żądania po ID najwyżej raz odświeżyć resolver przez istniejący singleflight. Jeśli nowy URL jest identyczny albo nadal niedostępny, zakończyć kontrolowanym błędem 502/504 i przyciskiem ponowienia. Nie traktować błędu transportu jako pewnego dowodu wygaśnięcia.
4. Dodać krótki, ograniczony stan awarii URL/hosta oraz współdzielenie wyniku nieudanego odświeżenia. Dziesięć Range/generatorów nie może uruchamiać dziesięciu cykli naprawy. Po czasie przerwy zezwolić na jedną próbę kontrolną. Błędy konkretnego zasobu nie powinny bezpodstawnie blokować całego źródła.
5. Oddzielić świeżość metadanych od adresu odtwarzania. Respektować wiarygodny termin ważności, jeśli rozpoznaje go adapter danego dostawcy. Nie zakładać uniwersalnej semantyki dowolnego parametru `e`. Przy nowym źródle strumienia unieważnić powiązane redirecty i oczekujące zadania z nieaktualnym adresem.
6. Wstrzymać nowe zadania indeksowania, profili, ekstrakcji i spekulacyjnych miniaturek podczas startu i `waiting/stalled`. Samo `playing` nie wystarcza do puszczenia całego tła: wymagać zapasu bufora wokół aktualnej pozycji, np. 5 s jako ustawienia początkowego. Żądania już rozpoczęte muszą mieć ograniczony czas życia.
7. Przygotowywać co najwyżej 1–2 prawdopodobne detale na intencję użytkownika: dłuższy hover/fokus/przycisk następnego filmu. Ewentualne pobranie początkowego zakresu testować tylko dla aktywnego kandydata, z twardym limitem bajtów i przerwaniem, jeśli źródło ignoruje Range. Nie preładować pełnych filmów z całej siatki.
8. Po ustaleniu źródła żądań profili ograniczyć spekulacyjne wywołania Livewire i walidować identyfikator autora na podstawie linku profilu/znanego pola. Nie tworzyć arbitralnej listy zakazanych nazw na podstawie kilku słów z logu.

**Odbiór:** symulowana odmowa połączenia daje najwyżej jedno współdzielone odświeżenie, brak nieskończonych prób i poprawny komunikat. Zmiana A→B anuluje intencję A. Dla dostępnego rzeczywistego filmu wykonać 20 porównań cold/warm i raportować p50/p95 pierwszej klatki. Cel roboczy ciepłego, wcześniej przygotowanego filmu: p95 ≤1 s przy sprawnym źródle; jeśli niemożliwe, wskazać zmierzony etap zewnętrzny. Nie zaliczać wcześniejszego schowania spinnera jako przyspieszenia.

### D. Podgląd rzeczywistej sekundy zamiast 48 kadrów całego filmu

Naprawia R5. Zależność: kontrola obciążenia i awarii z C. Miejsca: `storyboard_service.py`, `static/youtube-storyboard.js`, `static/player-core.js`, modal i `/watch`.

1. Zachować rzadki QUICK jako wstępny podgląd, ale nie uznawać go za realizację wymagania dokładnej sekundy. Dodać gęsty cache: segmenty osi czasu po 30–60 s, z klatkami co najwyżej co 1 s i manifestem rzeczywistych znaczników czasu. Opcjonalne 2–4 klatki/s dopiero po sprawdzeniu kosztu i oczekiwanego efektu ruchu.
2. Gdy dostępny jest prawidłowo zmapowany podgląd dostawcy, użyć go. W przeciwnym razie przygotowywać aktywny segment wokół kursora i najbliższy sąsiedni. Dla już oglądanych filmów segmenty zachować na dysku; odtwarzać gotowe bez łączenia z dostawcą.
3. Ekstrakcję segmentu wykonywać jednym odczytem/dekodowaniem przedziału, a nie osobnym procesem i nowym zdalnym seekiem na każdą sekundę filmu. Najpierw prototyp na neutralnym filmie i pomiar liczby requestów/bajtów, także dla pliku wymagającego odczytu końcowych metadanych. Ograniczyć czas, rozmiar i liczbę procesów; dla zdalnego źródła nie generować od razu całego wielogodzinnego materiału.
4. Renderer wybiera najbliższą klatkę po `times[]`, zamiast `floor(ratio*frame_count)` zakładającego równy i gęsty rozkład. Ruch kursora i tekst aktualizuje w `requestAnimationFrame`. Kolejka przygotowania zachowuje najnowszy cel; opuszczenie timeline usuwa niepotrzebny popyt.
5. Gotowy segment daje zmianę obrazu bez Range i bez FFmpeg. Dla niegotowego segmentu można użyć jednego kontrolowanego seekera dokładnego wideo, który czeka na faktycznie przedstawioną klatkę i nie konkuruje bez ograniczeń z głównym filmem. Wynik starego seeka nie może być opisany nową sekundą jako aktualny.
6. Przy zimnym filmie bez gęstego cache jawnie pokazać przygotowanie dokładnego podglądu, zachowując dostępny obraz. Nie udawać dokładności przez interpolację powielonych klatek lub przez przewijanie niezmapowanego zwiastuna. Natychmiastowy dokładny kadr dowolnej sekundy nie jest gwarantowalny z nieprzygotowanego, wolnego zdalnego źródła.
7. Jeden wspólny mechanizm w modalu i `/watch`, współdzielone manifesty również dla kafelków. LRU według bajtów dla segmentów RAM/dysk, wersja schematu i atomowy zapis. Nie kasować działających starych podglądów ani generować segmentów dla każdego kafelka.

**Odbiór:** neutralny film zawiera duży numer czasu i zmieniający się obraz co sekundę. Przeciągnięcie kursora przez 10 kolejnych sekund aktualizuje obrazy dla tych sekund; błąd czasowy ≤1 s w gotowym gęstym cache, p95 aktualizacji ≤50 ms. Wykonać osobno cold segment, warm segment, szybki skok na odległy czas, zmianę filmu i oba odtwarzacze. Gotowy podgląd nie generuje żądań strumienia, a praca zimnego nie powoduje nowych zacięć głównego filmu w kontrolowanym teście.

## 4. Sposób wykonania dla agenta

Kolejność: **A → C → B → D**. A usuwa najbardziej widoczną regresję, C ogranicza awarie i powielone żądania, B przywraca oczekiwany sposób przeglądania, D realizuje dokładny podgląd. W każdym pakiecie diagnoza występuje tylko w tabeli R1–R5; powyższe kroki są instrukcją naprawy, nie kolejnym audytem.

Rozszerzyć istniejące fixture/testy w `audit` o wskazane scenariusze, zamiast tworzyć drugi framework. Punktowy wcześniejszy test 64 kart nie pokrywa wielokrotnych aktualizacji SSE; test wyboru sprite'a nie pokrywa dokładności sekundy. Pełny zestaw istniejących testów uruchomić po zmianach współdzielonych, bez powtarzania go po samych edycjach planu.

Odbiór końcowy obejmuje rzeczywiste okno desktopowe, pierwsze uruchomienie i ponowne uruchomienie z cache. W raporcie zachować tylko: zmienione pliki, wynik kryteriów A–D, pomiary przed/po oraz ograniczenia źródeł. Nie uznawać samego PASS testów jednostkowych za dowód braku migotania ani czasu startu filmu.

**Polecenie do przekazania agentowi:**

> Wdróż ten plan w kolejności A, C, B, D. Zachowaj istniejące poprawki deduplikacji, generacji żądań, singleflight, Range i dane użytkownika. Przywróć stabilne karty oraz pełną paginację i licznik oparte na kompletnej rewizji lokalnego katalogu. Ogranicz powielone próby niedostępnych strumieni. Podgląd ma pokazywać rzeczywiste kolejne sekundy, nie tylko płynnie przesuwać wskaźnik nad rzadkim storyboardem. Wykorzystaj obecne testy i dopisz wyłącznie brakujące scenariusze odbioru. Szczegóły zapisuj w jednym raporcie, a w wiadomości podaj wynik i link. Nie przebudowuj niezwiązanych funkcji. Nie obiecuj natychmiastowych pełnych danych przy pierwszym uruchomieniu ani czasu CDN bez pomiaru.
