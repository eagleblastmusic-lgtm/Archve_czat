# Archivebite — wykonanie planu poprawek

Data: 3 października 2026. Użytkownik jawnie zlecił wykonanie planu z `AUDYT_I_PLAN_POPRAWEK_2026-10-02.md`; dokument audytu został potraktowany jako specyfikacja tego wdrożenia, a nie jako instrukcja samoczynna. Oryginalny plan, dane konta, `.env.local`, credentials, katalog SQLite i cache użytkownika nie zostały nadpisane.

## Wynik

Wdrożono zakres F01–F13, R01–R02 oraz U01–U02 w istniejących właścicielach danych i UI. Najważniejsze granice są teraz jawne: lokalny commit ma osobny wynik od synchronizacji zdalnej, synchronizacja nie wskrzesza tombstone'ów, restore zwiększa generację, „Mój katalog” nie uruchamia automatycznych operacji źródłowych, a ciężkie odczyty konta pracują poza pętlą ASGI.

Do przeglądu pozostaje odbiór w środowisku, które może uruchomić natywny proces Playwright i pywebview. Lokalne kontrole aplikacji zakończyły się wynikiem 92/93: 92 komendy przeszły; jedyna nieudana komenda zakończyła się przed startem Playwrighta błędem Windows `WinError 5` przy tworzeniu kanału procesu. Nie jest to wynik testu aplikacji. Nie wykonano logowania do prawdziwego konta, testów zewnętrznych dostawców ani manualnego odbioru czytnika ekranu.

## Zrealizowane pozycje

| Zakres | Wykonanie |
| --- | --- |
| F01 | `VideoKey`, absolutny stan docelowy ulubionego, trwałe `operation_id`, CAS dla późnej odpowiedzi, serializacja zdalnego zapisu oraz odczyt–mutacja–ponowny odczyt w scraperze. Niepotwierdzone wykonanie pozostaje `unknown`. |
| F02 | Merge chroni nowsze zamiary i tombstone'y, potwierdzenie wymaga obserwowanego `operation_id` i kompletnego źródła, a zmiana projekcji zwiększa `preferences_version`. |
| F03 | Wszystkie czytniki magazynu korzystają z blokady właściciela; projekcja, liczniki i wersja powstają z jednego zatwierdzonego snapshotu. Ciepła projekcja jest memoizowana i unieważniana przy każdym zapisie/restore/load. |
| F04 | Restore nadaje monotoniczne `preferences_version` i `restore_generation`; preview pokazuje zastąpienie magazynu oraz różnice kolekcji i blokad. Błąd zapisu odtwarza pamięć. |
| F05 | Wyniki lokalne mają jawny zakres, miniatury używają wyłącznie cache przy automatycznym renderowaniu, a probe dostępności, storyboard i media zaczynają się dopiero przy świadomym odtwarzaniu. |
| F06 | `author_filter` (`all`, `only_fav`, `exclude_fav`) jest stosowany w SQLite przed liczeniem i paginacją, z filtrem źródła, blokadami i przypiętą wersją preferencji. |
| F07 | Uzgadnianie pojedynczej tożsamości korzysta z endpointu `/api/account/favorites/state`, więc nie ma limitu pierwszych 1000 elementów ani kolizji ID między źródłami. |
| F08 | Nawigacja do konta unieważnia generację widoku, abortuje kontrolery, zamyka SSE i kończy pracę prefetchu; późne dane i błędy nie renderują się w nowej powierzchni. |
| F09 | Synchronizacja raportuje fazę, kompletność i terminalny wynik; relogin nie udaje zakończonego syncu; historia w modalu i `/watch` ma jeden komunikat z retry; stan zdalny ulubionego jest widoczny osobno. |
| F10 | Toast akcji przechowuje faktyczny callback i `expiresAt`; cofnięcie blokady jest dostępne przez deklarowane 10 sekund i wygasa razem z akcją. |
| F11 | Wspólny focus manager domyka dialogi, obsługuje Shift+Tab/Escape i przywraca fokus; pola skoku mają etykiety; reduced motion i forced colors mają reguły CSS; test browserowy obejmuje oba playery. |
| F12 | Endpointy konta używają `asyncio.to_thread`; endpointy diagnostyczne i blokad mają lekkie projekcje. Profil 3k/10k oraz cztery równoległe żądania został zapisany w `evidence_plan_2026-10-03/user_search_profile.json`. |
| F13 | Lock zawiera warunkowe `pythonnet`, `clr-loader`, `cffi` i `pycparser`. Czyste środowiska Windows Python 3.13 i 3.14 przechodzą `pip check`, kontrolę domknięcia i `ffmpeg -version`. |
| R01 | Launcher web i Desktop czekają na marker gotowości własnego runtime; Desktop posiada własny uchwyt `uvicorn.Server`, kontrolowane zamknięcie lifespan, join i brak obsługi obcych procesów. |
| R02 | Regresja single-flight używa wiernego cache i kontrolowanego wejścia współbieżnego; bramka browserowa jest dodana do CI jako osobny job z Playwrightem i artefaktami. |
| U01 | Font Awesome 6.5.1 jest dostarczony lokalnie w `static/vendor/fontawesome` wraz z licencją i README; zmienione style watch używają istniejących tokenów. |
| U02 | `CatalogService` buduje leniwy, pochodny FTS5 trigram index z triggerami INSERT/UPDATE/DELETE i zachowuje istniejący predykat `LIKE` jako autorytet semantyczny. Dla częstych fraz pozostaje bezpieczny fallback skanu. |

## Aktualne kontrole

- [Pełny lokalny runner — 92/93](repair_results_plan_full_final_2026-10-03/test_results.json): compileall, lock, projekcja użytkownika, bezpieczeństwo, katalog, storyboard, wszystkie regresje Node/Python i unit tests przeszły. `browser-acceptance` nie utworzył procesu Playwrighta na tym hoście (`WinError 5`).
- [Projekcja i wielowątkowość — PASS 1/1](repair_results_plan_projection2_2026-10-03/test_results.json), ponowienie po wcześniejszym flake'u heartbeat.
- [Profil F12/U02 — PASS 1/1](repair_results_plan_profile_final_2026-10-03/test_results.json) oraz [dane profilu](evidence_plan_2026-10-03/user_search_profile.json). Na 440 tys. rekordów: pierwsza budowa indeksu i wyszukanie 18,731 s; ciepłe `special_rare_term` p50 3,387 ms; brak wyników p50 0,833 ms; autor p50 32,382 ms; fraza częsta p50 1,129 s. W pomiarze nie opróżniano cache systemu plików.
- [Czysta instalacja Python 3.13/3.14](repair_results_plan_final_lock_2026-10-03/test_results.json): `pip check` — `No broken requirements found`; SQLite 3.50.4; FFmpeg 7.1 z `imageio-ffmpeg`. Szczegóły są w [evidence](evidence_plan_2026-10-03/dependency_install_evidence.json).
- [Próba runtime na czystym Python 3.13 — PASS](repair_results_plan_py313_runtime2_2026-10-03/test_results.json) oraz [projekcja na 3.13 — PASS](repair_results_plan_py313_projection_2026-10-03/test_results.json).
- [Natywny Desktop — wcześniejszy PASS](repair_results_plan_desktop4_2026-10-02/test_results.json) i [zapis przebiegu](evidence_plan_2026-10-03/desktop_lifecycle_result.json): neutralny WebView2, realny odczyt mediów, focus, zwolnienie portu/WAL/locków/procesów i restart tego samego portu. Ponowienie na bieżącym hoście zakończyło się timeoutem dziecka po 100 s, dlatego wymaga ponowienia w stabilnym środowisku Desktop.
- `git diff --check` i `python -m compileall -q .` przechodzą. Manifest źródła zapisany przez końcowy runner (przed dodaniem raportu): `10d4e8030de419738773fe126c47ef200e4086fc3d84c656cae8250abd115828`; punkt bazowy Git pozostaje `6c2065b0d2eee9a0fce3986185ff1731e3869991` z niezatwierdzonymi zmianami roboczymi.

## Ograniczenia odbioru

Job browserowy jest przygotowany dla Linux CI, gdzie instaluje Chromium przez Playwright. Na tym Windows `sync_playwright()` kończy się przed startem testu z `PermissionError: [WinError 5] Odmowa dostępu`; nie wolno interpretować tego jako pozytywnego ani negatywnego wyniku timeline. Natywne Desktop wymaga jednego świeżego przebiegu bez aktywnego konfliktu WebView2. Do formalnej decyzji wydania pozostaje także manualny czytnik ekranu, powiększenie/reflow, pełny high contrast oraz próba z prawdziwym kontem i dostawcami.

Indeks FTS5 jest pochodny i budowany przy pierwszym lokalnym wyszukiwaniu; koszt pierwszego zasilenia jest jawny w profilu. SQLite pozostaje jedynym właścicielem katalogu, a brak FTS5/trigram zachowuje dokładny fallback skanu.
