"""Run the existing offline checks in a credential-free temporary source copy."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'audit' / '2026-09-24'
OUT.mkdir(exist_ok=True)
COPY = Path(tempfile.mkdtemp(prefix='archivebite_audit_'))
for file in ROOT.iterdir():
    if file.is_file() and (file.name == '.gitignore' or file.suffix in {'.py', '.md', '.bat', '.ps1', '.txt', '.in'}):
        shutil.copy2(file, COPY / file.name)
for name in ('static', 'audit', '.github'):
    shutil.copytree(ROOT / name, COPY / name, ignore=shutil.ignore_patterns('2026-09-24', '__pycache__', '*.db*', '*.lock'))
(COPY / 'data').mkdir(exist_ok=True)
shutil.copy2(ROOT / 'data/model_tags.seed.json', COPY / 'data/model_tags.seed.json')
env = dict(os.environ)
for name in list(env):
    if name.startswith('ARCHIVEBATE_'):
        del env[name]
env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
commands = []
ci = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
for runner, file in re.findall(r'\b(python|node) (audit/[\w.]+)', ci):
    cmd = [sys.executable if runner == 'python' else 'node', file]
    if cmd not in commands:
        commands.append(cmd)
for file in sorted((COPY / 'audit').glob('regression_*')):
    if file.suffix not in {'.py', '.cjs'}:
        continue
    cmd = [sys.executable if file.suffix == '.py' else 'node', 'audit/' + file.name]
    if cmd not in commands:
        commands.append(cmd)
commands.append([sys.executable, '-m', 'unittest', 'test_suite', '-q'])
results = {'source_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
           'python': sys.version, 'node': subprocess.check_output(['node', '--version'], text=True).strip(),
           'isolation_copy': str(COPY), 'tests': []}
for cmd in commands:
    start = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=COPY, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=150)
        code, log = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as exc:
        code, log = 124, 'TIMEOUT 150s\n' + str(exc.stdout or '') + str(exc.stderr or '')
    name = Path(cmd[1]).stem if cmd[1] != '-m' else 'test_suite'
    (OUT / (name + '.log')).write_text(log, encoding='utf-8')
    row = {'command': ['python' if cmd[0] == sys.executable else cmd[0], *cmd[1:]],
           'exit_code': code, 'seconds': round(time.perf_counter() - start, 3), 'log': name + '.log'}
    results['tests'].append(row)
    (OUT / 'test_results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(f"{code:3} {row['seconds']:7.2f}s {name}", flush=True)
print('COPY=' + str(COPY), flush=True)
