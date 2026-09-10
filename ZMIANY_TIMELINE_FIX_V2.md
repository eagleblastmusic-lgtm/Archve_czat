# Timeline Preview Fix v2.1

Naprawa problemu, w którym podgląd na timeline potrafił stale pokazywać ten sam kadr.

## Co zmieniono

- nowy persistent storyboard cache: `archivebate-storyboards-v2` (stary v1 jest ignorowany),
- storyboard nie zapisuje klatki bezpośrednio po `seeked`; czeka na faktycznie zaprezentowaną klatkę dekodera przez `requestVideoFrameCallback`,
- wadliwy storyboard z wieloma identycznymi klatkami nie jest zapisywany,
- poprawiony silnik `latest request wins` dla szybkiego przesuwania kursora,
- stare callbacki są unieważniane po opuszczeniu timeline,
- brak `fastSeek` — używane jest precyzyjne `currentTime`,
- 20 klatek lokalnego storyboardu zamiast 16,
- poprawione nakładanie warstwy IMG/VIDEO w tooltipie,
- cache-busting: `storyboard-cache.js?v=2.1`, `player-core.js?v=2.1`, `app.js?v=15.1`.

## Ważne

Nie trzeba ręcznie czyścić starego IndexedDB — nowa wersja korzysta z osobnej bazy v2.
