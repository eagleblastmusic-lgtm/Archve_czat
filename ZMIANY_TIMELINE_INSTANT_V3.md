# Timeline Instant V3

Najważniejsze zmiany wydajności podglądu osi czasu:

- progresywny storyboard: QUICK (8 klatek) pojawia się pierwszy, FULL (36–96 klatek) buduje się później w tle;
- generator storyboardu uruchamia się już po dłuższym najechaniu kafelka / kliknięciu, przed najechaniem na timeline;
- rozwiązywanie direct URL filmu odbywa się jeden raz (single-flight), zamiast potencjalnie osobno dla każdego równoległego procesu FFmpeg;
- zmniejszono rozdzielczość klatki sprite do 160×90, zgodnie z rzeczywistym rozmiarem tooltipa;
- wyłączono kosztowne JPEG optimize/progressive podczas budowania sprite’a;
- frontend predekoduje sprite przed użyciem;
- sprite jest renderowany jako jeden obraz przesuwany przez GPU (`translate3d`) zamiast `background-position` z odczytem `clientWidth/clientHeight` na każdym ruchu myszy;
- aktualizacja hover jest ograniczona do maksymalnie jednej operacji na klatkę ekranu (`requestAnimationFrame`);
- ta sama klatka nie jest renderowana ponownie, jeśli indeks się nie zmienił;
- tooltip przesuwany jest transformacją GPU zamiast ciągłego layoutowania przez `left`;
- poster pokazuje się natychmiast, gdy QUICK storyboard nie jest jeszcze gotowy;
- dokładniejszy FULL sprite podmienia QUICK bez przerywania hover;
- opóźniono ciężki pełny stream podglądu na kafelku, aby nie walczył o transfer z playerem i storyboardem;
- `/watch` dostaje hint długości filmu z modala, dzięki czemu może rozpocząć warmup przed `loadedmetadata`;
- cache-busting podniesiony do `style.css?v=17.0`, `youtube-storyboard.js?v=2.0`, `app.js?v=17.1`.

Nie dodano zapisywania pozycji oglądania ani oznaczania procentu obejrzenia.
