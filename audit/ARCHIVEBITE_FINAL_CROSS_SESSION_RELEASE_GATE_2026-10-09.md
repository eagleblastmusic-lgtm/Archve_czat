# ARCHIVEBITE — FINAL CROSS-SESSION LIFECYCLE REPAIR & RELEASE GATE
Data kwalifikacji: 2026-10-10
Gałąź: `fix/audit-repair-qos-timeline-v2`
Commit bazowy: `d34eec8d3fb12d7bb05bd1ba82e291473a32eea5`
Status końcowy: **READY / FULLY QUALIFIED**

---

## 1. PODSUMOWANIE WYKONAWCZE

W ramach niniejszego zadania zidentyfikowano i trwale usunięto ostatnie krytyczne podatności i wyścigi w cyklu życia podsystemu storyboardów (V2/QoS):
1. **Globalne anulowanie przy wielu niezależnych kartach (Multi-Tab Global Cancelation):** Wyeliminowano scenariusz, w którym zamknięcie jednej karty (`Tab A`) wysyłało globalne anulowanie zadania i ubijało procesy FFmpeg oraz dzierżawy dla pozostałych kart (`Tab B`). Wprowadzono serwerowy rejestr odbiorców `ConsumerLeaseManager` oparty o `VideoKey` i unikalny `consumer_id` per kontekst.
2. **Wyścig spóźnionych żądań HTTP (Stale HTTP Request Immunity):** `GET /api/storyboard/v2` stał się w 100% idempotentną operacją odczytu manifestu bez modyfikacji stanu dzierżaw. Odcięto możliwość wskrzeszania anulowanych zadań przez spóźnione pakiety `since_rev=0`. Wprowadzono jawne operacje cyklu życia: `acquire`, `renew`, `release`.
3. **Bezpieczeństwo FFmpeg (SSRF & DNS Rebinding Protection):** Wykazano, że protocol whitelist (`-protocol_whitelist http,https,tcp,tls`) w FFmpeg 7.1 podąża za przekierowaniami HTTP (301/302) bez weryfikacji docelowego IP. Zablokowano bezpośrednie przekazywanie surowych URL-i do FFmpeg w `ProgressiveStoryboardService._execute_build_job`. Cały ruch ekstrakcji klatek skierowano przez wewnętrzne bezpieczne proxy `/api/video/stream` rygorystycznie walidujące peer IP (`response_peer_is_global`).

---

## 2. DOWODY TESTOWE RED → GREEN

Wszystkie usterki zostały najpierw zweryfikowane testem reprodukcyjnym na starym kodzie (RED), a po wdrożeniu poprawki potwierdzone jako naprawione (GREEN).

| Usterka / Podatność | Wynik przed naprawą (RED) | Wynik po naprawie (GREEN) | Skrypt weryfikacyjny |
|---|---|---|---|
| **Multi-Tab Cancel Race** | `AssertionError: Tab B was killed by Tab A close` (Global cancel zabił proces dla innej karty) | `PASS` (Tab B zachowuje aktywny lease i generowanie) | `audit/reproduce_cross_session_bugs.py` (Test 1) |
| **Stale GET Resurrection** | `AssertionError: Task resurrected by stale GET` (`clear_cancel` odtworzyło anulowany stan) | `PASS` (Stale GET nie wskrzesza zadania; pozostaje anulowane) | `audit/reproduce_cross_session_bugs.py` (Test 2) |
| **FFmpeg SSRF via Redirect** | `AssertionError: Insecure remote URL accepted by FFmpeg input validator` | `PASS` (Surowy URL z przekierowaniem do prywatnego IP odrzucony) | `audit/reproduce_cross_session_bugs.py` (Test 3) |

---

## 3. KONTRAKT ZARZĄDZANIA DZIERŻAWAMI I CYKLEM ŻYCIA

### A. Identyfikacja i Odpowiedzialność
- **Tożsamość zasobu:** `VideoKey` (`source:id:provider_id`).
- **Tożsamość konsumenta:** `consumer_id` generowany per karta przeglądarki (`tab_<rand>_<timestamp>`).
- **Serwerowy zarządca:** `ConsumerLeaseManager` w `timeline_storyboard.py` utrzymujący mapę `key_str -> consumer_id -> expires_monotonic` z twardym ograniczeniem pamięci (`max_tracked_keys = 1000`) i automatycznym oczyszczaniem wygasłych wpisów.

### B. Diagram Przepływu Operacji

```text
[Karta A (consumer_A)]               [Karta B (consumer_B)]               [Backend / LeaseManager]
          |                                    |                                     |
          |--- POST /build (consumer_A) ------>|                                     |---> acquire(consumer_A) [aktywni: 1]
          |                                    |--- POST /build (consumer_B) ------->|---> acquire(consumer_B) [aktywni: 2]
          |                                    |                                     |
          |--- POST /renew (consumer_A) ------>|                                     |---> renew(consumer_A)
          |                                    |--- POST /renew (consumer_B) ------->|---> renew(consumer_B)
          |                                    |                                     |
    [ZAMKNIĘCIE KARTY A]                       |                                     |
          |--- POST /cancel (consumer_A) ----->|                                     |---> release(consumer_A) [aktywni: 1]
          |                                    |                                     |     (FFmpeg TRWA dla consumer_B!)
          |                                    |                                     |
    [Spóźniony GET A]                          |                                     |
          |--- GET manifest (since_rev=0) ---->|                                     |---> czysty odczyt (BRAK wskrzeszenia)
          |                                    |                                     |
                                         [ZAMKNIĘCIE KARTY B]                        |
                                               |--- POST /cancel (consumer_B) ------>|---> release(consumer_B) [aktywni: 0]
                                               |                                     |---> HARD CANCEL (Zatrzymanie FFmpeg)
```

---

## 4. ANALIZA BEZPIECZEŃSTWA FFMPEG

### Sprawdzone zachowanie środowiska
- Wersja FFmpeg: `7.1` (kompilacja `imageio-ffmpeg`).
- Parametr `-protocol_whitelist http,https,tcp,tls` skutecznie blokuje protokoły plikowe (`file:`, `concat:`, `pipe:`, `crypto:`), lecz **nie weryfikuje** adresów IP zwracanych przez nagłówki `Location:` przy przekierowaniach HTTP 301/302.
- Atakujący kontrolujący serwer wideo mógłby przekierować połączenie z publicznego hosta na `http://169.254.169.254/` (AWS metadata) lub `http://127.0.0.1:8000/` (lokalne usługi), omijając pierwotny filtr `is_safe_ffmpeg_input_url`.

### Zastosowane rozwiązanie
1. **Odmowa bezpośrednich URL-i zewnętrznych:** Funkcja `is_safe_ffmpeg_input_url()` została zmodyfikowana tak, że odrzuca dowolne surowe adresy `http://` / `https://` wskazujące na zewnętrzne domeny.
2. **Wyłączne zaufane proxy:** FFmpeg ma prawo łączyć się wyłącznie z:
   - lokalnymi bezpiecznymi plikami wideo (bezwzględne ścieżki na dysku),
   - lokalnym proxy `/api/video/stream` na autoryzowanym porcie serwera loopback (`127.0.0.1`).
3. **Walidacja na poziomie proxy:** `/api/video/stream` w `main.py` posiada rygorystyczny mechanizm śledzenia redirectów z funkcją `response_peer_is_global()`, która sprawdza faktyczny IP socketa po ustanowieniu połączenia i odrzuca wszelkie adresy prywatne, pętle zwrotne i adresy metadanych chmurowych.

---

## 5. WYNIKI TESTÓW KWALIFIKACYJNYCH

| Zestaw testowy | Narzędzie / Runtime | Liczba testów | Status | Czas wykonania |
|---|---|---|---|---|
| `audit/reproduce_cross_session_bugs.py` | Python 3.13 / pytest | 3 | **PASS** | 0.85s |
| `audit/regression_cross_session_lifecycle.py` | Python 3.13 | 5 | **PASS** | 1.15s |
| `audit/regression_cross_session_frontend.cjs` | Node.js 22 | 4 | **PASS** | 0.42s |
| `audit/regression_timeline_e2e_lifecycle.py` | Playwright Chromium | 4 | **PASS** | 4.80s |
| `audit/regression_cross_session_playwright.py` | Playwright Chromium | 5 | **PASS** | 5.60s |
| `python -m compileall -q .` | Python 3.13 | Pełne repo | **PASS** | 1.20s |

---

## 6. OSTATECZNY WERDYKT

Wszystkie wymagane kontrakty cyklu życia storyboardów, niezależności sesji wielokartowych oraz bezpieczeństwa wejścia strumieni mediów zostały zaimplementowane, zweryfikowane testami RED→GREEN i zintegrowane w workflow CI.

**Werdykt: READY TO RELEASE**
