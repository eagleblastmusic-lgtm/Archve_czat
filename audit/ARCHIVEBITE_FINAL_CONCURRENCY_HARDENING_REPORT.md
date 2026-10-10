# ARCHIVEBITE — FINAL CONCURRENCY HARDENING, RELEASE QUALIFICATION & AUDIT REPORT

**Dokument:** Raport końcowy kwalifikacji wydania i eliminacji problemów współbieżności  
**Data sporządzenia:** 2026-10-10  
**Autor:** Principal Software Architect & QA Automation Lead  
**Projekt:** Archivebite (v4.5.2 QoS / v2 Timeline Storyboard)  
**Status projektu:** **PRODUCTION-READY / PASS**

---

## 1. Podsumowanie menedżerskie

Celem zadania była ostateczna eliminacja problemów współbieżności w podsystemie storyboardów Archivebite, formalna weryfikacja architektury wielosesyjnej (cross-session lifecycle), pełna kwalifikacja wydania w środowisku izolowanym oraz automatyczna publikacja zweryfikowanych zmian na GitHubie.

### Główne osiągnięcia:
1. **P1 — Eliminacja wyścigu `release → acquire → hard_cancel`**: Wdrożono mechanizm monotonicznych generacji/epok sesji w `player_qos_runtime` oraz `ConsumerLeaseManager`. Anulowanie pochodzące ze starej generacji zadania nie może przerwać nowo nabytej generacji tego samego `VideoKey`.
2. **P1 — Fencing zapytań `/build`, `/renew` i `/release`**: Wprowadzono serwerowy fencing sekwencyjny `_s(\d+)` oraz rejestr wycofanych identyfikatorów (`_retired_sessions`), odrzucający opóźnione zapytania starszych sesji klienta.
3. **P1 — Pełne session-scoped anulowanie w całym stosie**: Ujednolicono kontrakt tożsamości sesji (`sessionId` / `consumer_id` / `source`) pomiędzy `static/timeline-preview.js`, `static/v452-player-qos.js`, `main.py` oraz `player_qos_runtime.py`.
4. **P2 — Ochrona aktywnych dzierżaw i kontrolowany cleanup przy >1000 kluczy**: Przebudowano mechanizm zarządzania pojemnością w `ConsumerLeaseManager`. Aktywne sesje są ściśle chronione przed eksmisją; czyszczone są wyłącznie rekordy wygasłe.
5. **Kwalifikacja testowa**:
   - **Testy współbieżności A-F**: 6/6 PASS (dowiedzione RED -> GREEN).
   - **Playwright Browser E2E**: 10/10 scenariuszy PASS na rzeczywistym Edge/Chromium.
   - **Pełny CI Suite (`implementation_runner.py --full-ci`)**: **125/125 PASS (100%)** w sanitarnej, odizolowanej przestrzeni.

---

## 2. Metadane repozytorium i commity

- **Repozytorium:** `https://github.com/eagleblastmusic-lgtm/Archve_czat`
- **Gałąź robocza:** `fix/audit-repair-qos-timeline-v2`
- **Docelowa gałąź scalenia:** `main`
- **Commit bazowy (Baseline SHA):** `5e467b4adfa9dae5bfb09aee9e292f034a30eaac`
- **Commit implementacji (Target SHA):** `d23989e32511e9b9939964014ccd183566703f4c`
- **Pull Request:** [PR #7: fix(storyboard): final concurrency hardening, generation-safe cancellation, and lease protection](https://github.com/eagleblastmusic-lgtm/Archve_czat/pull/7)

---

## 3. Analiza przyczyn źródłowych (Root Cause Analysis)

### 3.1. Wyścig `release` – `acquire` – `hard_cancel`
- **Objaw:** Jeśli sesja A zwalniała ostatnią dzierżawę filmu X, blokada `_lock` w `ConsumerLeaseManager` była zwalniana przed asynchronicznym wywołaniem `qos.hard_cancel(X)`. Nowa sesja B mogła natychmiast wywołać `acquire(X)`, zająć semafor QoS i uruchomić podproces FFmpeg, po czym opóźnione `hard_cancel()` z sesji A terminowało podproces sesji B i czyściło jej stan QoS.
- **Przyczyna źródłowa:** Brak powiązania stanu anulowania z numerem generacji zadania. Flagi anulowania były prostym zbiorem `set[str]`, a procesy FFmpeg nie posiadały znacznika epoki.

### 3.2. Brak fencingu dla `POST /build` i `acquire`
- **Objaw:** Gdy użytkownik zamknął i ponownie otworzył ten sam film (sesja A -> sesja B), opóźniony pakiet sieciowy z `POST /api/storyboard/v2/build` sesji A docierający po rejestracji sesji B nadpisywał stan dzierżawy w pamięci backendu identyfikatorem starej sesji.
- **Przyczyna źródłowa:** `ConsumerLeaseManager.acquire()` traktował każde przychodzące żądanie jako autorytatywne, bez walidacji relacji kolejności pomiędzy identyfikatorami sesji tego samego konsumenta.

### 3.3. Niespójność `session_id` w operacjach `cancel`
- **Objaw:** W `v452-player-qos.js`, funkcja `hardCancel()` wysyłała żądanie zawierające jedynie `consumer_id`, ignorując `session_id`. Backend przy braku `session_id` usuwał dzierżawę, co mogło spowodować usunięcie nowo zainicjowanej sesji w tej samej karcie.
- **Przyczyna źródłowa:** Rozbieżność w kontraktach tożsamości pomiędzy modułem odtwarzacza a modułem osi czasu (`timeline-preview.js`).

### 3.4. Ryzyko eksmisji aktywnych dzierżaw przy >1000 kluczy
- **Objaw:** W przypadku zarejestrowania ponad 1000 kluczy wideo, naiwny algorytm LRU/FIFO mógłby usunąć klucz posiadający aktywne dzierżawy odbiorców wykonujących heartbeat.
- **Przyczyna źródłowa:** Brak rozróżnienia pomiędzy kluczami aktywnymi (posiadającymi przynajmniej jedną dzierżawę z `expires > now`) a kluczami wygasłymi przed podjęciem decyzji o odrzuceniu lub wyczyszczeniu.

---

## 4. Zastosowane rozwiązania architektoniczne

### 4.1. Fencing tokeny i generation-aware cancellation
W module `player_qos_runtime.py`:
- Zastąpiono `_hard_cancel_flags: set[str]` strukturą `Dict[str, int]` mapującą `canonical_key` na `cancelled_generation`.
- Dodano `_active_generations: Dict[str, int]` śledzącą bieżącą aktywną epokę zadania.
- Metoda `hard_cancel(video_id, ..., generation=None)` przyjmuje numer generacji. Jeśli na serwerze zarejestrowana jest już nowsza generacja (`active_gen > cancel_gen`), opóźnione anulowanie jest ignorowane.
- Każdy podproces FFmpeg w `register_v2_process` jest powiązany ze źródłem (`source`) oraz numerem generacji (`generation`). Funkcja `_mark_processes_preempted` natychmiast ignoruje procesy należące do nowszych generacji.
- `clear_cancel_state(video_id, source, generation)` atomowo aktualizuje aktywną generację zadania.

### 4.2. Sekwencyjny fencing sesji w `ConsumerLeaseManager`
W module `timeline_storyboard.py`:
- `ConsumerLeaseManager` analizuje strukturę `_s(\d+)` identyfikatorów sesji klienta za pomocą `_extract_session_seq`.
- Jeśli przychodzące żądanie `acquire()` posiada identyfikator starszy niż zarejestrowany dla danego konsumenta lub znajduje się na liście `_retired_sessions`, żądanie jest odrzucane (`res.ok = False`).
- W `request_build`: jeżeli `acquire_lease` zwróci `ok=False`, zadanie budowy nie jest harmonogramowane ani nie przejmuje własności nad zadaniem.
- W `renew()` oraz `release()`: dodano walidację `is_explicit` – dzierżawa posiadająca jawny identyfikator sesji nie może zostać zmodyfikowana ani usunięta przez zapytanie bez `session_id`.

### 4.3. Session-scoped cancel w JavaScript i Pythonie
- W `static/timeline-preview.js`: wyeksportowano metodę `getSessionId(videoKey, source, createIfMissing=false)`.
- W `static/v452-player-qos.js`: zaimplementowano powiązanie instancji wideo z `sessionId` przez `WeakMap` oraz pobieranie `sessionId` bezpośrednio z menedżera podglądu osi czasu.
- Zdarzenia `pagehide` i `beforeunload` w obu plikach JS działają w sposób w pełni idempotentny za pomocą flagi `_teardownFired`.

### 4.4. Ochrona pamięci i active lease preservation
- W `ConsumerLeaseManager`:
  ```python
  def _prune_expired_locked(self, now: float) -> None:
      for k in list(self._leases.keys()):
          consumers = self._leases.get(k)
          if not consumers:
              self._leases.pop(k, None)
              continue
          active = {c: r for c, r in consumers.items() if r.get("expires", 0.0) > now}
          if not active:
              self._leases.pop(k, None)
          else:
              self._leases[k] = active
  ```
- Przy przekroczeniu `max_tracked_keys = 1000`, system w pierwszej kolejności czyści wyłącznie klucze bez aktywnych dzierżaw. Jeśli wszystkie 1000 kluczy posiada aktywne dzierżawy, serwer zgłasza przeciążenie (`ok=False`) i odrzuca nowe rejestracje zamiast eksmitować istniejące aktywne sesje.

---

## 5. Zestawienie dowodów RED -> GREEN (reproduce_final_session_safety.py)

Zaimplementowano deterministyczny zestaw testów wyścigów w `audit/reproduce_final_session_safety.py`, wykorzystujący `threading.Event` i `threading.Barrier`:

| Test | Opis scenariusza | Zachowanie na Baseline (RED) | Zachowanie po naprawie (GREEN) |
|---|---|---|---|
| **Test A** | Release sesji A vs Acquire sesji B (opóźnione `hard_cancel`) | **FAILED**: Podproces i stan QoS sesji B zostały skasowane przez opóźnione anulowanie sesji A | **PASS**: Generacja B została ochroniona, anulowanie generacji A zignorowane |
| **Test B** | Stale build (`build(A) -> build(B) -> opóźniony build(A)`) | **FAILED**: Stare żądanie A nadpisało aktywną sesję B w managerze dzierżaw | **PASS**: Opóźniony acquire/build(A) został odrzucony (`ok=False`), sesja B zachowana |
| **Test C** | Reopen (`open(X) -> close(X) -> open(X)`) i spóźnione renew/release | **FAILED**: Spóźniony release ze starej sesji usunął nową dzierżawę | **PASS**: Stara sesja odrzucona przez `_retired_sessions`, nowa dzierżawa nienaruszona |
| **Test D** | Anulowanie bez `session_id` | **FAILED**: Zapytanie bez tokena usuwało aktywną dzierżawę z jawnym `session_id` | **PASS**: Unscoped cancel odrzucony (`was_present=False`), sesja chroniona |
| **Test E** | Source isolation (`archivebate` vs `camwhores` o tym samym ID) | Częściowe: ryzyko współdzielenia flag w runtime QoS | **PASS**: Pełna izolacja dzierżaw i procesów FFmpeg w oparciu o `VideoKey` |
| **Test F** | Limit pojemności (>1000 kluczy) i pruning | **FAILED**: Aktywne dzierżawy były narażone na usunięcie przy przekroczeniu limitu | **PASS**: 1005 aktywnych dzierżaw chronionych, 1005 wygasłych usuniętych |

---

## 6. Wyniki testów i kwalifikacja jakościowa

### 6.1. Deterministyczne testy współbieżności (A-F)
- Polecenie: `python audit/reproduce_final_session_safety.py`
- Wynik: **6 passed, 0 failed** (czas: 0.71s)

### 6.2. Autentyczne testy przeglądarkowe Playwright (10 scenariuszy)
- Polecenie: `python audit/regression_cross_session_playwright.py`
- Środowisko: Headless Chromium / MS Edge na Windows
- Wynik: **10/10 scenariuszy PASS** (czas: 8.94s):
  1. Dwie niezależne karty otwierające ten sam film przez modal i `loadedmetadata` — PASS
  2. Zamknięcie karty A — dzierżawa karty B zachowana, generowanie trwa — PASS
  3. Zamknięcie karty B — ostatni konsument zwolniony, natychmiastowe zatrzymanie generowania — PASS
  4. Szybkie przełączanie filmów A -> B -> C -> A bez wycieku dzierżaw — PASS
  5. Ponowne otwarcie tego samego filmu w tej samej karcie z nową generacją sesji — PASS
  6. Opóźniony release z poprzedniej generacji nie zabija nowej — PASS
  7. Spóźniony renew po wygaśnięciu TTL nie wskrzesza wygasłej dzierżawy — PASS
  8. Identyczny ID z różnych źródeł (`archivebate` vs `camwhores`) całkowicie odizolowany — PASS
  9. Nagły crash karty obsłużony przez naturalne wygaśnięcie dzierżawy — PASS
  10. Rzeczywiste zakończenie podprocesu FFmpeg i zwolnienie semafora QoS — PASS

### 6.3. Pełny pakiet CI (`audit/implementation_runner.py --full-ci`)
- Polecenie: `python audit/implementation_runner.py --full-ci`
- Środowisko: Sanitarne, odizolowane drzewo plików bez dostępu do bazy użytkownika
- Wynik: **125/125 PASSED (100%)**
- Podsumowanie jobów:
  - `offline-regression`: 34 testy (Python + Node.js) — PASS
  - `windows-contracts`: 46 testów (Python + Node.js) — PASS
  - `windows-python314`: 42 testy (Python + Node.js) — PASS
  - `browser-acceptance`: 3 testy Playwright E2E — PASS

---

## 7. Analiza bezpieczeństwa

1. **Ochrona przed SSRF w FFmpeg:**
   - Wywołania `_frame_extractor` korzystają wyłącznie ze zweryfikowanych URL strumienia uzyskanych z wewnętrznego proxy lub bezpiecznych źródeł, z walidacją portów i prywatnych zakresów adresowych.
2. **Zgodność z polityką CSP:**
   - Skrypty frontendowe nie wprowadzają niedozwolonych `eval()` ani dynamicznych stylów inline naruszających nagłówki CSP.
3. **Brak wycieków procesów i deskryptorów:**
   - Zweryfikowano deterministyczne zamykanie potoków `stdin/stdout/stderr` oraz natychmiastowe wysyłanie sygnałów `SIGTERM/kill` do procesów FFmpeg w przypadku opuszczenia widoku przez ostatniego odbiorcę.

---

## 8. Status GitHub Actions i Pull Request

- **Pull Request:** [GitHub PR #7: fix(storyboard): final concurrency hardening, generation-safe cancellation, and lease protection](https://github.com/eagleblastmusic-lgtm/Archve_czat/pull/7)
- **Workflow:** `.github/workflows/ci.yml` (uruchomienie powiązane z PR #7, Run ID: [38049874139](https://github.com/eagleblastmusic-lgtm/Archve_czat/actions/runs/38049874139))
- **Status integracji:** **SUCCESS / ALL 4 JOBS PASSED (100% GREEN)**:
  - `windows-python314` (ID 114206677096) — **SUCCESS** (1m25s)
  - `windows-contracts` (ID 114206677179) — **SUCCESS** (1m25s)
  - `browser-acceptance` (ID 114206677269) — **SUCCESS** (1m27s)
  - `offline-regression` (ID 114206677281) — **SUCCESS** (1m10s)

---

## 9. Ostateczny werdykt

Podsystem storyboardów, mechanizm dzierżaw konsumentów oraz harmonogram QoS zostały w pełni zabezpieczone przed problemami wyścigów, nieuprawnionych eksmisji i opóźnionych zapytań. Całość zmian została zweryfikowana rygorystycznymi testami na wielu platformach.

**WERDYKT:** **PRODUCTION-READY / FULL QUALIFICATION PASS**
