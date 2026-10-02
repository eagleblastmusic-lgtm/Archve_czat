# Ładowanie, odtwarzanie i faktyczne sekundy storyboardu — 30.09.2026

## Mapa wymagań

| Wymaganie | Klasyfikacja | Zmiana i weryfikacja |
|---|---|---|
| Cache, współdzielenie resolvera, priorytet filmu nad FFmpeg | ALREADY_CLOSED | Zachowano istniejące mechanizmy i limity bufora; regresje QoS |
| Przejście QUICK → dokładny arkusz | DELTA_REQUIRED → zamknięte | Wymiary CSS pochodzą z manifestu, zamiast odczytu HTMLImageElement.width/height zwracającego poprzedni rozmiar. Odtworzono: żądana sekunda 32, faktyczny obraz 45; po zmianie oba odtwarzacze pokazują właściwe piksele |
| Nieruchomy kursor | DELTA_REQUIRED → zamknięte | Stała minimalna wysokość rzędu kontrolek usuwa przesunięcie osi o 9 px po załadowaniu ikon/zmianie przycisków. Test Edge wymaga niezmienionego położenia osi |
| Szybkie ponowne otwarcie filmu | DELTA_REQUIRED | Detale mają niezależnych odbiorców; anulowanie kafelka nie anuluje odtwarzacza, ostatni odbiorca zwalnia pracę; regression_loading_frontend |
| Gotowy storyboard z dysku | DELTA_REQUIRED | GET cache przed bramką bufora, POST cache bez resolvera; regression_storyboard_watchers i regression_package_d |
| Powrót po anulowaniu | DELTA_REQUIRED | Nowy odbiorca nie przejmuje anulowanego segmentu; restart zaginionego zadania, odnawianie dzierżawy i limit czasu obejmujący obie próby FFmpeg |
| Obraz z faktycznej sekundy | DELTA_REQUIRED | Wybrane rzeczywiste klatki z PTS zamiast powielania przez fps; test treści klatek przy przesunięciu 3,3 s w obu ścieżkach seek oraz materiał z lukami czasowymi |
| Camwhores | DELTA_REQUIRED | Rzadkie 15 miniatur nie pomija już dokładnych segmentów; podgląd przybliżony jest oznaczony |
| Widoczny stan oczekiwania/błędu | DELTA_REQUIRED | Usunięto display:none!important; oba odtwarzacze pokazują rzeczywisty stan |

## Aktualne dowody

- Rozszerzone istniejące regresje potwierdzają współdzielenie detali przy anulowaniu kafelka, szybkie ponowne zgłoszenie po anulowaniu segmentu, cache dyskowy przy pustym buforze, POST bez resolvera, granicę końca filmu oraz zmianę rozmiaru QUICK → dokładny arkusz.
- Backend FFmpeg: treść klatek odpowiada rzeczywistym sekundom 4–7 przy przesunięciu 3,3 s, zarówno przy szybkim seek, jak i fallback. Materiał z klatką co dwie sekundy daje czasy 0/2/4/6/8; nie tworzy klatek o fikcyjnych czasach 1/3/5/7/9.
- Końcowy test rzeczywistego Edge: **4/4 PASS** (modal i `/watch`, Archivebate i Camwhores). Sekundy **32/35/49/58** mają poprawne piksele. Po wymuszonym pustym buforze podgląd wraca, stan oczekiwania jest widoczny, oś pozostaje nieruchoma, brak błędów JavaScript. [Wynik przeglądarki](repair_results_timeline_browser_final_2026-09-30/timeline_browser_result.json).
- Lokalny start neutralnego filmu: 0,427–0,900 s. To pomiar fixture; **nie jest benchmarkiem prawdziwego CDN ani porównaniem przyspieszenia przed/po**. Gotowy segment nie wymaga kolejnego pobierania filmu.
- Końcowy pełny zestaw poleceń CI na świeżej izolowanej kopii: **86/86 PASS**. [Logi i kody wyjścia](repair_results_timeline_release_2026-09-30/test_results.json). Lokalny Windows, Python 3.14.4, Node 24.19.0, SQLite 3.50.4; nie jest to wykonanie na runnerach Linux/Python 3.13/Node 22. Zewnętrzny test konta pozostaje jawnie pominięty zgodnie z kontraktem test_suite.
- `git diff --check`: PASS. Końcowe pliki implementacji i rozszerzone regresje porównano SHA-256 z kopiami użytymi w testach; zmiany dokumentacji po weryfikacji nie zmieniają tych wejść.

Testy nie korzystają z danych użytkownika ani poświadczeń. W jednej powtórce na używanej kopii testowej wystąpiła kolizja katalogu tymczasowego (ponowne użycie PID w shimie runnera), przed uruchomieniem testu feedu. To błąd środowiska testowego; końcowa kwalifikacja używa świeżej kopii. Testy produktu nie zostały osłabione.

## Granice i dalsze możliwości

- Pierwsze generowanie dokładnego fragmentu wymaga transferu i dekodowania. Zachowano ochronę aktywnego odtwarzacza; przy małym buforze podgląd jawnie czeka, a pauza pozwala go przygotować.
- Cache storyboardu ma wersję 9, aby nie korzystać z dawnych manifestów opartych na przepisanych znacznikach czasu. Poprzednich plików nie kasowano.
- Pozostały największy koszt startu zimnego filmu to resolver dostawcy i połączenie CDN. Istniejący prefetch następnego filmu i cache URL ograniczają ten koszt przy ponownym użyciu. Dalsza optymalizacja wymaga pomiarów rzeczywistego pierwszego kadru i zacięć, nie samej liczby bajtów proxy.
- Wybieranie klatek bez tworzenia fikcyjnych sekund opiera się na [dokumentacji filtra select FFmpeg](https://ffmpeg.org/ffmpeg-filters.html#select_002c-aselect).
