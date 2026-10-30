"""One-command, credential-free review. Outputs remain separate from archived runs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import os
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path)
    args = parser.parse_args()
    out = args.out_dir or ROOT / '.runtime' / ('review-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6])
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    commands = [
        ('tests', ['-m', 'unittest', 'discover', '-s', 'tests', '-v']),
        ('scenarios', ['-m', 'scripts.evaluate', '--mode', 'offline', '--out-dir', str(out / 'offline')]),
        ('native-errors', ['-m', 'scripts.check_failures', str(out / 'failures')]),
        ('preferences-baseline', ['-m', 'scripts.compare_preferences', '--mode', 'offline', '--out-dir', str(out / 'preferences')]),
        ('evidence', ['-m', 'scripts.audit', '--output', str(out / 'audit.json')]),
    ]
    env = dict(os.environ)
    env.pop('DASHSCOPE_API_KEY', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    records = []
    for name, command in commands:
        result = subprocess.run([sys.executable] + command, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', timeout=180)
        (out / (name + '.txt')).write_text(result.stdout + result.stderr, encoding='utf-8')
        records.append({'check': name, 'passed': result.returncode == 0, 'exit_code': result.returncode})
        print(json.dumps(records[-1]), flush=True)
    report = {'passed': all(r['passed'] for r in records), 'checks': records,
              'scope': 'No live API calls; validates behavior and consistency of archived live evidence.'}
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(str(out / 'summary.json'))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
