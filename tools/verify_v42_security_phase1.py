from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
CHECKS = [
    ROOT / 'main.py',
    ROOT / 'cache_store.py',
    ROOT / 'audit' / 'regression_security_v42.py',
]


def main() -> int:
    for path in CHECKS:
        if not path.exists():
            print(f'FAIL missing: {path}')
            return 1
        if path.suffix == '.py':
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            print(f'OK AST: {path.relative_to(ROOT)}')

    main_text = (ROOT / 'main.py').read_text(encoding='utf-8')
    required = [
        'TrustedHostMiddleware',
        'async def local_security_gate',
        '@app.post("/api/catalog/refresh")',
        '@app.post("/api/video/details/refresh")',
        'Odświeżenie katalogu wymaga POST /api/catalog/refresh',
    ]
    for marker in required:
        if marker not in main_text:
            print(f'FAIL missing marker: {marker}')
            return 1

    forbidden = 'url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:")'
    if forbidden in main_text:
        print('FAIL localhost stream bypass still present')
        return 1
    print('OK source markers')

    proc = subprocess.run(
        [sys.executable, 'audit/regression_security_v42.py'],
        cwd=ROOT,
        text=True,
    )
    if proc.returncode != 0:
        print(f'FAIL regression_security_v42.py rc={proc.returncode}')
        return proc.returncode

    print('PASS ARCHIVEBITE V4.2 SECURITY PHASE 1')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
