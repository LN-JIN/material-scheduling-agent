import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / 'outputs' / 'material-scheduling-agent'
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))
from src.store import DraftStore


def run(name, input_name, omit_rules=False, reject_insert=False, corrupt_db=False):
    target = OUT / name
    target.mkdir(parents=True, exist_ok=True)
    data = target / 'data'
    data.mkdir(exist_ok=True)
    for filename in ('materials.json', 'rules.json', 'schedules.json'):
        if filename != 'rules.json' or not omit_rules:
            shutil.copyfile(PROJECT / 'data' / filename, data / filename)
    request = target / 'input.json'
    shutil.copyfile(PROJECT / 'examples' / 'inputs' / (input_name + '.json'), request)
    db = target / 'drafts.sqlite'
    if reject_insert:
        store = DraftStore(db)
        store.connection.execute("CREATE TRIGGER reject_draft BEFORE INSERT ON drafts BEGIN SELECT RAISE(ABORT, 'draft insert rejected by storage'); END")
        store.close()
    if corrupt_db:
        db.write_bytes(b'This is not a SQLite database.\n')
    output = target / 'output.json'
    trace = target / 'trace.jsonl'
    args = [sys.executable, '-m', 'src.main', '--mode', 'offline', '--input', str(request),
            '--data-dir', str(data), '--db', str(db), '--output', str(output), '--trace', str(trace)]
    process = subprocess.run(args, cwd=PROJECT, capture_output=True, text=True, encoding='utf-8', timeout=30)
    (target / 'stderr.txt').write_text(process.stderr, encoding='utf-8')
    result = json.loads(output.read_text(encoding='utf-8')) if output.exists() else None
    record = {'case': name, 'exit_code': process.returncode, 'output_exists': output.exists(),
              'result': {k: result.get(k) for k in ('task_status', 'review_status', 'save_status', 'confirmation_status', 'risk_reasons', 'stop_reason')} if result else None,
              'stderr_last_line': process.stderr.splitlines()[-1] if process.stderr else None}
    if not corrupt_db:
        with sqlite3.connect(db) as connection:
            record['draft_count'] = connection.execute('SELECT count(*) FROM drafts').fetchone()[0]
    if trace.exists():
        entries = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()]
        record['last_event'] = entries[-1] if entries else None
    return record


records = [run('risk-baseline', 'risk-confirmation'),
           run('missing-rules', 'risk-confirmation', omit_rules=True),
           run('sqlite-insert-rejected', 'normal', reject_insert=True),
           run('sqlite-invalid-file', 'normal', corrupt_db=True)]
(OUT / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(records, ensure_ascii=False, indent=2))
