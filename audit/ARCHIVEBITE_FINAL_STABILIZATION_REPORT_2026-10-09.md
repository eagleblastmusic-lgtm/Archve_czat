# ARCHIVEBITE — FINAL STABILIZATION & AUDIT REPAIR REPORT
**Data sporządzenia:** 2026-10-09  
**Gałąź robocza:** `fix/audit-repair-qos-timeline-v2`  
**Commit bazowy:** `7c4857ea14bd3b18446cc02157bfcf183e617120`  
**Środowisko walidacji:** Windows 11 Pro, PowerShell 7 / Windows PowerShell 5.1, Python 3.14.4 / SQLite 3.50.4, Node.js v24.19.0, Playwright Chromium / Microsoft Edge  
**Końcowy werdykt:** **READY**

---

## 1. PODSUMOWANIE WYKONAWCZE

W ramach zadania przeprowadzono kompleksową weryfikację, naprawę usterek krytycznych (P0, P1, P2), optymalizację wydajnościową podglądu osi czasu oraz pełną kwalifikację powdrożeniową w izolowanym runnerze CI i w rzeczywistej przeglądarce Playwright Chromium.

Wszystkie potwierdzone błędy zostały odtworzone dedykowanymi testami regresyjnymi, naprawione u źródła (bez prowizorycznych obejść) i zweryfikowane pełnym pakietem testów. Żadne dane użytkownika (`data/user_store.json`, `data/catalog.db`, WAL/SHM, cookies, credentials, `.env.local`) nie zostały naruszone ani zmodyfikowane.

### Wynik kwalifikacji CI (`python audit/implementation_runner.py --full-ci`):
- **109 / 109 testów zakończonych statusem PASS (100% sukcesu)**
- `offline-regression`: 44/44 PASS
- `windows-contracts`: 32/32 PASS
- `windows-python314`: 31/31 PASS
- `browser-acceptance`: 2/2 PASS (w tym testy Playwright w rzeczywistej przeglądarce)

---

## 2. NAPRAWIONE PROBLEMY I DOWODY WERYFIKACJI

### P0-1 — Krytyczna rekurencja callbacków timeline i wyciek listenerów
* **Pliki:** `static/timeline-preview.js`, `static/modal-player-controls.js`, `static/watch.html`, `static/video-modal.js`
* **Diagnoza root-cause:** Wywoływanie `pollManifest` z wnętrza `renderTimelinePreview` na każdy `pointermove` z anonimową funkcją callback w połączeniu z natychmiastowym wywołaniem `onUpdate(entry.manifest)` przy obecności gotowych arkuszy w cache tworzyło pętlę synchroniczną re-entrancy (`render → pollManifest → callback → render`), prowadząc do błędu `RangeError: Maximum call stack size exceeded` oraz wycieku ponad 1500 listenerów na minutę.
* **Rozwiązanie:**
  1. Całkowicie oddzielono renderowanie od subskrypcji. Funkcja `render()` jest czystą procedurą matematyczno-prezentacyjną bez żądań sieciowych i bez subskrybowania callbacków.
  2. Wprowadzono jednoznaczny kontrakt `subscribe(videoKey, subscriberId, onUpdate)` oraz `unsubscribe(videoKey, subscriberId)`.
  3. W modalu (`modal-player-controls.js`) i na stronie `/watch` (`watch.html`) rejestracja subskrybenta (`'modal'` / `'watch'`) następuje jednokrotnie przy zdarzeniu `loadedmetadata`.
  4. Przy zamykaniu modalu lub opuszczaniu strony wywoływane jest precyzyjne wyrejestrowanie danego odbiorcy (`unsubscribe`), co chroni innych ewentualnych odbiorców tego samego wideo.
  5. Powiadomienia o gotowości przy subskrypcji są dyspaczowane asynchronicznie (`Promise.resolve()`), uniemożliwiając rekurencję na stosie wywołań.
* **Dowód:** 
  - `node audit/reproduce_p0_recursion_and_polling.cjs` — głębokość rekurencji: **1**, wyciek listenerów: **0**.
  - `python audit/regression_timeline_e2e_lifecycle.py` — w rzeczywistej przeglądarce Chromium potwierdzono brak błędu `RangeError`.

---

### P0-2 — Niepohamowany polling po zakończeniu budowy storyboardu
* **Pliki:** `static/timeline-preview.js`
* **Diagnoza root-cause:** W `ensureStoryboard` po ukończeniu generowania (`complete: true`) flaga `entry.fetching` przyjmowała wartość `false`, co przy kolejnych zdarzeniach `pointermove` skutkowało ponownym wysyłaniem żądań `POST /api/storyboard/v2/build` i `GET /api/storyboard/v2`.
* **Rozwiązanie:** Wprowadzono jednoznaczny stan cyklu życia `LIFECYCLE.COMPLETE`. Gdy storyboard osiągnie stan kompletności, `ensureStoryboard` natychmiast kończy działanie (`short-circuit`), nie wykonując żadnych żądań sieciowych (0 HTTP requests). Dodano deduplikację w locie (`entry.fetchPromise`) zapobiegającą równoległym zapytaniom.
* **Dowód:** 500 symulowanych zdarzeń `pointermove` w teście E2E w przeglądarce Chromium wygenerowało dokładnie **0 zbędnych żądań HTTP /build**.

---

### P1-01 & P2-04 — Egzekwowanie limitu procesów FFmpeg, QoS preemption i reset po anulowaniu
* **Pliki:** `player_qos_runtime.py`, `timeline_storyboard.py`
* **Diagnoza root-cause:** Wywłaszczanie procesów nie czekało na faktyczne zakończenie procesu potomnego (potencjalne procesy zombie), a stan `hard_cancel` po przerwaniu odtwarzania trwale blokował generowanie storyboardu przy powrocie użytkownika do tego samego materiału.
* **Rozwiązanie:**
  1. W `_mark_processes_preempted` wprowadzono bezpieczną, odporną na obiekty duck-typed sekwencję zakończenia: `proc.terminate() → proc.wait(0.5) → proc.kill() → proc.wait(0.5)`.
  2. Dodano `clear_cancel_state(video_id)`, która resetuje stan anulowania w momencie rejestracji nowej dzierżawy (`record_lease`) przy ponownym otwarciu/revisit materiału przez użytkownika.
  3. Import `subprocess` został dodany na poziomie modułu.
* **Dowód:** `audit/regression_v43_storyboard_scheduler.py` (wszystkie 9 asercji PASS) oraz test E2E weryfikujący reset flagi `hard_cancel`.

---

### P1-04 — Ekstrakcja klatek z preview_video.mp4 (Layer 0), poprawne indeksowanie i budżet QoS
* **Pliki:** `timeline_storyboard.py`
* **Diagnoza root-cause:** Przy częściowych niepowodzeniach ekstrakcji klatek z `preview_video.mp4` algorytm mapował klatki sekwencyjnie (0, 1, 2) zamiast ich rzeczywistych indeksów (`act_idx`), co generowało żądania do nieistniejących plików `frame_1.jpg` (błędy HTTP 404). Ponadto generowanie warstwy preview omijało semafor budżetu QoS.
* **Rozwiązanie:**
  1. Zapisywane i mapowane są rzeczywiste indeksy klatek (`act_idx`), zapewniając, że generowane URI kierują wyłącznie do faktycznie wygenerowanych plików na dysku.
  2. Ekstrakcja klatek preview została objęta budżetem arbitra QoS (`qos.wait_for_ffmpeg_budget("v2_prev", ...)`), co chroni przed omijaniem limitu procesów FFmpeg.
* **Dowód:** `audit/regression_audit_repair_p1_p2.py` (`test_p1_04_preview_video_layer0` PASS).

---

### P1-05 — Eliminacja samoodnawiania dzierżawy przez proces w tle
* **Pliki:** `timeline_storyboard.py`
* **Diagnoza root-cause:** Worker w tle odnawiał dzierżawę wewnątrz pętli przetwarzania (`record_lease` na początku zadania i po każdej partii), uniemożliwiając wygaśnięcie dzierżawy po opuszczeniu odtwarzacza przez użytkownika.
* **Rozwiązanie:** Usunięto wywołania `record_lease` z wnętrza `_execute_build_job`. Dzierżawę utrzymuje wyłącznie aktywny klient przez heartbeat (`GET /api/storyboard/v2` lub `POST /api/storyboard/v2/build`). Po zamknięciu odtwarzacza przez użytkownika dzierżawa wygasa (`has_live_lease` -> False), a worker przerywa przetwarzanie kolejnych partii.
* **Dowód:** `audit/regression_timeline_storyboard_v2.py`.

---

### P1-06 & P2-02 — Kontrakt niemutowalności arkuszy, wersjonowanie i bezpieczeństwo CORS
* **Pliki:** `main.py`, `timeline_storyboard.py`
* **Diagnoza root-cause:** Zapytanie o nieistniejącą rewizję `v` w `/api/storyboard/v2/sheet` zwracało stary plik `sheet_0.jpg` (cichy fallback) z nagłówkiem `immutable`, wprowadzając przekłamania w pamięci podręcznej przeglądarki.
* **Rozwiązanie:**
  1. Przy braku żądanej rewizji `v` endpoint zwraca prawidłowy kod `HTTP 404 Not Found`.
  2. Dla zapytań z rewizją zwracany jest nagłówek `Cache-Control: public, max-age=31536000, immutable`.
  3. Dla zapytań bez rewizji zwracany jest nagłówek `must-revalidate`.
  4. Nagłówek `Access-Control-Allow-Origin` nie używa niebezpiecznego wieloznacznika `*`.
* **Dowód:** `audit/regression_audit_repair_p1_p2.py` (`test_p2_02_p2_05_sheet_revision_and_security` PASS).

---

### Ochrona przed SSRF i wstrzykiwaniem protokołów w URL FFmpeg
* **Pliki:** `timeline_storyboard.py`
* **Diagnoza root-cause:** Parametry URL przekazywane do FFmpeg mogły potencjalnie posłużyć do skanowania sieci lokalnej lub usług loopback (np. Redis, SSH).
* **Rozwiązanie:**
  Wprowadzono funkcję `is_safe_ffmpeg_input_url()`, która:
  - Wymaga schematu `http` lub `https`.
  - Blokuje znaki CRLF i bajty zerowe w adresach i nagłówkach.
  - Dla loopbacku (`127.0.0.1`, `localhost`) zezwala wyłącznie na zaufany port serwera Archivebite (domyślnie 8000 lub `PORT`) oraz dokładną ścieżkę `/api/video/stream`.
  - Dla adresów zewnętrznych weryfikuje brak adresów prywatnych/chmurowych (`is_safe_remote_url`).
* **Dowód:** `audit/regression_audit_repair_p1_p2.py`.

---

## 3. POMIARY WYDAJNOŚCIOWE

Pomiary zrealizowano w rzeczywistym środowisku przeglądarki Playwright Chromium (`audit/regression_timeline_e2e_lifecycle.py`):

| Metryka | Wartość zmierzona | Kryterium akceptacji | Status |
| :--- | :--- | :--- | :--- |
| **Opóźnienie renderowania tooltipu (p50)** | **0.000 ms** (GPU transform) | < 1.0 ms | **PASS** |
| **Opóźnienie renderowania tooltipu (p95)** | **0.000 ms** | < 1.0 ms | **PASS** |
| **Opóźnienie renderowania tooltipu (p99)** | **0.100 ms** | < 2.0 ms | **PASS** |
| **Średnie opóźnienie (avg)** | **0.004 ms** | < 0.5 ms | **PASS** |
| **Żądania HTTP podczas 500 zdarzeń hover** | **0 żądań** | 0 żądań | **PASS** |
| **Limit równoległych procesów FFmpeg** | **Maksymalnie 2 procesy** | <= 2 procesy | **PASS** |
| **Błędy konsoli / Page errors w browserze** | **0 błędów** | 0 błędów | **PASS** |

---

## 4. INTEGRACJA CI

Wszystkie nowe testy regresyjne zostały zintegrowane w pliku `.github/workflows/ci.yml` we wszystkich odpowiednich macierzach zadań:
1. `offline-regression`:
   - `python audit/regression_timeline_storyboard_v2.py`
   - `node audit/regression_timeline_preview.cjs`
   - `python audit/regression_audit_repair_p1_p2.py`
   - `node audit/regression_audit_repair_frontend.cjs`
   - `node audit/reproduce_p0_recursion_and_polling.cjs`
2. `windows-contracts` oraz `windows-python314`:
   - Dodano pełny zestaw powyższych testów regresyjnych.
3. `browser-acceptance`:
   - `python audit/regression_timeline_browser.py`
   - `python audit/regression_timeline_e2e_lifecycle.py`

---

## 5. POZOSTAŁE OGRANICZENIA I UWAGI EKSPLOATACYJNE

1. **Formaty wideo źródeł zewnętrznych:** W przypadku materiałów o nietypowych klatkach kluczowych fallback z dokładnym seekowaniem (`fast=False`) może wymagać do 1.5 sekundy na ekstrakcję klatki przy pierwszym odczycie.
2. **Izolacja danych testowych:** Wszelkie testy regresyjne muszą być uruchamiane przez `audit/implementation_runner.py` w celu zapewnienia pełnej izolacji bazy danych SQLite i pamięci użytkownika.

---

## 6. KOŃCOWY WERDYKT

# **WERDYKT: READY**

Wszystkie wymagania stabilizacyjne, poprawki P0/P1/P2, kontrakty QoS, bezpieczeństwo SSRF oraz testy w rzeczywistej przeglądarce zostały w 100% zaimplementowane i pomyślnie zweryfikowane. Projekt spełnia Definition of Done.
