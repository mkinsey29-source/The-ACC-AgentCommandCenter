"""Run real tests and retain failed results without changing ACC host configuration."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--suite', choices=['focused', 'full', 'sdk', 'bridge', 'all'], default='all')
parser.add_argument('--output', type=Path, help='New output folder; existing files are never overwritten')
args = parser.parse_args()
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
out = (args.output or ROOT / '.local' / 'pr25-results' / stamp).resolve()
out.mkdir(parents=True, exist_ok=False)
env = dict(os.environ)
env.pop('TYPESAFE_API_KEY', None)
commands = []
def discover(label, directory, pattern):
    commands.append((label, [sys.executable, '-m', 'unittest', 'discover', '-s', directory, '-p', pattern, '-v']))
if args.suite in ('focused', 'all'):
    discover('conversation', 'tests', 'test_conversation.py')
    discover('setup', 'tests', 'test_setup.py')
if args.suite in ('full', 'all'):
    discover('full', 'tests', 'test*.py')
if args.suite in ('sdk', 'bridge', 'all'):
    commands.append(('sdk-installed', [sys.executable, '-c', 'import mcp; import importlib.metadata; print(importlib.metadata.version("mcp"))']))
if args.suite in ('sdk', 'all'):
    discover('sdk-existing', 'tests', 'test_mcp_sdk.py')
if args.suite in ('bridge', 'all'):
    discover('bridge-acceptance', 'tools/pr25-tests/tests', 'test_bridge_acceptance.py')
commands.append(('python-compile', [sys.executable, '-m', 'compileall', '-q', 'acc', 'tools/pr25-tests']))
if shutil.which('node'):
    commands.append(('javascript-syntax', ['node', '--check', 'acc/web/app.js']))
results = []
for label, command in commands:
    # Remove the credential immediately before EVERY subprocess test invocation.
    env.pop('TYPESAFE_API_KEY', None)
    with (out / (label + '.log')).open('w', encoding='utf-8') as log:
        try:
            run = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            code = run.returncode
        except subprocess.TimeoutExpired:
            code = 124
            log.write('\nTest command timed out after 300 seconds.\n')
    results.append({'check': label, 'exit_code': code, 'result': 'passed' if code == 0 else 'failed', 'log': label + '.log'})
    print(label + ': ' + results[-1]['result'], flush=True)
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
summary = {'timestamp_utc': stamp, 'head': head, 'python': sys.version,
           'native_chatgpt_host_acceptance': 'not run', 'checks': results,
           'javascript_syntax': 'run' if shutil.which('node') else 'not run: node unavailable'}
(out / 'results.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
print('Evidence:', out)
sys.exit(1 if any(r['exit_code'] for r in results) else 0)
