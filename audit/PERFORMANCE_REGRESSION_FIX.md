# Naprawa regresji ładowania — 2026-09-13

Baza: `21a05de`; wynik dotyczy lokalnej zmiany, bez commita i wdrożenia.

| Wymaganie | Klasyfikacja diagnozy | Wykonana delta | Dowód |
|---|---|---|---|
| Szybkie przygotowanie listy | DELTA_REQUIRED | Wzbogacanie członków grup w jednej porcji; pominięcie niewykorzystywanych członków dużych grup; wydajne zbiory identyfikatorów ulubionych w SQL | Pomiar rzeczywistego katalogu; test liczby porcji, pracy SQLite i filtrów z ponad 1100 kluczami |
| Pełne strony 1 i 2 | DELTA_REQUIRED | Usunięcie kosztu powodującego timeout uzupełnienia pierwszych 16 kart; prefetch następnej strony dopiero po uzupełnieniu bieżącej | Przeglądarka: 280 kart DOM na każdej z pierwszych dwóch stron, grupowanie + Bez polubionych; test dwóch rozłącznych stron po 280 grup |
| Responsywność odtwarzacza podczas ładowania | DELTA_REQUIRED | Zapytanie kompletnej rewizji SSE wykonywane poza pętlą zdarzeń | Test wątku wykonania; istniejące testy natychmiastowego startu odtwarzacza, anulowania i odświeżania URL |
| Mechanizm paginacji, stabilne klucze, lazy thumbnails | ALREADY_CLOSED | Zachowano istniejące mechanizmy | Testy paginacji, frontend, ładowania i grupowania |

Pomiary diagnostyczne z cProfile na rzeczywistym katalogu (odczyt SQLite w trybie ro): zwykłe strony około 0,27 s, grupowane przed zmianą 10,76/12,00 s; po batched enrichment około 2,46/2,49 s. Filtr Bez polubionych po końcowej poprawce: grupowane strony około 3,45/3,01 s. Są to pomiary lokalnego przygotowania danych z narzutem profilera, nie gwarantowany czas pobrania mediów z CDN. Katalog jest aktualizowany przez niezależny proces, więc jego rozmiar między pomiarami może się zmieniać.

Test przeglądarkowy: osobny backend na 127.0.0.1:8765, bez procedury startup/indexingu i bez logowania; katalog tylko do odczytu, stan konta w osobnym pliku testowym. Filtr Bez polubionych + grupowanie: strona 1 i strona 2 po 280 kart, druga strona z cache; na obu sprawdzonych ekranach 4/4 widoczne miniatury załadowane. Pozostałe miniatury są celowo leniwe i pobierają się podczas przewijania. Filtr Tylko polubieni + grupowanie zwracał 197 grup na jednej stronie — pełność strony nadal uwzględnia faktyczny rozmiar zakresu.

Końcowa weryfikacja offline: 37/37 poleceń zakończonych kodem 0. Pierwszy sandboxowy test loading miał błąd uprawnień do TemporaryDirectory; pełny przebieg poza tym ograniczeniem przeszedł. Logi: `audit/tmp_performance_verification/`. Wynik dotyczy lokalnego drzewa o poniższych SHA-256, nie historycznego raportu ani CI na innym commicie.

| Plik | SHA-256 |
|---|---|
| `catalog_service.py` | `ec989d84d7107e5e286da665caa92315d2dc757feac543a641568b11e0c379f2` |
| `main.py` | `0465ab63759a7077aa04a3bfae2f9ccbf74e8dc65531a4e8ecec85ce897cf291` |
| `static/video-views.js` | `cecb54fc519440374762dcb587e91df39b0441190c027174de1a871e5bfb147a` |
| `audit/regression_grouped_query_batch.py` | `64ce9e4e51bf85d5bb09c3b374e3c94d3b57fe121c06042c00b6999eee587821` |
| `audit/regression_loading.py` | `38992e4c3912793532870e54c7a0d629c3870bfc8058f4978e4e430680ecea00` |
| `audit/regression_loading_frontend.cjs` | `75f0971cd6981fc64e8efe12c4c3a3401895932d91eae681ce7e6bb8a8efe67e` |

| Polecenie | Exit | Czas [s] |
|---|---:|---:|
| `python -m compileall -q .` | 0 | 1.33 |
| `python audit/regression_audit_fixes.py` | 0 | 2.77 |
| `python audit/regression_checks.py` | 0 | 1.57 |
| `node audit/regression_frontend.cjs` | 0 | 0.2 |
| `node audit/regression_accessibility.cjs` | 0 | 0.08 |
| `node audit/regression_home_loading.cjs` | 0 | 0.07 |
| `node audit/regression_block_no_reload.cjs` | 0 | 0.07 |
| `node audit/regression_pagination.cjs` | 0 | 0.1 |
| `node audit/regression_search_filters.cjs` | 0 | 0.07 |
| `node audit/regression_local_search.cjs` | 0 | 0.1 |
| `python audit/regression_search_pagination.py` | 0 | 0.67 |
| `node audit/regression_storyboard_client.cjs` | 0 | 0.08 |
| `node audit/regression_storyboard_cross_tab.cjs` | 0 | 2.28 |
| `node audit/regression_catalog_partial_frontend.cjs` | 0 | 0.18 |
| `python audit/regression_feed.py` | 0 | 3.89 |
| `python audit/regression_catalog_bootstrap.py` | 0 | 1.85 |
| `python audit/regression_catalog_resume.py` | 0 | 4.06 |
| `python audit/regression_catalog_transient_resume.py` | 0 | 1.88 |
| `python audit/regression_catalog_revision_monotonicity.py` | 0 | 0.66 |
| `python audit/regression_latest_sparse_migration_scope.py` | 0 | 0.65 |
| `python audit/regression_archivebate_sparse_pagination.py` | 0 | 1.95 |
| `python audit/regression_deep_archivebate.py` | 0 | 0.62 |
| `python audit/regression_camwhores_sparse_pagination.py` | 0 | 1.97 |
| `python audit/regression_legacy_resume_cursor.py` | 0 | 3.88 |
| `python audit/regression_archivebate_true_end.py` | 0 | 2.14 |
| `python audit/regression_archivebate_source_limit.py` | 0 | 7.79 |
| `python audit/regression_grouped_query_batch.py` | 0 | 1.47 |
| `python audit/regression_catalog_read_concurrency.py` | 0 | 0.58 |
| `python audit/regression_integration.py` | 0 | 3.06 |
| `python audit/regression_package_d.py` | 0 | 1.71 |
| `python -m unittest test_suite -q` | 0 | 1.23 |
| `python audit/regression_next_generation.py` | 0 | 2.41 |
| `node audit/regression_package_b_frontend.cjs` | 0 | 0.11 |
| `python -m unittest -v test_suite.py` | 0 | 1.23 |
| `python audit/regression_loading.py` | 0 | 1.49 |
| `node audit/regression_loading_frontend.cjs` | 0 | 0.23 |
| `python audit/regression_package_b.py` | 0 | 6.24 |

Test rzeczywistego odtwarzania: PASS — film miał readyState=4, paused=false i currentTime=50,64 s; po Escape paused=true. Pierwsza próba w sandboxie była blokowana przez proxy 127.0.0.1:9; test z dostępem sieciowym wykonano osobnym procesem poza tym ograniczeniem. Nie jest to pomiar p95 startu ani gwarancja dostępności wszystkich nagrań/CDN. Testowy serwer został zatrzymany. Dane użytkownika i jego zastane zmiany pozostają poza zakresem poprawki.
