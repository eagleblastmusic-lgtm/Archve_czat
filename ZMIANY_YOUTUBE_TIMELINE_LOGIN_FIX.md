# YouTube Timeline + Login Fix

## Timeline

- Archivebate nie seekuje już drugiego elementu `<video>` podczas przesuwania kursora.
- Backend generuje **jeden sprite JPG** (arkusz klatek), podobnie jak storyboardy używane przez duże serwisy wideo.
- Klatki są wyciągane z pełnego filmu przez FFmpeg przy użyciu HTTP Range seeków.
- Liczba klatek jest adaptacyjna: 24–72 zależnie od długości filmu.
- Sprite jest zapisywany w `data/storyboard_cache` i używany ponownie przy kolejnych otwarciach.
- Hover po gotowym storyboardzie nie wykonuje żadnych zapytań wideo — tylko zmienia `background-position` jednego obrazu.
- Camwhores nadal używa własnych gotowych storyboardów, jeśli są dostępne; w przeciwnym razie może użyć tego samego generatora sprite.
- Podczas pierwszego przygotowania zamiast błędnej/starej klatki wyświetlany jest komunikat „Przygotowywanie podglądu…”.

## Logowanie

- `.env.local` jest czytany jako `utf-8-sig`, więc działa również gdy starszy PowerShell zapisał BOM.
- `USTAW_KONTO.bat` zapisuje teraz UTF-8 **bez BOM**.
- przycisk ponownego logowania ponownie czyta aktualny `.env.local` — restart aplikacji nie jest wymagany;
- weryfikacja sesji nie zależy już od jednego kruchego napisu w HTML odpowiedzi po POST;
- API zwraca dokładny `login_error`, a frontend pokazuje konkretny powód błędu.

## Nowe zależności

- `Pillow`
- `imageio-ffmpeg` — dostarcza FFmpeg także wtedy, gdy nie jest zainstalowany globalnie w Windows.
