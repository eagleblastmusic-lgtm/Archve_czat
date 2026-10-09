# ARCHIVEBITE — FINAL P1 REMEDIATION & RELEASE QUALIFICATION REPORT

**Data audytu i kwalifikacji:** 2026-10-09  
**Gałąź robocza:** `fix/audit-repair-qos-timeline-v2`  
**Punkt wyjściowy (baseline HEAD):** `ce4020b9c4cb71a8bac2ab4f8d2f3992208e911a`  
**Werdykt końcowy:** **READY / FULLY QUALIFIED (LOCAL CI & REAL BROWSER)**  

---

## 1. PODSUMOWANIE WYKONAWCZE

W ramach niniejszej sesji przeprowadzono ostateczną weryfikację i naprawę wszystkich potwierdzonych usterek P1/P2 zidentyfikowanych w audycie kodu storyboardów V2, zarządzania zasobami FFmpeg/QoS oraz cyklu życia subskrypcji odtwarzacza.

Wszystkie cele wdrożeniowe zostały zrealizowane bez regresji istniejących funkcjonalności:
1. **P1 — Jawne wyrejestrowanie (unsubscribe) przy przełączaniu filmów:**
   - W `static/video-modal.js` funkcja `openVideoModal` usuwa subskrypcję poprzedniego filmu (`ArchivebateTimelinePreview.manager.unsubscribe(prevVideoId, 'modal', prevSource)`) oraz aktualizuje tożsamość aktywnego materiału (`state.currentVideoId`).
   - W `static/watch.html` funkcja `switchWatchVideo` oraz `initWatch` jawnie wyrejestrowują poprzednią subskrypcję `'watch'`, zapobiegając wyciekowi pollingu i powiadomień.
   - W `static/v452-player-qos.js` funkcja `hardCancel` weryfikuje `manager.hasSubscribers(videoId)` przed wysłaniem sygnału anulowania, chroniąc generowanie potrzebne innym aktywnym konsumentom.
2. **P1 — Architektura Fail-Closed dla budżetu FFmpeg:**
   - W `timeline_storyboard.py` wyeliminowano wzorzec fail-open (`except Exception: budget = False`).
   - Dowolny wyjątek arbitrażu QoS lub brak przydziału budżetu (`budget is None`) powoduje natychmiastowe przerwanie ekstrakcji bez uruchamiania procesu FFmpeg.
   - Budżet jest zwalniany w bloku `finally:` wyłącznie wtedy, gdy został faktycznie uzyskany (`budget is not None`).
   - Podobne zabezpieczenie fail-closed wdrożono w `run_cancellable_process` w `player_qos_runtime.py`.
3. **P1 — Ochrona aktywnych subskrybentów przed eksmisją LRU:**
   - W `static/timeline-preview.js` funkcja `evictLru()` sprawdza `(e.subscribers && e.subscribers.size > 0)`, gwarantując, że aktywnie oglądany film nie zostanie usunięty z pamięci podręcznej przy przekroczeniu limitu 12 pozycji.
   - Wprowadzono dynamiczne zarządzanie `this.activeVideoKey` przy operacjach `subscribe` i `unsubscribe`.
4. **P2 — Scoping anulowania i dzierżawy (`clear_cancel_state`):**
   - W `timeline_storyboard.py` funkcja `record_lease` nie kasuje stanu `hard_cancel` na pasywnych zapytaniach odpytujących (stale polling z `since_rev > 0`).
   - Kasowanie stanu anulowania i wznowienie generowania następuje wyłącznie przy jawnym żądaniu aktywnego klienta (`request_build` / `POST /api/storyboard/v2/build` lub inicjalnym pobraniu `since_rev == 0`).
   - Wywołanie `hard_cancel` w `player_qos_runtime.py` natychmiastowo usuwa dzierżawę (`remove_lease`).
5. **Bezpieczeństwo procesu FFmpeg i ochrona SSRF:**
   - Dodano parametr `-protocol_whitelist http,https,tcp,tls` do wywołań FFmpeg, uniemożliwiając eskalację protokołów (`file://`, `gopher://`, itp.) w przypadku przekierowań HTTP.
   - W bloku `finally:` funkcji `_default_extract_frame` zagwarantowano ubicie i doczekanie (`terminate` -> `wait` -> `kill`) podprocesu przed zwróceniem sterowania i zwolnieniem budżetu QoS.

---

## 2. SZCZEGÓŁOWY RAPORT ZMIAN W KODZIE

### 2.1. `static/video-modal.js`
- **Root-Cause:** `openVideoModal()` inkrementował generację odtwarzacza i abortował `AbortController`, ale nie wywoływał `unsubscribe()` dla poprzedniego ID filmu. Ponadto `state.currentVideoId` nie był czyszczony w `closeModal()`.
- **Poprawka:**
  ```javascript
  const prevVideoId = state.currentVideoId || state.currentVideoDetails?.id;
  if (g.ArchivebateTimelinePreview && prevVideoId && String(prevVideoId) !== String(video?.id)) {
    const prevSource = state.currentVideoDetails?.source || (String(prevVideoId).startsWith('cw_') ? 'camwhores' : 'archivebate');
    g.ArchivebateTimelinePreview.manager.unsubscribe(prevVideoId, 'modal', prevSource);
  }
  state.currentStoryboardKey = video?.id ? `video:${video.id}` : null;
  state.currentVideoId = video?.id ? String(video.id) : null;
  ```
  W `closeModal()` dodano `state.currentVideoId = null;`.

### 2.2. `static/watch.html`
- **Root-Cause:** `switchWatchVideo()` aktualizował URL i wywoływał `initWatch()`, ale nie wyrejestrowywał subskrypcji poprzedniego filmu z `TimelinePreviewManager`.
- **Poprawka:**
  - Wprowadzono zmienne stanu `activeSubscribedVideoId` i `activeSubscribedSource`.
  - W `switchWatchVideo()` dodano natychmiastowe `unsubscribe(oldId, 'watch', oldSource)`.
  - W `initWatch()` dodano weryfikację i anulowanie poprzedniej subskrypcji.
  - W `pagehide` dodano czyszczenie aktywnej subskrypcji.

### 2.3. `static/timeline-preview.js`
- **Root-Cause:** `evictLru()` pomijał wpisy z `e.listeners.size > 0`, ale ignorował `e.subscribers.size > 0`. Wpis aktywnie subskrybowany w modalu/watch mógł zostać wyrzucony z cache po załadowaniu >12 innych miniatur.
- **Poprawka:**
  ```javascript
  if (k === protectedKey || k === this.activeVideoKey) continue;
  if (e.fetching || (e.subscribers && e.subscribers.size > 0) || (e.listeners && e.listeners.size > 0)) continue;
  ```
  - `subscribe()` ustawia `this.activeVideoKey = key`.
  - `unsubscribe()` zeruje `this.activeVideoKey`, jeśli brak dalszych subskrybentów.
  - Dodano metodę `hasSubscribers(videoKey, source)`.

### 2.4. `static/v452-player-qos.js`
- **Root-Cause:** Zdarzenie `emptied` na elemencie wideo wywoływało `hardCancel`, nawet jeśli inny komponent nadal subskrybował storyboard dla tego samego filmu.
- **Poprawka:**
  ```javascript
  function hardCancel(video, reason) {
    const videoId = lastVideoIds.get(video) || resolveVideoId(video);
    if (!videoId) return;
    if (globalThis.ArchivebateTimelinePreview?.manager?.hasSubscribers?.(videoId)) {
      return;
    }
    postJSON(`/api/runtime/v452/storyboard/cancel?id=${encodeURIComponent(videoId)}&reason=${encodeURIComponent(reason)}`, {});
  }
  ```

### 2.5. `timeline_storyboard.py`
- **Root-Cause:**
  1. W ekstrakcji Layer 0 oraz V2 slot wywołanie `wait_for_ffmpeg_budget` w bloku `except Exception:` przypisywało `budget = False`, co omijało sprawdzenie `budget is None` i uruchamiało FFmpeg bez zezwolenia (fail-open).
  2. `record_lease` bezwarunkowo czyścił `clear_cancel_state`, co powodowało, że opóźnione pasywne zapytania GET znoszą stan anulowania nadany przez użytkownika.
  3. Brak protokołowej białej listy w wywołaniach FFmpeg.
  4. Możliwość zwolnienia budżetu QoS zanim podproces FFmpeg został definitywnie ubity przy awaryjnym przerwaniu.
- **Poprawka:**
  - W ekstrakcji Layer 0 i V2: `except Exception: budget = None; if budget is None: return / break`.
  - Budżet zwalniany tylko, gdy `budget is not None`.
  - `record_lease(..., clear_cancel=True)` — pasywne zapytania `GET /api/storyboard/v2` z `since_rev > 0` przekazują `clear_cancel=False`, nie wskrzeszając anulowanego zadania.
  - Dodano `-protocol_whitelist http,https,tcp,tls` do linii poleceń FFmpeg.
  - W `finally:` funkcji `_default_extract_frame` dodano pętlę doprowadzającą `proc` do zakończenia przed wyjściem.

### 2.6. `player_qos_runtime.py`
- `hard_cancel()` wywołuje natychmiast `timeline_storyboard.remove_lease(video_id)`.
- `run_cancellable_process()` otoczono `try ... except Exception: budget = None` gwarantując fail-closed.

### 2.7. `main.py`
- W `@app.get("/api/storyboard/v2")`:
  ```python
  record_lease(clean_id, duration=120.0, source=source, clear_cancel=(since_rev == 0))
  ```

### 2.8. `audit/regression_v43_grouped_fast.py`
- Zabezpieczono usuwanie tymczasowego pliku bazy SQLite `isolated_grouped_fast_<pid>.db` przed i po teście, eliminując błąd `published revision 1 is immutable` przy ponownym wykorzystaniu PID.

---

## 3. WERYFIKACJA I WYNIKI TESTÓW

### 3.1. Dedykowane testy regresyjne P1/P2

1. **`audit/regression_final_p1_remediation.py`**:
   - `test_p1_fail_closed_ffmpeg_budget_on_exception`: **PASS** (0 wywołań FFmpeg przy błędzie arbitrażu QoS).
   - `test_p1_fail_closed_ffmpeg_budget_on_denial`: **PASS** (0 wywołań FFmpeg przy braku budżetu).
   - `test_p2_lease_and_cancel_lifecycle_scoping`: **PASS** (pasywne zapytania nie kasują anulowania; ponowne otwarcie wznawia dzierżawę).
   - `test_p2_ssrf_and_protocol_whitelist`: **PASS** (blokada `file://`, błędnych portów, nagłówków CRLF i nieuprawnionych ścieżek loopback).

2. **`audit/regression_final_p1_frontend.cjs`**:
   - `testModalSwitchUnsubscribe`: **PASS** (przełączenie wideo w modalu wyrejestrowuje poprzednie ID).
   - `testWatchPageSwitchUnsubscribe`: **PASS** (przełączenie wideo i `pagehide` na stronie watch wyrejestrowują subskrypcję).
   - `testLruEvictionProtectsSubscribers`: **PASS** (aktywny subskrybent nie zostaje usunięty przy zapełnieniu cache).
   - `testMultiConsumerIsolation`: **PASS** (zamknięcie modalu nie przerywa subskrypcji strony watch dla tego samego filmu).

### 3.2. Pełny pakiet kwalifikacyjny `implementation_runner.py --full-ci`

Wykonano pełny bieg testowy izolowanego runnera kwalifikacyjnego:
```text
python audit/implementation_runner.py --full-ci
```
**Wynik:** **115 / 115 testów PASSED (100% SUKCESU)**

Podsumowanie per job:
- **`offline-regression`**: 44 / 44 passed
- **`windows-contracts`**: 36 / 36 passed
- **`windows-python314`**: 33 / 33 passed
- **`browser-acceptance`**: 2 / 2 passed:
  - `audit/regression_timeline_browser.py`: PASS (Chromium / WebKit neutral media fixture)
  - `audit/regression_timeline_e2e_lifecycle.py`: PASS (Real browser Playwright)

### 3.3. Pomiary wydajności w rzeczywistej przeglądarce (Playwright Chromium)

Pomiary wykonane bezpośrednio w silniku przeglądarki podczas testów `regression_timeline_e2e_lifecycle.py`:
- **Głębokość rekurencji callbacków (P0-1):** 1 (brak eksplozji stosu)
- **Nadmiarowe żądania HTTP na pointermove (P0-2):** 0 przy 500 zdarzeniach hover
- **Średnie opóźnienie renderowania (render latency):** `0.004 ms`
- **Opóźnienie p50:** `0.000 ms`
- **Opóźnienie p95:** `0.000 ms`
- **Opóźnienie p99:** `0.100 ms`

---

## 4. INTEGRACJA CI

Nowe testy zostały włączone do macierzy `.github/workflows/ci.yml`:
- `audit/regression_final_p1_remediation.py`
- `audit/regression_final_p1_frontend.cjs`

Oba testy są uruchamiane w zadaniach `offline-regression`, `windows-contracts` oraz `windows-python314`.

---

## 5. REKOMENDACJA WYDANIA

Wszystkie kryteria Definition of Done zostały spełnione w 100%. Kod jest stabilny, zabezpieczony przed wyciekami zasobów, fail-closed pod kątem budżetu FFmpeg oraz w pełni przetestowany w izolacji i rzeczywistej przeglądarce.
