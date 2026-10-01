"""One-time, stdlib laptop configuration, diagnostics, and saved ACC launch settings."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parent.parent
MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json')


def config_dir():
    return Path(os.environ.get('ACC_CONFIG_DIR', str(Path.home() / '.acc'))).expanduser().resolve()


def absolute(value):
    return str(Path(value).expanduser().resolve())


def read_json(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Configuration must be a JSON object.')
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    if os.name != 'nt':
        temporary.chmod(0o600)
    temporary.replace(path)


def local_endpoint(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Local endpoint must be a loopback HTTP URL without credentials, query, or fragment.')
    # Accessing port validates malformed ports as well.
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('Invalid local endpoint port.')
    return value.rstrip('/')


def initialize(args, directory=None):
    directory = Path(directory or config_dir()).resolve()
    settings_path = directory / 'launcher.json'
    saved = read_json(settings_path) if settings_path.exists() else {}
    project = getattr(args, 'project', None) or saved.get('project')
    if not getattr(args, 'yes', False):
        project = input('Project folder [{}]: '.format(project or Path.cwd())).strip() or project or str(Path.cwd())
    if not project:
        raise ValueError('Supply --project for the first noninteractive setup.')
    project = absolute(project)
    if not Path(project).is_dir():
        raise ValueError('Project folder does not exist.')
    # A changed project gets its own state directory; preserve the old project state.
    suffix = hashlib.sha256(project.encode()).hexdigest()[:12]
    state = getattr(args, 'state_dir', None) or (saved.get('state_dir') if saved.get('project') == project else None)
    state = absolute(state or directory / suffix)
    agents_path = absolute(getattr(args, 'agents', None) or saved.get('agents') or directory / 'agents.json')
    port = getattr(args, 'port', None)
    if port is None:
        port = saved.get('port', 8765)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    endpoint = local_endpoint(getattr(args, 'local_endpoint', None) or saved.get('local_endpoint', 'http://127.0.0.1:11434/v1/models'))
    if getattr(args, 'agents', None) and not Path(agents_path).is_file():
        raise ValueError('--agents must name an existing JSON file.')
    if Path(agents_path).exists():
        agents = read_json(agents_path)
    else:
        agents = read_json(REPO / 'examples' / 'hermes-agents.json')
        agents['conversation']['enabled'] = False
    voice_model = getattr(args, 'voice_model_dir', None)
    if voice_model:
        voice_model = absolute(voice_model)
        if not all((Path(voice_model) / name).is_file() for name in MODEL_FILES):
            raise ValueError('Voice model must contain model.bin, config.json, and tokenizer.json.')
        voice_python = absolute(getattr(args, 'voice_python', None) or saved.get('voice_python') or sys.executable)
        agents['transcription'] = {'argv': [voice_python, str(REPO / 'acc' / 'transcribe.py'),
            '--model-dir', voice_model, '--device', 'cpu', '--compute-type', 'int8',
            '--audio', '{audio_file}', '--output', '{text_file}']}
        saved.update(voice_model_dir=voice_model, voice_python=voice_python)
    elif getattr(args, 'voice_python', None):
        raise ValueError('--voice-python requires --voice-model-dir.')
    if not Path(agents_path).exists() or voice_model:
        write_json(agents_path, agents)
    saved.update(version=1, project=project, state_dir=state, agents=agents_path, port=port,
                 python=saved.get('python', absolute(sys.executable)), local_endpoint=endpoint)
    Path(state).mkdir(parents=True, exist_ok=True)
    write_json(settings_path, saved)
    # Mergeable fragment uses the existing Hermes generator's exact schema.
    write_json(directory / 'hermes-mcp.json', {'mcp_servers': {'acc': {
        'command': saved['python'], 'args': [str(REPO / 'acc' / 'bridge.py'),
        '--url', 'http://127.0.0.1:' + str(port), '--token-file', str(Path(state) / 'token')]
    }}})
    return saved


def probe(argv):
    """Capture and discard process output: auth tooling can include sensitive details."""
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=8, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint_available(url):
    try:
        url = local_endpoint(url)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(url, timeout=3) as response:
            return response.status == 200
    except (ValueError, OSError, urllib.error.URLError):
        return False


def doctor(settings):
    checks = []
    def add(name, ok, detail):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})
    python = settings.get('python', sys.executable)
    add('python', probe([python, '-c', 'import sys; sys.exit(sys.version_info < (3, 10))']),
        'Saved interpreter must run Python 3.10 or newer.')
    add('project', Path(settings['project']).is_dir(), 'Saved project folder exists.')
    git = shutil.which('git')
    add('git', bool(git) and probe([git, '--version']), 'Git must be on PATH.')
    add('project_git', bool(git) and probe([git, '-C', settings['project'], 'rev-parse', '--show-toplevel']),
        'The selected project must be a Git working tree.')
    gh = shutil.which('gh')
    add('github_cli', bool(gh), 'GitHub CLI is optional unless using GitHub actions.')
    add('github_auth', bool(gh) and probe([gh, 'auth', 'status']),
        'Run gh auth login on this laptop if GitHub actions are needed; no credentials are displayed.')
    add('hermes', bool(shutil.which('hermes')), 'Executable detection only; profile login and model inference are unverified.')
    endpoint = settings.get('local_endpoint', 'http://127.0.0.1:11434/v1/models')
    add('local_endpoint', endpoint_available(endpoint),
        'HTTP reachability only; installed model, routing, and inference are unverified.')
    try:
        agents = read_json(settings['agents'])
        transcription = agents.get('transcription', {}).get('argv', [])
        add('agents_config', isinstance(agents.get('agents'), list), 'Host adapter configuration can be read.')
        add('automatic_conversation', bool(agents.get('conversation', {}).get('enabled')),
            'Disabled initially; configure actual Hermes profiles before enabling in ACC.')
    except (OSError, ValueError, TypeError, AttributeError):
        transcription = []
        add('agents_config', False, 'Host adapter JSON is missing or invalid.')
    voice_python = settings.get('voice_python', python)
    model_dir = settings.get('voice_model_dir')
    if isinstance(transcription, list) and '--model-dir' in transcription:
        index = transcription.index('--model-dir')
        if index + 1 < len(transcription):
            model_dir = transcription[index + 1]
        if transcription:
            voice_python = transcription[0]
    add('voice_package', probe([voice_python, '-c', 'import faster_whisper']),
        'Optional faster-whisper must import in the transcription interpreter.')
    add('voice_model', bool(model_dir) and all((Path(model_dir) / name).is_file() for name in MODEL_FILES),
        'Local model files checked only; speech recognition remains a host test.')
    verified = False
    try:
        database = Path(settings['state_dir']) / 'acc.sqlite3'
        if database.is_file():
            with sqlite3.connect(database) as db:
                row = db.execute("SELECT value FROM meta WHERE key='bridge_status'").fetchone()
            verified = bool(row and json.loads(row[0]).get('conversation_verified_at'))
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
        pass
    add('chatgpt_remote', verified,
        'Requires the native MCP client to discover ACC tools and complete one ChatGPT Remote conversation turn.')
    return checks


def launch(settings, open_browser=True):
    argv = [settings['python'], '-m', 'acc.server', '--project', settings['project'],
            '--state-dir', settings['state_dir'], '--agents', settings['agents'], '--port', str(settings['port'])]
    if open_browser:
        argv.append('--open-browser')
    return subprocess.call(argv, cwd=str(REPO))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Save laptop settings; preserves existing agents and settings')
    init.add_argument('--yes', action='store_true', help='Do not prompt (first setup requires --project)')
    for name in ('project', 'state-dir', 'agents', 'local-endpoint', 'voice-model-dir', 'voice-python'):
        init.add_argument('--' + name)
    init.add_argument('--port', type=int)
    diagnostics = commands.add_parser('doctor', aliases=['status'], help='Read-only host checks; never invokes inference')
    diagnostics.add_argument('--json', action='store_true')
    start = commands.add_parser('launch', help='Start ACC using saved settings')
    start.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    try:
        path = config_dir() / 'launcher.json'
        if args.command == 'init':
            initialize(args)
            print('Settings saved: ' + str(path))
            print('MCP fragment: ' + str(path.with_name('hermes-mcp.json')))
            print('Run doctor next. Host logins, model installation, and profile configuration remain manual.')
            return 0
        if not path.exists():
            if args.command == 'launch' and sys.stdin.isatty():
                initialize(argparse.Namespace(yes=False))
            else:
                raise ValueError('Run setup init --project PATH first (add --yes for noninteractive setup).')
        settings = read_json(path)
        if args.command == 'launch':
            return launch(settings, not args.no_browser)
        checks = doctor(settings)
        if args.json:
            print(json.dumps({'checks': checks, 'inference_verified': False}, indent=2))
        else:
            for check in checks:
                print(('OK   ' if check['ok'] else 'TODO ') + check['name'] + ': ' + check['detail'])
            print('Diagnostics do not verify provider login, model behavior, or end-to-end voice.')
        # Optional integrations do not prevent the stdlib UI from starting.
        return 0 if all(c['ok'] for c in checks if c['name'] in ('python', 'project', 'git', 'project_git', 'agents_config')) else 1
    except (OSError, ValueError, KeyError) as exc:
        print('ACC setup: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
