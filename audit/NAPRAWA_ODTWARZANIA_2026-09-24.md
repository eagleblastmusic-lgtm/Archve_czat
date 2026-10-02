# Weryfikacja wdrożenia i poprawki odtwarzania — 24.09.2026

## Zakres i stan wymagań
- ALREADY_CLOSED: izolacja członków grup, źródło/ID w kwarantannie, potwierdzanie dwoma świeżymi odczytami, POST odświeżania, ochrona bufora odtwarzacza. Zachowano wdrożenie wcześniejszego planu.
- DELTA_REQUIRED → wykonane: rozpoznanie usunięcia pliku Mixdrop przy istniejącej stronie Archivebate HTTP 200; propagacja dowodu przez cache/API, trwały lokalny rejestr i drugą kartę przeglądarki; komunikat o usunięciu w obu odtwarzaczach i HTTP 410 strumienia.
- DELTA_REQUIRED → wykonane: podwójne opóźnienie EXACT oraz anulowanie na każdym ruchu kursora. Koordynator korzysta z istniejącego requestSegment (140 ms zamiast 260 + 140 ms), współdzieli pracę w obrębie segmentu i zachowuje QoS. Anuluje po opuszczeniu osi, zmianie filmu i problemach bufora.
- DELTA_REQUIRED → wykonane: ponowne przygotowanie QUICK po odbudowaniu bufora, ograniczenie koordynatora do aktywnego filmu w modalu, ochrona dokładnej miniatury przed spóźnionym QUICK i oznaczenie przybliżonego podglądu.

- DELTA_REQUIRED → wykonane po teście przeglądarkowym: CSP blokowała własny skrypt strony `/watch`. Dodano SHA-256 dokładnej treści skryptu z końcowego HTML, również po transformacji runtime. Nie dodano unsafe-inline ani unsafe-eval dla skryptów.

- DELTA_REQUIRED → wykonane po teście osi czasu: `/watch` chował kontrolki pod nieruchomym kursorem po 2,5 s i przez pointerleave anulował miniaturę. Usunięto osobny timer; wykorzystano istniejący `PlayerCore.setupIdleTimer`, stosowany już przez modal.

## Dowody
- Film 16444260: strona źródłowa HTTP 200; Mixdrop przekierowuje na mxdrop.top i wyświetla „We can't find the video you are looking for”. Dwa odczyty naprawionym klientem: unavailable / embed_file_not_found / retryable=false (11,67 s i 3,03 s). Sama odpowiedź 403 nie jest dowodem usunięcia.
- Testy obejmują 403/503/challenge/obcy host, skrypt wspominający captcha bez wyświetlania wyzwania, HTTP 410, trwałość kwarantanny, zachowanie aktywnych członków grup i rzeczywiste połączenie koordynatora z klientem storyboardu.
- Pełny zestaw poleceń CI: **85/85 PASS** w izolowanej kopii, lokalnie na Windows/Python 3.14. Nie jest to wykonanie na runnerze Linux ani wszystkich wersjach Pythona deklarowanych przez CI. Logi: `repair_results_final_2026-09-24/test_results.json`.
- Pełny zestaw powtórzono po naprawie CSP. Po późniejszej zmianie timera `/watch` ponowiono trzy właściwe kontrole: accessibility (rozszerzona o zachowanie timera i składnię inline), frontend oraz runtime wiring z weryfikacją hashy CSP — wszystkie PASS. Wynik runtime: `repair_results_watch_final/test_results.json`.
- Pomiar prawdziwego CDN, 64 KB aktywnego filmu 16447670: bezpośrednio 0,735 s; proxy 0,742 s (połączenie upstream 667 ms), ponowne proxy 0,155 s. To pomiar małego zakresu bajtów, nie czasu pierwszej klatki ani dowód stałego przyspieszenia całego odtwarzania.

- Test rzeczywistego Edge na nowym profilu: film 16447670 osiągnął 1,12 s odtwarzania po 4,06 s od nawigacji; readyState=4, bez błędu mediów i bez błędów JavaScript. Film 16444260 wyświetlił komunikat o usunięciu i trafił do lokalnej kwarantanny po potwierdzeniach (`live_playback_initial.json`). To pojedyncza próba, nie benchmark porównawczy z przeglądarką źródła.

- Po poprawce timera test Edge potwierdził: 1,14 s odtwarzania po 3,61 s od nawigacji, gotowy segment podglądu w około 4,44 s, załadowany obraz i widoczna klatka nr 6, zero anulowanych odbiorców i zero błędów JavaScript (`live_playback_preview.json`).

- Ostatnia próba Edge potwierdziła zmianę klatki **6 → 20** po przesunięciu kursora przy trwającym odtwarzaniu; segment z cache był gotowy w 25,7 ms. Ponownie potwierdzono komunikat i kwarantannę filmu 16444260, bez błędów JS (`live_playback_final.json`). To ciepły cache, nie pomiar pierwszego generowania.

## Granice
Usunięcia są ukrywane po wykryciu i niezależnym potwierdzeniu, nie przez kosztowne skanowanie całego katalogu. Kwarantanna pozostaje odwracalna i wygasa po 12 godzinach. Brak pliku, timeout i odmowa dostępu pozostają różnymi stanami. Cztery wstępne klatki QUICK są przybliżeniem; dokładny fragment nadal wymaga pobrania i przetworzenia materiału przez FFmpeg. Nie wyłączono ochrony bufora i limitów procesów. Dane użytkownika i baza katalogu nie były zmieniane przez testy.


## Uzupełnienie po ponownym zgłoszeniu wiszącego storyboardu

- DELTA_REQUIRED: koordynator modala usuwał zamiar podglądu podczas waiting/stalled lub spadku bufora. Gdy kursor pozostawał nieruchomy, nie było zdarzenia odtwarzającego żądanie. Zachowano ostatni cel wraz z sygnałem anulowania; przy odzyskaniu bufora/progress/play/pause/seeked wraca istniejący requestSegment. Wyjście z osi lub zmiana filmu nadal odrzuca zamiar.
- DELTA_REQUIRED: requestSegment ignorował błąd i pozostawiał ogólny napis ładowania. Udostępnia teraz rozróżnienie oczekiwania na bufor, generowania i błędu; oba odtwarzacze oraz warstwa fallback korzystają z tego stanu. Błąd startu jest obsługiwany od razu. Ruch kursora/następne najechanie umożliwia ponowienie.
- DELTA_REQUIRED: szybkie odpowiedzi statusu QUICK mogły tworzyć gorącą pętlę zapytań. Odpytywanie ma teraz minimalny odstęp 180 ms, z zachowaniem przerwania przez AbortSignal.
- ALREADY_CLOSED: próba w prawdziwym modalu na osobnym profilu Edge z filmem 16447670 przeszła przed tą deltą (odtwarzanie i zmiana klatki 6 → 20). Nie odtworzyła zgłoszenia „w każdym” w warunkach zdrowego bufora; nie traktujemy jej jako dowodu usunięcia wszystkich przyczyn.
- Rozszerzony test integracji rzeczywistych modułów JS wymusza waiting, sprawdza anulowanie, przywraca bufor i wymaga wznowienia bez ruchu kursora. Osobno sprawdza komunikat przy błędzie backendu. Test PASS.
- Odczyt diagnostyki działającej aplikacji potwierdził code_root Archivebite_Czat. Sprawdzono przez HTTP zgodność trzech serwowanych plików JS z plikami na dysku. Otwarta wcześniej karta wymaga ponownego załadowania.
- Wyniki regresji tego uzupełnienia: `repair_results_storyboard_stall/test_results.json`.
