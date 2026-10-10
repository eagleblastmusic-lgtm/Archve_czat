# ARCHIVEBITE — FINAL EPOCH CONSISTENCY & VIDEOKEY ISOLATION VERIFICATION REPORT

**Data:** 2026-10-10  
**Status:** **PRODUCTION-READY / PASS**  
**Gałąź:** `fix/audit-repair-qos-timeline-v2`  
**Pull Request:** [GitHub PR #7](https://github.com/eagleblastmusic-lgtm/Archve_czat/pull/7)  

---

## 1. Identyfikacja commitów (Baseline & Final SHA)

- **Bazowy HEAD (Baseline SHA):** `00911f236dabe542fa3804e3fe009bc9492172c6`
- **Wdrożenie poprawek (Fix Commit SHA):** `60ab4923ddda3c9a4f6bfceff1533a99655e8589`
- **Docelowy PR na GitHubie:** `https://github.com/eagleblastmusic-lgtm/Archve_czat/pull/7`
- **Baza PR:** `main` (brak automatycznego merge zgodnie z kontraktem)

---

## 2. Przyczyny źródłowe (Root Cause Analysis)

### 2.1. P1: Niespójność numeracji epok po TTL/pruning (`timeline_storyboard.py` & `player_qos_runtime.py`)
- **Mechanizm defektu:** W `ConsumerLeaseManager._prune_expired_locked()` przy wygaśnięciu dzierżawy klucz wideo był bezwarunkowo usuwany z `self._epochs` i `self._retired_sessions`. Gdy ten sam film został ponownie otwarty przez użytkownika, `acquire()` nie znajdował wpisu w `_epochs` i rozpoczynał numerację od `epoch = 1`. Jednocześnie `player_qos_runtime.py` pamiętał `_active_generations[key] = max(prev, incoming) = 5`. W momencie opuszczenia filmu przez ostatniego konsumenta, wywołanie `qos.hard_cancel(..., generation=1)` napotykało warunek `if current_active (5) > generation (1): return 0`. Anulowanie było błędnie klasyfikowane jako „przestarzałe”, procesy FFmpeg nie były ubijane, a semafory QoS pozostawały zajęte.
- **Root Cause:** Asymetria cyklu życia pomiędzy pamięcią dzierżaw a rejestrem generacji QoS oraz brak synchronizacji generacji bazowej podczas ponownego `acquire()`.

### 2.2. P2: Brak izolacji `source` w workerze V2 (`timeline_storyboard.py`)
- **Mechanizm defektu:** W pętli `ProgressiveStoryboardService._worker_loop()` (linia 948) oraz pętli ekstrakcji klatek (linia 1227 i 1339) wywołanie `qos.is_hard_cancelled(job["key"].provider_id)` nie przekazywało parametru `source`. Funkcja `_key(video_id, source=None)` domyślnie przyjmowała źródło `archivebate`. Jeśli film z `archivebate` o danym ID został anulowany, worker V2 przy przetwarzaniu filmu z `camwhores` o identycznym ID odczytywał flagę anulowania `archivebate` i bezpodstawnie przerywał generowanie miniatur dla `camwhores`.
- **Root Cause:** Pominięcie parametru `source=key.source` w wewnętrznych punktach kontrolnych anulowania workera V2.

---

## 3. Wyniki reprodukcji RED (stan bazowy przed naprawą)

Przed zmianą kodu uruchomiono deterministyczny zestaw testów `audit/reproduce_epoch_and_source_isolation.py`:

```text
--- TEST A: Generation after natural expiration & pruning ---
Previous epoch: 2, New epoch: 1
AssertionError: FAIL: Epoch rolled back or failed to advance! Expected > 2, got 1

--- TEST D: Worker V2 source isolation ---
ab is_hard_cancelled (scoped): True
cw is_hard_cancelled (scoped): False
cw checked without source (as in baseline worker): True  <-- FAIL: Cross-source interference
```

Dowiedziono:
1. Po wygaśnięciu TTL i wykonaniu `_prune_expired_locked()` nowa sesja cofała licznik generacji do 1.
2. Następcze wywołanie `hard_cancel()` dla nowej sesji było ignorowane (`is_hard_cancelled: False`), co uniemożliwiało zakończenie procesu FFmpeg.
3. Pominięcie `source` powodowało fałszywie dodatnie rozpoznanie anulowania w drugim źródle.

---

## 4. Wprowadzone poprawki

### 4.1. player_qos_runtime.py
1. Dodano funkcję `get_active_generation(video_id, source=None) -> int`, umożliwiającą bezpieczne odpytanie autorytatywnego licznika generacji QoS pod blokadą `_lock`.
2. Dodano `evict_video_key(video_id, source=None) -> None`, usuwającą atomowo stan klucza w przypadku osiągnięcia globalnego limitu pojemności `max_tracked_keys`.

### 4.2. timeline_storyboard.py
1. **Ochrona epok przed wygasaniem dzierżaw:** W `_prune_expired_locked()` usunięto przedwczesne usuwanie `_epochs` i `_retired_sessions` przy wygaśnięciu pojedynczych dzierżaw.
2. **Kontrola pojemności i usuwanie najstarszych nieaktywnych epok:** Wprowadzono bezpieczny mechanizm czyszczenia wyłącznie przy przekroczeniu limitu `max_tracked_keys`:
   ```python
   if len(self._epochs) >= self.max_tracked_keys:
       active_set = set(self._leases.keys())
       removable = [k for k in self._epochs if k not in active_set]
       excess = max(1, len(self._epochs) - self.max_tracked_keys + 1)
       for k in removable[:excess]:
           self._epochs.pop(k, None)
           self._retired_sessions.pop(k, None)
           qos.evict_video_key(k)
   ```
3. **Monotoniczność generacji podczas `acquire()`:** Przy braku aktywnych dzierżaw lub zmianie sesji konsumenta, baza epoki jest wyznaczana jako `max(self._epochs.get(key_str, 0), qos.get_active_generation(key.provider_id, source=key.source)) + 1`. Gwarantuje to, że nowa sesja ZAWSZE otrzymuje generację ściśle wyższą od jakiejkolwiek poprzedniej generacji zarejestrowanej w systemie.
4. **Usunięcie martwego kodu:** Usunięto zduplikowany nagłówek klasy `ConsumerLeaseManager` (linie 104–109).
5. **Eksport funkcji modułowej:** Dodano `get_active_consumers_count(video_id, source=None)` na poziomie modułu `timeline_storyboard`.
6. **Pełna izolacja `source` w workerze V2:**
   - W `_worker_loop`: `qos.is_hard_cancelled(job["key"].provider_id, source=job["key"].source)`
   - W batch loop (`_execute_build_job`): `qos.is_hard_cancelled(key.provider_id, source=key.source)`
   - W batch loop: `self._wait_for_playback_qos(key.provider_id, source=key.source)`
   - W `_wait_for_playback_qos`: dodano parametr `source: Optional[str] = None` i przekazano do `qos.is_hard_cancelled(video_id, source=source)`.

---

## 5. Kontrakt generacji i polityka cleanupu

| Zdarzenie | Wpływ na `_leases` | Wpływ na `_epochs` | Wpływ na QoS `_active_generations` |
|---|---|---|---|
| Naturalne wygaśnięcie dzierżawy | Usunięcie wygasłego konsumenta | **Brak zmian** (zachowanie generacji) | **Brak zmian** |
| Ponowne otwarcie filmu (revisit) | Utworzenie nowego rekordu dzierżawy | Monotoniczny przyrost: `max(epoch, qos_gen) + 1` | Atomowa aktualizacja do nowej wartości `epoch` |
| Anulowanie sesji (`release`/`cancel`) | Usunięcie dzierżawy | Zachowanie epoki | Preempcja procesów o `gen <= cancel_gen` |
| Przekroczenie `max_tracked_keys = 1000` | Chronione aktywne dzierżawy | Usunięcie najstarszych **nieaktywnych** wpisów | Synchronizowana eksmisja przez `evict_video_key` |
| Restart serwera / czyszczenie pamięci | Pusta pamięć początkowa | Pusta pamięć początkowa | Pusta pamięć początkowa |

---

## 6. Wyniki testów jednostkowych, integracyjnych i E2E

### 6.1. Dedykowana regresja współbieżności i epok (`audit/reproduce_epoch_and_source_isolation.py`)
- **Test A:** Generacja po naturalnym wygaśnięciu & pruning — **PASS** (`prev_epoch: 2, new_epoch: 3`, natychmiastowe ubicie podprocesu przy `release`)
- **Test B:** Opóźnione anulowanie z synchronizacją `threading.Event` — **PASS** (nowa sesja B ochroniona przed opóźnionym anulowaniem sesji A)
- **Test C:** Wygasły lease i stare zapytania (renew/release/acquire) — **PASS** (stare operacje odrzucone)
- **Test D:** Izolacja źródeł w workerze V2 — **PASS** (anulowanie Archivebate nie blokuje Camwhores o tym samym ID)
- **Test E:** Rejestracja podprocesów i budżet QoS z izolacją źródeł — **PASS**
- **Test F:** Ograniczenie pamięci i usuwanie nadmiarowych wpisów — **PASS** (liczba epok ograniczona <= 50)

### 6.2. Wcześniejsza regresja sesji (`audit/reproduce_final_session_safety.py`)
- Wszystkie testy A–F: **6/6 PASS**

### 6.3. Pozostałe regresje lokalne
- `python -m compileall -q .` — **PASS**
- `python audit/regression_cross_session_lifecycle.py` — **5/5 PASS**
- `node audit/regression_cross_session_frontend.cjs` — **4/4 PASS**
- `python audit/regression_cross_session_playwright.py` — **10/10 PASS** (autentyczny przeglądarkowy Chromium/Edge)
- `python audit/regression_timeline_e2e_lifecycle.py` — **5/5 PASS**
- `python audit/regression_final_p1_remediation.py` — **4/4 PASS**

---

## 7. Wynik pełnego lokalnego CI (`audit/implementation_runner.py --full-ci`)

Uruchomiono pełny zestaw CI w odizolowanym katalogu tymczasowym (brak dostępu do danych użytkownika):

```text
RESULTS=C:\Projekty\Aplikacje\Archivebite_Czat\audit\implementation_2026-09-24\test_results.json
SUMMARY=128/128 passed
```

Zestawienie jobów:
- `offline-regression`: 35 testów — **PASS**
- `windows-contracts`: 47 testów — **PASS**
- `windows-python314`: 43 testy — **PASS**
- `browser-acceptance`: 3 testy E2E — **PASS**

---

## 8. Wyniki GitHub Actions dla commitów `60ab492` i `36c4ec8`

- **Pull Request:** [GitHub PR #7: fix(storyboard): final concurrency hardening, generation-safe cancellation, and lease protection](https://github.com/eagleblastmusic-lgtm/Archve_czat/pull/7)
- **Workflow Run (Fix Commit `60ab492`):** [Actions Run ID: 38057255817](https://github.com/eagleblastmusic-lgtm/Archve_czat/actions/runs/38057255817) — **SUCCESS (100% GREEN)**
- **Workflow Run (Final Commit `36c4ec8`):** [Actions Run ID: 38057402814](https://github.com/eagleblastmusic-lgtm/Archve_czat/actions/runs/38057402814) — **SUCCESS (100% GREEN)**:
  - `windows-python314` (ID 114228587208) — **SUCCESS** (1m18s)
  - `browser-acceptance` (ID 114228587244) — **SUCCESS** (1m39s)
  - `windows-contracts` (ID 114228587258) — **SUCCESS** (1m20s)
  - `offline-regression` (ID 114228587399) — **SUCCESS** (1m12s)

---

## 9. Pozostałe ryzyka

1. **Restart serwera podczas aktywnego generowania:** Nowo uruchomiony serwer rozpoczyna z pustą pamięcią operacyjną i epoką 1 dla każdego klucza. Podprocesy z poprzedniego procesu serwera nie istnieją (zostały zakończone przez system operacyjny), więc reset generacji po restarcie procesu jest całkowicie bezpieczny.
2. **Kompilacja i wersje FFmpeg:** Weryfikacja opiera się na bibliotece `imageio-ffmpeg` z poprawnym przekazywaniem argumentów wejściowych; brak zależności od systemowych binariów innych wersji.

---

## 10. Ostateczny werdykt

Oba zidentyfikowane problemy zostały precyzyjnie usunięte bez wprowadzania niepożądanych skutków ubocznych ani naruszenia wcześniejszych gwarancji współbieżności. Wszystkie testy lokalne, przeglądarkowe E2E oraz zdalne potoki GitHub Actions zakończyły się pełnym sukcesem.

**WERDYKT:** **PRODUCTION-READY / FULL PASS**
