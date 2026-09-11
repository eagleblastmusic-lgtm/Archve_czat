from pathlib import Path

main_path = Path("main.py")
text = main_path.read_text(encoding="utf-8")

old_bootstrap = '''            try:\n                from catalog_service import catalog_service\n                _ensure_catalog_indexing(catalog_service)\n            except Exception as exc:\n                print(f"[Catalog] Błąd bootstrapu indeksu: {exc}")\n'''
new_bootstrap = old_bootstrap + '''            # Deep Archivebate runs independently from the shallow home-feed index.\n            # It discovers profiles and walks their historical pages with durable checkpoints.\n            try:\n                from deep_archivebate import deep_archivebate_service\n                deep_archivebate_service.start(scraper)\n            except Exception as exc:\n                print(f"[Deep Archivebate] Błąd startu: {exc}")\n'''
assert text.count(old_bootstrap) == 1, "bootstrap integration point changed"
text = text.replace(old_bootstrap, new_bootstrap, 1)

old_shutdown = '''    yield\n    print("[Archivebate Browser] Zamykanie aplikacji.")\n'''
new_shutdown = '''    yield\n    try:\n        from deep_archivebate import deep_archivebate_service\n        deep_archivebate_service.stop(wait=False)\n    except Exception:\n        pass\n    print("[Archivebate Browser] Zamykanie aplikacji.")\n'''
assert text.count(old_shutdown) == 1, "shutdown integration point changed"
text = text.replace(old_shutdown, new_shutdown, 1)

status_marker = '''@app.get("/api/status")\nasync def get_status():\n'''
deep_endpoints = '''@app.get("/api/deep-archivebate/status")\ndef get_deep_archivebate_status():\n    from deep_archivebate import deep_archivebate_service\n    return deep_archivebate_service.status()\n\n\n@app.post("/api/deep-archivebate/start")\ndef start_deep_archivebate():\n    from deep_archivebate import deep_archivebate_service\n    started = deep_archivebate_service.start(scraper)\n    return {"started": started, **deep_archivebate_service.status()}\n\n\n@app.post("/api/deep-archivebate/stop")\ndef stop_deep_archivebate():\n    from deep_archivebate import deep_archivebate_service\n    deep_archivebate_service.stop(wait=False)\n    return {"stopped": True, **deep_archivebate_service.status()}\n\n\n'''
assert text.count(status_marker) == 1, "status endpoint marker changed"
text = text.replace(status_marker, deep_endpoints + status_marker, 1)
main_path.write_text(text, encoding="utf-8")
