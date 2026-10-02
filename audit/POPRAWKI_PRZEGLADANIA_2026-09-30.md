# Podglądy, nawigacja i ładowanie — 30.09.2026

| Wymaganie | Stan przed zmianą | Delta i dowód |
|---|---|---|
| Zmienna klatka co 10 s | DELTA_REQUIRED | Trzy krótkie odczyty z rzeczywistych pozycji zamiast dekodowania całych 30 s; istniejący cache i scheduler |
| Główna, Ulubione, Profile | DELTA_REQUIRED | Osobny górny pasek, Profile na istniejącym grupowanym katalogu |
| Środkowy przycisk myszy | DELTA_REQUIRED | Usunięcie wywołań nieobecnego tabManager; natywne linki /watch |
| Usunięte filmy | DELTA_REQUIRED | Kontrola widocznych kart przed hover; potwierdzony brak pliku po odświeżeniu adresu; istniejąca kwarantanna |
| Szybsze kafelki i profile | DELTA_REQUIRED | Naprawa odzyskiwania miniatur, cache profilu i wcześniejszy lokalny widok |
| Cache, współdzielenie pracy, ochrona filmu | ALREADY_CLOSED | Zachować działające prymitywy i testy anulowania/QoS |
| Wykorzystanie 9d0 Mb/s | DELTA_REQUIRED | Usunięcie zbędnego poglądu całego filmu, równoległe miniatury i większe porcje proxy; bez deklaracji osiągnięcia przepustowości CDN |

Użytkownik wybrał zakładki Główna / Ulubione / Profile i karty przeglądarki dla filmów.
Wymienione delty zostały wykonane. Istniejące zmiany w katalogu roboczym pozostają zachowane.

## Co działa po zmianie

- Storyboard przygotowuje trzy krótkie wycinki dla segmentu: np. 1:00, 1:10, 1:20. Zachowuje rzeczywiste znaczniki PTS i pokazuje czas wybranej klatki, także pomiędzy punktami dziesięciosekundowymi. Wersja cache to 10. Nie powstaje dodatkowy, zimny arkusz czterech klatek całego filmu; dokładniejsze istniejące manifesty pozostają obsługiwane przez klienta.
- W nagłówku jest osobny pasek Główna / Ulubione / Profile. Profile używają istniejącego grupowanego katalogu, po jednym kafelku na autora. Historia, Obserwowane i Panel Konta nadal są dostępne.
- Miniatura, Odtwórz i nagrania wewnątrz grup są prawdziwymi linkami /watch. Środkowy przycisk, Ctrl+klik i menu kontekstowe działają natywnie; zwykły klik otwiera modal raz.
- Pierwsze 16 miniatur startuje od razu; dalsze korzystają z obserwatora i ograniczonego marginesu widoku. Po przejściowym błędzie miniatura jest ponawiana bez hover. Jeśli JPG nie istnieje, krótki klip podglądu Freefile może dostarczyć pierwszą klatkę przez lokalne proxy; odczyt jest zamykany po uzyskaniu obrazu. Ten fallback nie uruchamia pełnego filmu Camwhores.
- Widoczne kafelki są sprawdzane w tle, po dwa jednocześnie. Kontrole nie są rozpoczynane podczas odtwarzania. Kontrola pobiera nagłówki z Range 0–0 i zamyka odpowiedź przed body, także gdy serwer ignoruje Range. Potwierdzony brak strony/embed lub pliku po odświeżeniu adresu korzysta z istniejącej kwarantanny i znika również przy ponownym renderowaniu.
- Profil najpierw może wyświetlić zapisane lokalnie nagrania z istniejącego indeksu autora. Odpowiedź dostawcy je odświeża. Niewielki cache klienta pozwala od razu ponownie otworzyć profil; anulowanie i kolejność odpowiedzi są sprawdzane.
- Porcja transmisji proxy wzrosła z 64 do 256 KiB, co ogranicza liczbę przejść przez generator. Cache, Range i priorytet odtwarzacza pozostają zachowane.

## Końcowa weryfikacja

- **86/86 poleceń regresji CI: PASS** na świeżej izolowanej kopii. [Wyniki i logi](repair_results_browse_release_2026-09-30/test_results.json).
- **Edge: 4/4 scenariusze odtwarzacza oraz scenariusz przeglądania: PASS**. Modal i /watch dla obu dostawców pokazują właściwe piksele w 60/70/80/90 s. Oś pozostaje nieruchoma, stan oczekiwania na bufor jest widoczny, brak błędów JavaScript. [Wynik przeglądarki](repair_results_browse_browser4_2026-09-30/timeline_browser_result.json).
- Scenariusz przeglądania potwierdza odzyskanie miniatury bez najechania, fallback z klipu przy niedostępnym JPG, faktyczne otwarcie nowej karty środkowym przyciskiem, trzy zakładki oraz trzy odczyty dowodu usunięcia i brak kafelka po powrocie na listę.
- Backend obejmuje: 404 → nowy działający URL, powtórne 404/410 po odświeżeniu, 403, 503, brak body kontroli oraz odrzucenie niepełnego dowodu. Profile mają test kolejności lokalny → online, ponownego użycia cache i spóźnionych odpowiedzi po zmianie widoku.
- `pip check`: PASS; kontrola poświadczeń i destrukcyjnych launcherów: 0 trafień; `git diff --check`: PASS. [Zgodność SHA-256 końcowych wejść z kopiami testowymi](repair_results_browse_release_2026-09-30/verified_inputs.json).
- Środowisko lokalne: Windows, Python 3.14.4, Node 24.19.0, SQLite 3.50.4. Uruchomiono polecenia projektu, nie macierz hostowanych runnerów Linux/Python 3.13/Node 22. Test konta wymagający zewnętrznego dostępu pozostaje pominięty zgodnie z kontraktem test_suite. Testy nie korzystają z danych użytkownika ani poświadczeń.

## Granice pomiaru i użycia

Pomiar startu lokalnego neutralnego filmu i scenariusze fixture nie są pomiarem transferu prawdziwego CDN. Nie potwierdzono osiągania 90 Mb/s. Rzeczywisty czas zależy od dostawcy, dostępności pliku, indeksu MP4 i położenia klatek kluczowych; trzy krótkie seeki nie oznaczają gwarantowanej redukcji bajtów o 90%.

Pierwszy niebuforowany fragment nadal wymaga przygotowania. Przy krytycznym buforze film zachowuje priorytet, a podgląd jawnie czeka; pauza pozwala go wygenerować. Znane, potwierdzone usunięcia są filtrowane od razu. Nowego usunięcia nie można poznać bez sprawdzenia źródła, więc taki kafelek może chwilowo pojawić się przed potwierdzeniem. Błędy sieci, prywatność i autoryzacja nie powodują ukrycia filmu.

W trakcie pracy dwa pierwsze uruchomienia testu nowych kart wymagały poprawki narzędzia testowego: funkcji zamiast eval przy CSP i zdarzenia nowej strony w kontekście przeglądarki zamiast popup z openerem. Jedno ręczne uruchomienie Python poza runnerem trafiło na ograniczenie ACL katalogu tymczasowego; końcowe 86/86 używa właściwej izolacji. Wymagania testów produktu nie zostały osłabione.

Aby załadować backend i wszystkie nowe skrypty, uruchom ponownie aplikację i odśwież kartę Ctrl+F5.

## Ponowne zgłoszenie: nieruchomy podgląd, 30.09.2026

DELTA_REQUIRED: obsługa wolnego źródła i ograniczenie czasu odczytu statusu.
ALREADY_CLOSED: wybór klatki co 10 s, rzeczywiste PTS, atlas, anulowanie i priorytet filmu.
Przyczyna na rzeczywistym źródle użytkownika pozostaje niepotwierdzona: nie otrzymano jeszcze adresu filmu. Zrzut pokazuje poster i stan przygotowania, nie gotowy storyboard.

- `_extract_preview_frames_with_times` wykorzystuje teraz jeden odczyt i seek istniejącego ekstraktora dla segmentu, zamiast trzech osobnych otwarć MP4. Filtr wybiera rzeczywiste klatki co 10 s. Limit procesu wynosi 25 s zamiast 8 s na kotwicę; zachowano walidację, PTS i anulowanie. Format cache pozostaje zgodny.
- Odczyt statusu obejmujący nagłówki i JSON ma limit 12 s; przygotowanie segmentu ma również limit całej pracy 60 s. Fallback mutacji bez klienta API ma limit czasu. Brak odpowiedzi przestaje pozostawiać nieograniczone przygotowywanie.
- Błąd pozostaje widoczny przez 10 s, także podczas ruchu kursora. Ruch nie rozpoczyna ponownie tego samego błędnego żądania co klatkę ekranu.
- Rozszerzono istniejące dowody: jeden proces dla różnych klatek co 10 s, zawieszony odczyt statusu przy nieruchomym kursorze, brak lawiny ponowień, rzeczywisty Edge ze sztucznym opóźnieniem nagłówków źródła o 9 s. Wszystkie potwierdzają wynik.

Końcowa weryfikacja nowych wejść:

- [Pełne CI: 85/86 w pierwszym przebiegu](repair_results_preview_retry_release/test_results.json). Jedyny błąd: kolizja katalogu tymczasowego `tmp<PID>_0` po ponownym użyciu PID w izolacji Windows, zanim wykonano sprawdzany kontrakt. [Ponowne uruchomienie tego testu w świeżej izolacji: PASS](repair_results_preview_retry_security/test_results.json). Wymagania nie zostały osłabione; wszystkie wymagane polecenia mają końcowy PASS.
- [Edge: 5 scenariuszy odtwarzacza plus przeglądanie: PASS](isolated_implementation_preview_retry_browser/timeline_browser_result.json). Piąty scenariusz dostarcza różne piksele w 60/70/80/90 s pomimo 9 s opóźnienia każdej odpowiedzi strumienia storyboardu. To symulacja wolnego HTTP, nie rzeczywisty CDN.
- [Zgodność SHA-256 bieżących wejść z wynikami](repair_results_preview_retry_release/verified_inputs.json). `git diff --check`: PASS.
- Po informacji użytkownika o uruchomieniu serwera GET na port 8000 odpowiada. Runtime ma storyboard v10. Po próbie użytkownika diagnostyka identyfikuje film `16456728` (shinyways, okno Desktop). Cache zawiera gotowe segmenty 0 i 15, czasy odpowiednio 0/10/20 oraz 450/460/470 s. Analiza istniejących atlasów na dysku potwierdza trzy różne obrazy w obu segmentach; GET dla segmentu 15 i rzeczywistej długości 1317,487333 s zwraca ready. Czas generowania p95 w tej sesji: 11,69 s. Cztery pliki JS/CSS pobrane z uruchomionego serwera odpowiadają bieżącym plikom roboczym SHA-256. Potwierdzono zatem generowanie klatek z rzeczywistego źródła, ale nie ich prezentację w desktopowym DOM: otrzymany pełny zrzut ma schowany pasek i tooltip. Oczekiwana jest odpowiedź o ponownym hover 7:30 / 7:40 / 7:50; nie deklarujemy potwierdzenia prezentacji w tym oknie na podstawie samego atlasu.

## Wymaganie natychmiastowego hover: końcowa delta

Użytkownik potwierdził później, że klatki pojawiają się, ale pierwszy odczyt fragmentu trwa ponad 5 s. DELTA_REQUIRED: przygotowywanie innych fragmentów przed ruchem kursora. ALREADY_CLOSED: wyświetlanie gotowych klatek i wybór rzeczywistej pozycji filmu. Zerowy czas pierwszego odczytu nieznanego zdalnego źródła nie jest gwarantowany.

`youtube-storyboard.js` zaczyna przygotowywanie całej osi po metadanych/pauzie, wykorzystując istniejące `warm` i `prepareSegment`, cache SSD, atlas oraz dzierżawy. W danej chwili przygotowuje jeden kolejny fragment; grający film wymaga 8 s bufora. Bieżący hover ma pierwszeństwo i może przerwać przygotowywanie w tle. Przerwany fragment jest później ponawiany, a zamknięcie odtwarzacza, zmiana filmu i pagehide anulują pozostałą pracę. Podczas pierwszego przygotowania tooltip pokazuje procent. Gotowe klatki co 10 s są odczytywane z cache i nie uruchamiają generowania na ruchu kursora.

Dodano wyłącznie do Desktop odczyt `/api/runtime/desktop/timeline`, korzystający z istniejącego `window.evaluate_js`. Zwraca wybrane stany DOM/liczniki, bez URL dostawcy, cookies i danych konta; istniejąca ochrona lokalnego Host pozostaje sprawdzana. Po restarcie użytkownika rzeczywisty DOM potwierdził wczytany atlas, klatkę 650 s, 6 segmentów w pamięci, 7 wyświetlonych klatek oraz czas przygotowania p50 4,0 s / p95 7,6 s. To ustaliło koszt przygotowania na hover jako rzeczywistą deltę; nie było podstaw do przebudowy wyświetlania atlasu.

Końcowe testy używają bieżących wejść:

- Edge: 6 scenariuszy odtwarzacza plus przeglądanie PASS. Nowy scenariusz przerywa przygotowanie w tle poprzez pilny hover, następnie wymaga wszystkich fragmentów filmu w cache. Kolejny sweep pokazuje właściwe piksele 60/70/80/90 s przy **0 nowych zadaniach przygotowania**, z limitem każdego sprawdzenia wyświetlenia 1 s. Zachowano scenariusz HTTP z opóźnieniem 9 s. [Wynik](isolated_implementation_preview_ahead_browser_final/timeline_browser_result.json).
- [Pełna końcowa weryfikacja: 86/86 PASS](repair_results_preview_ahead_final/test_results.json), `pip check` PASS, `git diff --check` PASS. [Zgodność SHA-256](repair_results_preview_ahead_final/verified_inputs.json). Po pełnym przebiegu poprawiono jedynie końce linii Desktop z CRLF na LF; treść jest zgodna po normalizacji i [ponowiona właściwa regresja routingu Desktop PASS](repair_results_preview_ahead_desktop_final/test_results.json). Wcześniejsze wyniki dotyczą etapów przed przygotowywaniem całej osi.

Granica: pierwsze przygotowanie nieznanego filmu nadal wymaga pobrania i dekodowania; w tle może trwać dłużej dla całej osi niż dla jednego fragmentu. Nie ma gwarancji natychmiastowego cold hover, zanim odpowiednia klatka zostanie przygotowana. Ponowne użycie istniejącego cache nie wymaga generowania. Pamięć klienta pozostaje ograniczona do 48 segmentów; dla dłuższych filmów starsze fragmenty są odczytywane z cache SSD zamiast utrzymywania nieograniczonej pamięci.

Po ostatniej zmianie trzeba ponownie otworzyć okno Desktop, aby załadowało skrypt przygotowujący podglądy z wyprzedzeniem. Użytkownik nie potwierdził jeszcze zachowania tej końcowej wersji na własnym filmie; testy przeglądarki używają neutralnego lokalnego materiału.
