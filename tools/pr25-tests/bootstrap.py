"""Build an isolated test interpreter; works on Linux, Codespaces and Windows."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--venv-dir', type=Path, default=ROOT / '.local' / 'pr25-venv')
parser.add_argument('--sdk', action='store_true', help='Install the pinned optional MCP test SDK')
args = parser.parse_args()
if sys.version_info < (3, 10):
    parser.error('Python 3.10 or newer is required.')
venv.EnvBuilder(with_pip=True).create(args.venv_dir)
python = args.venv_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
if args.sdk:
    subprocess.run([str(python), '-m', 'pip', 'install', '-r',
                    str(Path(__file__).with_name('requirements-sdk.lock'))], check=True)
print('Environment ready:', args.venv_dir)
print('Run:', str(python), 'tools/pr25-tests/run.py --suite all')
