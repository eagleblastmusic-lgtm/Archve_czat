# Archivebite — Końcowy Protokół Wydania (Release Final)

Data wydania: **3 października 2026**.  
Status kwalifikacji: **FINAL RELEASE COMPLETE**.  

---

## 1. Identyfikatory i metadane wydania

- **Gałąź kanoniczna (default branch):** `main`
- **Pierwotny zweryfikowany RC SHA:** `b950c040ebe2c98019ba0f2a34f6e1fa85e4edcb`
- **SHA dokumentacyjnego commita RC:** `4801cdc9d4567406a6c4df1997d4dfbf94df8ce5`
- **Numer Pull Requesta:** [PR #6](https://github.com/eagleblastmusic-lgtm/Archve_czat/pull/6)
- **Merge SHA (scalenie RC do `main`):** `62b4415894e68a3b4799df0e9c354610143a1c85`
- **Wcześniejszy CI Run RC:** [CI Run 37076896780](https://github.com/eagleblastmusic-lgtm/Archve_czat/actions/runs/37076896780) (4/4 PASS)
- **Post-merge CI Run (PR #6 merge do `main`):** [CI Run 37078011104](https://github.com/eagleblastmusic-lgtm/Archve_czat/actions/runs/37078011104) (4/4 PASS)
- **Oficjalny tag wydania:** `archivebite-2026-10-03`

---

## 2. Podsumowanie wyników automatycznych bramek jakościowych

| Bramka | Środowisko | Wynik | Szczegóły dowodowe |
| --- | --- | --- | --- |
| **Lokalny runner pełny (`--full-ci`)** | Windows (izolacja sanitarna) | **93/93 PASS** | 48 offline-regression, 24 windows-contracts, 20 windows-python314, 1 browser-acceptance |
| **Linux Chromium Playwright** | Ubuntu Latest (Python 3.13) | **PASS** | Run 37076896780 (1m 45s), Run 37078011104 (1m 41s); artefakt `neutral-browser-evidence` |
| **Windows CI Python 3.13** | Windows Latest (Node 22) | **PASS** | Kontrakty QoS, security, single-flight, storage rollback |
| **Windows CI Python 3.14** | Windows Latest (Node 22) | **PASS** | Catalog concurrency, lock closure, test_suite |
| **Natywny Desktop (pywebview/WebView2)** | Windows 10 | **PASS** | 2 pełne cykle na tym samym porcie; port released, locks released, FFmpeg exited (21.01s) |
| **Profil wydajności FTS5 / F12** | Windows 10 | **PASS** | 440 tys. rekordów; rzadkie zapytanie p50 0.46 ms, p95 0.85 ms; heartbeat pod obciążeniem < 55 ms |

---

## 3. Integralność i ochrona danych użytkownika

Niniejszym potwierdza się, że w trakcie całego procesu audytu, implementacji, testów lokalnych i weryfikacji wydania:
- **Plik `.env.local`** oraz konfiguracja środowiskowa nie zostały zmodyfikowane ani usunięte.
- **Kredencjały użytkownika (`data/credentials.local.json`)** i cookies sesyjne pozostały nienaruszone.
- **Lokalna baza użytkownika (`data/catalog.db`)** oraz magazyn konta (`data/user_store.json`) nie były poddawane mutacjom ani usunięciom; wszystkie testy wykonywano w izolowanych, tymczasowych kopiach (`ARCHIVEBATE_ISOLATED_CHECKOUT`).
- **Żadne operacje sieciowe** nie wykonywały zapytań do prywatnego konta użytkownika w serwisach zewnętrznych.

---

## 4. Status kryteriów manualnych (MANUAL_REQUIRED)

Kryteria programistyczne i kontrakty DOM/CSS/JS zostały w 100% automatycznie zweryfikowane, natomiast fizyczna percepcja ułatwień dostępu wymaga odbioru manualnego przed ostateczną publikacją binarną:

1. **Czytnik ekranu (NVDA / Windows Narrator):** Odsłuch anonsów toastów (`role="status"`) oraz etykiet pól skoku (`aria-label="Numer strony"`).
2. **Skalowanie 200% Zoom / Reflow:** Brak nachodzenia elementów i ucinania tekstu w widoku 960x640 przy powiększeniu tekstu.
3. **Tryb wysokiego kontrastu (Forced Colors):** Wyraźne kontury kart i widoczność fokusa w motywie systemowym Canvas.
4. **Percepcja braku animacji:** Potwierdzenie wyłączenia shimmerów skeletonów przy systemowym `prefers-reduced-motion: reduce`.
5. **Prawdziwe konto zewnętrzne:** Ręczne potwierdzenie poprawności synchronizacji z kontem rzeczywistym dostawcy.

---

## 5. Podsumowanie i status końcowy

Wszystkie wymagania techniczne wydania zostały spełnione w sposób powtarzalny, hermetyczny i udokumentowany. Wydanie zostało zatwierdzone i scalone do głównej gałęzi `main`.
