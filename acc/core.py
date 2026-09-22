"""Persistent task state and supervised local workers. Python standard library only."""
from __future__ import annotations
import codecs
import contextlib
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid


def now():
    return time.time()


def identifier():
    return uuid.uuid4().hex


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                task_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL);
            ''')
            self._migrate_task_numbers(db)

    @staticmethod
    def _migrate_task_numbers(db):
        """Give every existing user-visible task one permanent human number."""
        rows = db.execute('SELECT id,data FROM tasks ORDER BY rowid').fetchall()
        tasks = [(row['id'], json.loads(row['data'])) for row in rows]
        existing = [task.get('task_number') for _, task in tasks
                    if not task.get('internal') and type(task.get('task_number')) is int
                    and task['task_number'] > 0]
        next_number = max(existing, default=0) + 1
        for task_id, task in tasks:
            if task.get('internal') or (type(task.get('task_number')) is int and task['task_number'] > 0):
                continue
            task['task_number'] = next_number
            next_number += 1
            db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(task), task_id))
        stored = db.execute("SELECT value FROM meta WHERE key='next_task_number'").fetchone()
        if stored:
            try:
                next_number = max(next_number, int(stored[0]))
            except (TypeError, ValueError):
                pass
        db.execute("INSERT OR REPLACE INTO meta VALUES ('next_task_number',?)", (str(next_number),))

    @staticmethod
    def ensure_task_number(db, task):
        """Allocate a number inside the caller's transaction; internal runs stay hidden."""
        if task.get('internal') or (type(task.get('task_number')) is int and task['task_number'] > 0):
            return task
        row = db.execute("SELECT value FROM meta WHERE key='next_task_number'").fetchone()
        number = int(row[0]) if row else 1
        task['task_number'] = number
        db.execute("INSERT OR REPLACE INTO meta VALUES ('next_task_number',?)", (str(number + 1),))
        return task

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def tasks(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM tasks ORDER BY rowid')]

    def get(self, task_id):
        with self.connect() as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return json.loads(row[0])

    def save(self, task, kind, details):
        # State and its event are committed together, so reconnects see consistent state.
        with self.connect() as db:
            self.ensure_task_number(db, task)
            db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?)', (task['id'], json.dumps(task)))
            if task.get('task_number') and 'task_number' not in details:
                details = {**details, 'task_number': task['task_number']}
            db.execute('INSERT INTO events(at,task_id,kind,data) VALUES (?,?,?,?)',
                       (now(), task['id'], kind, json.dumps(details)))

    def event(self, kind, details, task_id=None):
        with self.connect() as db:
            db.execute('INSERT INTO events(at,task_id,kind,data) VALUES (?,?,?,?)',
                       (now(), task_id, kind, json.dumps(details)))

    def events(self, after=0, limit=200):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?',
                              (after, limit)).fetchall()
        return [dict(r, data=json.loads(r['data'])) for r in rows]

    def tail(self):
        with self.connect() as db:
            seq = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        return seq


class StateLock:
    """One coordinator per state directory. Released by the OS after process exit."""
    def __init__(self, path):
        self.file = open(path, 'a+b')
        self.file.seek(0)
        self.file.write(b'0')
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise Conflict('Another ACC coordinator owns this state directory.')

    def close(self):
        if not self.file.closed:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()


def git_snapshot(project):
    def git(*args):
        return subprocess.run(['git', '-C', str(project), *args], capture_output=True,
                              timeout=8, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    status = git('status', '--porcelain=v1', '-z', '--untracked-files=all')
    if status.returncode:
        return {'available': False, 'message': 'This folder is not a readable Git worktree.'}
    records = status.stdout.decode('utf-8', 'replace').split('\0')
    files, i = [], 0
    while i < len(records):
        row = records[i]
        i += 1
        if not row:
            continue
        state, name = row[:2], row[3:]
        if 'R' in state or 'C' in state:
            i += 1  # porcelain -z puts the old name after the new name
        path = project / name
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = None
        files.append({'status': state, 'path': name, 'modified_ns': stamp})
    branch = git('branch', '--show-current').stdout.decode().strip() or '(detached HEAD)'
    head = git('rev-parse', '--verify', 'HEAD')
    commit = head.stdout.decode().strip() if head.returncode == 0 else None
    log = git('log', '-8', '--format=%h%x00%s%x00%ct')
    commits = []
    for line in log.stdout.decode('utf-8', 'replace').splitlines():
        fields = line.split('\0', 2)
        if len(fields) == 3:
            commits.append(dict(zip(('sha', 'subject', 'time'), fields)))
    return {'available': True, 'branch': branch, 'head': commit, 'files': files, 'commits': commits}


def stop_tree(proc):
    """Do not transfer ownership until the supervised process has ended."""
    if os.name == 'nt':
        # Full job-object isolation is required before enabling Windows takeover.
        result = subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                                capture_output=True, timeout=15)
        if result.returncode and proc.poll() is None:
            raise Conflict('Windows could not confirm the runner stopped.')
        proc.wait(timeout=5)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # Always kill remaining group members, even if the parent exited first.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


class Coordinator:
    def __init__(self, project, state, config=None):
        self.project = Path(project).resolve()
        if not self.project.is_dir():
            raise ValueError('Project folder does not exist.')
        self.state = Path(state).resolve()
        self.state.mkdir(parents=True, exist_ok=True)
        try:
            self.state.chmod(0o700)
        except OSError:
            pass
        self.ownership = StateLock(self.state / 'coordinator.lock')
        workspace_locks = Path.home() / '.acc' / 'workspace-locks'
        workspace_locks.mkdir(parents=True, exist_ok=True)
        lock_name = hashlib.sha256(os.path.normcase(str(self.project)).encode()).hexdigest()
        try:
            self.project_ownership = StateLock(workspace_locks / lock_name)
        except Exception:
            self.ownership.close()
            raise
        self.lock = threading.RLock()
        self.store = Store(self.state / 'acc.sqlite3')
        with self.store.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='project'").fetchone()
            if row and row[0] != str(self.project):
                self.ownership.close()
                self.project_ownership.close()
                raise Conflict('State directory belongs to a different project.')
            db.execute("INSERT OR REPLACE INTO meta VALUES ('project',?)", (str(self.project),))
        self.process = None
        self.running_task = None
        self.worker_thread = None
        self.deadline = None
        self.halt = threading.Event()
        self.git = {'available': False, 'message': 'Inspecting local Git…'}
        self.agents = {'local-command': {'id': 'local-command', 'name': 'Local command',
                       'kind': 'tool', 'available': True, 'description': 'Runs an explicit argv command; no AI inference.'}}
        for key, name in [('deepseek', 'DeepSeek'), ('claude', 'Claude'), ('grok', 'Grok'), ('local-model', 'Local model')]:
            self.agents[key] = {'id': key, 'name': name, 'kind': 'model', 'available': False,
                                'description': 'Not configured on this computer.'}
        settings = json.loads(Path(config).read_text(encoding='utf-8')) if config else {}
        for_config = settings.get('agents', [])
        if for_config:
            for agent in for_config:
                key = agent['id']
                if key == 'local-command' or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', key):
                    raise ValueError('Invalid agent id.')
                if agent.get('kind') == 'job':
                    capability = agent.get('capability')
                    if not isinstance(capability, str) or not re.fullmatch(
                            r'[a-z][a-z0-9-]{0,39}(?:\.[a-z][a-z0-9-]{0,39}){0,3}', capability):
                        raise ValueError('Job-backed agents need a dotted lowercase capability.')
                    self.agents[key] = {'id': key, 'name': agent.get('name', key), 'kind': 'job',
                                        'capability': capability, 'provider': agent.get('provider'),
                                        'local': bool(agent.get('local')), 'available': True,
                                        'description': 'Job-backed implementer; provider readiness checked at submission.'}
                    continue
                driver = agent.get('driver')
                if driver == 'hermes':
                    executable = agent.get('executable', 'hermes')
                    argv = [sys.executable, str(Path(__file__).with_name('hermes.py')), 'run',
                            '--executable', executable, '--packet', '{prompt_file}']
                    for option in ('provider', 'model', 'profile'):
                        if agent.get(option):
                            argv += ['--' + option, agent[option]]
                    agent['argv'] = argv
                    check_executable = executable
                elif driver == 'dsh':
                    executable = agent.get('executable', 'dsh')
                    argv = [sys.executable, str(Path(__file__).with_name('deepseek_harness.py')),
                            '--executable', executable, '--packet', '{prompt_file}']
                    agent['argv'] = argv
                    check_executable = executable
                elif driver == 'ollama':
                    if not agent.get('model'):
                        raise ValueError('An ollama-driver agent needs a model.')
                    host = agent.get('host', 'http://127.0.0.1:11434')
                    argv = [sys.executable, str(Path(__file__).with_name('ollama.py')),
                            '--packet', '{prompt_file}', '--model', agent['model'], '--host', host]
                    agent['argv'] = argv
                    from .ollama import is_reachable
                    available = is_reachable(host)
                    self.agents[key] = {**agent, 'kind': 'model', 'available': available,
                                        'description': 'Configured command adapter; provider readiness not verified.'
                                                        if available else 'Ollama host not reachable: ' + host}
                    continue
                elif driver == 'gemini':
                    api_key_file = agent.get('api_key_file')
                    if not api_key_file:
                        raise ValueError('A gemini-driver agent needs an api_key_file.')
                    model = agent.get('model', 'gemini-3.5-flash')
                    argv = [sys.executable, str(Path(__file__).with_name('gemini.py')),
                            '--packet', '{prompt_file}', '--api-key-file', api_key_file, '--model', model]
                    if agent.get('endpoint'):
                        argv += ['--endpoint', agent['endpoint']]
                    agent['argv'] = argv
                    # A local credential-reference file, not a PATH-resolvable command.
                    available = Path(api_key_file).is_file()
                    self.agents[key] = {**agent, 'kind': 'model', 'available': available,
                                        'description': 'Configured command adapter; provider readiness not verified.'
                                                        if available else 'Gemini API key file not found: ' + api_key_file}
                    continue
                elif driver == 'gemini-cli':
                    executable = agent.get('executable', 'gemini')
                    argv = [sys.executable, str(Path(__file__).with_name('gemini_cli.py')),
                            '--packet', '{prompt_file}', '--executable', executable]
                    if agent.get('model'):
                        argv += ['--model', agent['model']]
                    agent['argv'] = argv
                    check_executable = executable
                elif driver == 'deepastra':
                    launcher = agent.get('launcher', 'launch.py')
                    provider = agent.get('provider', 'deepseek')
                    argv = [sys.executable, str(Path(__file__).with_name('deepastra.py')),
                            '--packet', '{prompt_file}', '--launcher', launcher, '--provider', provider]
                    if agent.get('key_file'):
                        argv += ['--key-file', agent['key_file']]
                    agent['argv'] = argv
                    # launch.py is a cloned script, not a PATH-resolvable command; shutil.which
                    # would wrongly require it be chmod +x, unlike how it's actually invoked here.
                    available = Path(launcher).is_file()
                    self.agents[key] = {**agent, 'kind': 'model', 'available': available,
                                        'description': 'Configured command adapter; provider readiness not verified.'
                                                        if available else 'DeepAstra launcher not found: ' + launcher}
                    continue
                else:
                    argv = agent['argv']
                    check_executable = argv[0]
                if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                    raise ValueError('Agent argv must be a nonempty string array.')
                available = bool(shutil.which(check_executable))
                self.agents[key] = {**agent, 'kind': 'model', 'available': available,
                                    'description': 'Configured command adapter; provider readiness not verified.' if available else 'Executable missing.'}
        # An old live process could still be writing. Require explicit inspection on restart.
        self.recovery_required = False
        for task in self.store.tasks():
            if task['status'] in ('launching', 'running', 'stopping', 'processing_result', 'publishing'):
                job_backed = bool((task.get('workflow') or {}).get('job_id')) and \
                    self.agents.get(task.get('active_agent'), {}).get('kind') == 'job'
                task['status'] = 'interrupted'
                task['activity'] = ('Coordinator restarted while a job-backed step was outstanding; '
                                     'confirm the external job/worker is stopped or reconciled before '
                                     'releasing workspace.' if job_backed else
                                     'Coordinator restarted. Inspect previous PID before releasing workspace.')
                self.store.save(task, 'interrupted', {'message': task['activity']})
            if task['status'] == 'interrupted':
                self.recovery_required = True
        self.observer = threading.Thread(target=self._observe, daemon=True)
        from .workflow import Workflows
        self.workflows = Workflows(self)
        from .conversation import Conversation
        self.conversation = Conversation(self, settings.get('conversation'), settings.get('transcription'))
        from .voice import Voice
        self.voice = Voice(self, settings.get('transcription'))
        from .controls import Controls
        self.controls = Controls(self)
        from .integrations import IntegrationHub
        self.integrations = IntegrationHub(self, settings.get('integrations'))
        from .github import GitHub
        self.github = GitHub(self, settings.get('github'))
        from .archive import Archive
        self.archive = Archive(self)
        self.observer.start()
        self.github.thread.start()
        self.workflows.thread.start()

    def public_agents(self):
        return [{k: a.get(k) for k in ('id', 'name', 'kind', 'available', 'description', 'local', 'driver')} for a in self.agents.values()]

    def snapshot(self):
        with self.lock:
            seq = self.store.tail()
            return {'project': str(self.project), 'project_mode': self.controls.mode(),
                    'tasks': [t for t in self.store.tasks() if not t.get('internal')],
                    'conversation': self.conversation.state(), 'github': self.github.snapshot(),
                    'integrations': self.integrations.snapshot(),
                    'agents': self.public_agents(), 'git': self.git, 'cursor': seq,
                    'events': self.store.events(max(0, seq - 100)),
                    'recovery_required': self.recovery_required,
                    'capabilities': {'github': True, 'switch_after_step': True, 'automatic_offline': bool(self.conversation.settings.get('local_agent')), 'safe_takeover': False, 'managed_workflows': True, 'hermes_connector': True,
                                     'integration_jobs': True, 'shared_memory': True, 'fenced_job_leases': True}}

    def build_task(self, payload):
        title, instruction = payload.get('title', '').strip(), payload.get('instruction', '').strip()
        if not title or not instruction or len(title) > 200 or len(instruction) > 50000:
            raise ValueError('Provide a title (up to 200 characters) and instruction (up to 50,000).')
        agent = payload.get('agent', 'local-command')
        if agent not in self.agents:
            raise ValueError('Unknown agent.')
        argv = payload.get('argv', [])
        if not isinstance(argv, list) or not all(isinstance(x, str) and '\0' not in x for x in argv):
            raise ValueError('Command must be a JSON array of strings, not a shell string.')
        timeout = payload.get('timeout_seconds', 900)
        if type(timeout) is not int or not 1 <= timeout <= 86400:
            raise ValueError('Timeout must be an integer between 1 and 86400 seconds.')
        task = {'id': identifier(), 'title': title, 'instruction': instruction, 'revision': 1,
                'requirements_history': [], 'delivered_revision': None, 'agent': agent, 'active_agent': None,
                'argv': argv, 'status': 'queued', 'activity': 'Ready to start.', 'next_step': 'Run assigned worker',
                'created': now(), 'run_id': None, 'pid': None, 'exit_code': None, 'evidence': [], 'runs': [],
                'review': None, 'timeout_seconds': timeout, 'timed_out': False}
        return task

    def create(self, payload):
        with self.lock:
            task = self.build_task(payload)
            self.store.save(task, 'task_created', {'message': task['title'], 'revision': 1})
            return task

    def assign(self, task_id, agent):
        with self.lock:
            task = self.store.get(task_id)
            if task.get('task_kind') == 'workflow_step':
                raise Conflict('Workflow step records are controlled through their parent task.')
            if agent not in self.agents or not self.agents[agent]['available']:
                raise ValueError('That agent is not configured and available.')
            if task.get('internal'):
                raise Conflict('Use conversation routing to assign internal work.')
            if task['status'] in ('launching', 'running', 'stopping', 'processing_result', 'publishing', 'interrupted'):
                raise Conflict('Active takeover is not enabled yet. Stop the runner and inspect its handoff before reassigning.')
            if task.get('workflow'):
                raise Conflict('Use workflow assignments to change a managed worker.')
            task['agent'] = agent
            self.store.save(task, 'assigned', {'message': 'Assigned to ' + self.agents[agent]['name']})
            return task

    def revise(self, task_id, instruction):
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 50000:
            raise ValueError('Instruction must contain 1–50,000 characters.')
        with self.lock:
            task = self.store.get(task_id)
            if task.get('task_kind') == 'workflow_step':
                raise Conflict('Workflow step records are controlled through their parent task.')
            if task.get('internal'):
                raise Conflict('Send a new conversation message instead of revising internal work.')
            if task['status'] in ('launching', 'running', 'stopping', 'processing_result', 'publishing', 'interrupted'):
                raise Conflict('Stop and inspect this run before changing its instructions in v0.1.')
            if task.get('workflow'):
                task['workflow'] = None  # New requirements need a fresh, explicitly enabled workflow.
            task['requirements_history'].append({'revision': task['revision'], 'instruction': task['instruction']})
            task['revision'] += 1
            task['instruction'] = instruction.strip()
            task['review'] = None
            task['status'] = 'queued'
            self.store.save(task, 'requirements_updated', {'message': 'Requirements revised.', 'revision': task['revision']})
            return task

    def _packet(self, task):
        packet_task = json.loads(json.dumps(task))
        if packet_task.get('baseline'):
            packet_task['baseline'].pop('entries', None)
        if packet_task.get('workflow'):
            if packet_task['workflow'].get('snapshot'):
                packet_task['workflow']['snapshot'].pop('entries', None)
            packet_task['workflow']['history'] = packet_task['workflow']['history'][-6:]
        return {'task': packet_task, 'project': str(self.project), 'git': self.git,
                'rules': ['Read applicable project instructions before editing.',
                          'Use the task requirements; preserve unrelated work.',
                          'Report actual checks and remaining work. Only the assigned reviewer gives its own verdict; never claim approval on behalf of another role.']}

    def start(self, task_id, _managed=False, _internal=False):
        with self.lock:
            task = self.store.get(task_id)
            if task.get('task_kind') == 'workflow_step':
                raise Conflict('Workflow step records are run through their parent task.')
            if task.get('internal') and not _internal:
                raise Conflict('Use the conversation or transcription retry controls for internal work.')
            if self.halt.is_set():
                raise Conflict('Coordinator is shutting down.')
            if task.get('workflow') and not _managed:
                raise Conflict('Use Resume workflow for managed tasks.')
            if self.recovery_required:
                raise Conflict('Interrupted run needs manual process inspection before new work can start.')
            lease = self.conversation._expire()
            if lease and lease['kind'] == 'external':
                raise Conflict('Release the external orchestration turn before starting a worker.')
            if self.github.busy:
                raise Conflict('GitHub publication owns the project.')
            if self.controls.blocked(task):
                raise Conflict('Waiting for prerequisite tasks to be accepted.')
            if self.running_task:
                raise Conflict('Another runner owns this workspace. Wait or stop it first.')
            if task['status'] in ('launching', 'running', 'stopping', 'processing_result', 'publishing', 'interrupted', 'accepted'):
                raise Conflict('This task cannot be started in its current state.')
            workflow = task.get('workflow') if _managed else None
            agent_id = self.workflows.agent_for(workflow) if workflow else task['agent']
            if workflow and workflow['stage'] == 'review' and agent_id == workflow.get('implementation_agent'):
                raise Conflict('The same adapter cannot implement and independently review this snapshot.')
            agent = self.agents[agent_id]
            if not agent['available']:
                raise ValueError('Configure this worker on the host first.')
            if self.controls.mode() == 'offline' and agent.get('kind') == 'model' and agent.get('local') is not True:
                raise Conflict('Project is offline; new cloud model runs are blocked.')
            if workflow and workflow['stage'] == 'implement' and (task.get('baseline') or {}).get('revision') != task['revision']:
                from .snapshots import inventory
                before = git_snapshot(self.project)
                task['baseline'] = {'revision': task['revision'], 'entries': inventory(self.project),
                                    'dirty_paths': [f['path'] for f in before.get('files', [])], 'head': before.get('head')}
            run_id = identifier()
            if workflow:
                stage = workflow['stage']
                child = self.build_task({'title': stage.capitalize() + f" Task {task['task_number']}: {task['title']}",
                                         'instruction': task['instruction'], 'agent': agent_id,
                                         'timeout_seconds': task['timeout_seconds']})
                child.update(task_kind='workflow_step', parent_task_id=task['id'],
                             parent_task_number=task['task_number'], workflow_stage=stage,
                             run_id=run_id, status='launching', active_agent=agent_id,
                             activity='Starting ' + stage + ' step.', next_step='Return result to parent task')
                self.store.save(child, 'workflow_step_created', {'message': child['title'],
                                'parent_task_number': task['task_number'], 'stage': stage})
                task['active_child_id'] = child['id']
            folder = self.state / 'runs' / run_id
            folder.mkdir(parents=True)
            if agent.get('kind') == 'job':
                return self._start_job_implementer(task, agent, run_id, folder)
            prompt = folder / 'task.json'
            packet = self._packet(task)
            packet['result_file'] = str(folder / 'result.json')
            if task.get('internal') == 'conversation':
                packet['conversation'] = self.conversation.packet(task, run_id)
            if workflow:
                packet['workflow'] = self.workflows.packet(task, run_id)
            prompt.write_text(json.dumps(packet, indent=2), encoding='utf-8')
            argv = task['argv'] if agent['id'] == 'local-command' else [
                a.replace('{prompt_file}', str(prompt)).replace('{project}', str(self.project)) for a in agent['argv']]
            if not argv:
                raise ValueError('Local command requires an explicit argv array.')
            opts = {'start_new_session': True} if os.name != 'nt' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
            # Persist intent BEFORE spawn: a crash in the spawn/save gap blocks recovery.
            task.update(status='launching', run_id=run_id, pid=None, active_agent=agent['id'])
            self.store.save(task, 'launching', {'message': 'Launching ' + agent['name'], 'run_id': run_id})
            cwd = folder if task.get('internal') else self.project
            if workflow:
                workflow['phase'] = 'running'
                if workflow['stage'] == 'review':
                    cwd = Path(workflow['snapshot']['path'])
                elif workflow['stage'] == 'coordinate':
                    cwd = folder
            try:
                proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, **opts)
            except OSError as exc:
                task.update(status='failed', active_agent=None, activity='Worker failed to launch.')
                self.store.save(task, 'launch_failed', {'message': str(exc)})
                if task.get('active_child_id'):
                    child = self.store.get(task['active_child_id'])
                    child.update(status='failed', active_agent=None, activity=task['activity'])
                    self.store.save(child, 'workflow_step_finished', {'message': child['activity'],
                                    'parent_task_number': task['task_number'], 'status': 'failed'})
                raise ValueError('Worker failed to launch: ' + str(exc))
            self.process, self.running_task = proc, task_id
            task.update(status='running', active_agent=agent['id'], run_id=run_id, pid=proc.pid,
                        exit_code=None, activity='Runner started; waiting for output.',
                        delivered_revision=task['revision'], next_step='Inspect worker result', review=None, timed_out=False)
            task['runs'].append({'id': run_id, 'pid': proc.pid, 'agent': agent['id'],
                                 'revision': task['revision'], 'started': now(), 'folder': str(folder),
                                 'stage': workflow['stage'] if workflow else 'command'})
            self.store.save(task, 'run_started', {'message': 'Started ' + agent['name'], 'run_id': run_id,
                                                'pid': proc.pid, 'revision': task['revision']})
            if workflow:
                child = self.store.get(task['active_child_id'])
                child.update(status='running', pid=proc.pid, activity='Runner started; waiting for output.')
                child['runs'].append({'id': run_id, 'pid': proc.pid, 'agent': agent['id'],
                                      'revision': task['revision'], 'started': now(), 'folder': str(folder),
                                      'stage': workflow['stage']})
                self.store.save(child, 'workflow_step_started', {'message': child['activity'], 'run_id': run_id})
            self.worker_thread = threading.Thread(target=self._collect, args=(proc, task_id, run_id, folder), daemon=True)
            self.deadline = threading.Timer(task['timeout_seconds'], self._timeout, args=(task_id, run_id))
            self.deadline.daemon = True
            self.deadline.start()
            self.worker_thread.start()
            return task

    def _start_job_implementer(self, task, agent, run_id, folder):
        """Dispatch the implement stage to a capability job instead of a supervised subprocess.

        A job has no PID for ACC to own: it is claimed and executed by a decoupled, possibly
        remote worker and reports back later via IntegrationHub.finish(). The single-writer
        workspace lock (running_task) still applies for the whole wait, same as a subprocess run.
        """
        workflow = task['workflow']
        job = self.integrations.submit({
            'capability': agent['capability'], 'provider': agent.get('provider'),
            'task_id': task['id'], 'input': {
                'title': task['title'], 'instruction': task['instruction'],
                'round': workflow['round'], 'history': workflow['history'][-6:]}})
        workflow['job_id'], workflow['phase'] = job['id'], 'running'
        task.update(status='running', run_id=run_id, pid=None, active_agent=agent['id'], exit_code=None,
                    activity='Dispatched to ' + agent['name'] + '; awaiting job completion.',
                    delivered_revision=task['revision'], next_step='Waiting for job-backed implementer',
                    review=None, timed_out=False)
        task['runs'].append({'id': run_id, 'pid': None, 'agent': agent['id'], 'job_id': job['id'],
                             'revision': task['revision'], 'started': now(), 'folder': str(folder),
                             'stage': 'implement'})
        self.store.save(task, 'run_started', {'message': 'Dispatched job to ' + agent['name'],
                        'run_id': run_id, 'job_id': job['id'], 'revision': task['revision']})
        if task.get('active_child_id'):
            child = self.store.get(task['active_child_id'])
            child.update(status='running', activity=task['activity'])
            child['runs'].append({'id': run_id, 'pid': None, 'agent': agent['id'], 'job_id': job['id'],
                                  'revision': task['revision'], 'started': now(), 'folder': str(folder),
                                  'stage': 'implement'})
            self.store.save(child, 'workflow_step_started', {'message': child['activity'], 'run_id': run_id})
        self.process, self.running_task = None, task['id']
        self.deadline = threading.Timer(task['timeout_seconds'], self._timeout, args=(task['id'], run_id))
        self.deadline.daemon = True
        self.deadline.start()
        return task

    def _conclude_job_child(self, task, child_status):
        if task.get('active_child_id'):
            child = self.store.get(task['active_child_id'])
            child.update(status=child_status, active_agent=None, pid=None, activity=task['activity'])
            if child['runs']:
                child['runs'][-1].update(ended=now())
            self.store.save(child, 'workflow_step_finished', {'message': child['activity'],
                            'parent_task_number': task['task_number'], 'status': child_status})
            task['active_child_id'] = None
            self.store.save(task, 'workflow_child_link_cleared', {'message': 'Numbered workflow step recorded.'})

    def on_job_finished(self, job):
        """Called by IntegrationHub.finish() (after its own transaction commits) for any job
        that carries a task_id. Most calls are a no-op here: only a job that a job-backed
        implementer step is actively waiting on advances the workflow."""
        with self.lock:
            task_id = job.get('task_id')
            if not task_id:
                return
            try:
                task = self.store.get(task_id)
            except KeyError:
                return
            w = task.get('workflow')
            if not (w and w.get('job_id') == job['id'] and w['stage'] == 'implement'
                    and self.running_task == task_id and task['status'] in ('running', 'stopping')):
                return  # Stale, superseded, or already resolved (e.g. marked interrupted at restart).
            if self.deadline:
                self.deadline.cancel()
            stopped = task['status'] == 'stopping'
            try:
                self.workflows.finish_job(task, job, stopped)
            except Exception as exc:
                self.workflows.hold(task, str(exc))
            task = self.store.get(task_id)
            child_status = 'paused' if stopped else ('completed' if task['status'] == 'queued' else 'failed')
            self._conclude_job_child(task, child_status)
            self.controls.apply(task_id)
            self.process, self.running_task = None, None

    def _timeout(self, task_id, run_id):
        with self.lock:
            task = self.store.get(task_id)
            if self.running_task != task_id or task['run_id'] != run_id:
                return
            task['timed_out'] = True
            self.store.save(task, 'timeout', {'message': 'Run deadline exceeded; stopping worker.'})
        try:
            # force=True: a stuck run is exactly the emergency case, not the graceful one -- a
            # job-backed step must actually be asked to stop here, not just left to keep running
            # past its deadline. force is a no-op for a supervised subprocess (already immediate)
            # or a still-queued job (already cancellable outright).
            self.stop(task_id, force=True)
        except (Conflict, OSError, subprocess.TimeoutExpired):
            pass  # Existing run remains owned; the user can inspect or stop it.

    def _collect(self, proc, task_id, run_id, folder):
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        try:
            with (folder / 'output.log').open('w', encoding='utf-8') as log:
                while True:
                    raw = os.read(proc.stdout.fileno(), 4096)
                    if not raw:
                        break
                    chunk = decoder.decode(raw)
                    log.write(chunk)
                    log.flush()
                    with self.lock:
                        task = self.store.get(task_id)
                        task['activity'] = chunk.strip()[-240:] or 'Worker output received.'
                        self.store.save(task, 'output', {'message': chunk, 'run_id': run_id})
            code = proc.wait()
            # Stop leftover children before making this workspace available again.
            if os.name != 'nt':
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            with self.lock:
                if self.deadline:
                    self.deadline.cancel()
                task = self.store.get(task_id)
                stopped = task['status'] == 'stopping'
                task.update(status='failed' if task['timed_out'] else ('paused' if stopped else ('awaiting_review' if code == 0 else 'failed')),
                            exit_code=code, active_agent=None,
                            activity='Run timed out; inspect retained changes.' if task['timed_out'] else ('Stopped; inspect retained files before continuing.' if stopped else f'Runner exited with code {code}.'),
                            next_step='Inspect changes and evidence')
                task['runs'][-1].update(ended=now(), exit_code=code)
                if task.get('workflow') or task.get('internal'):
                    # A crash before the next persisted transition must surface in recovery.
                    task['status'] = 'processing_result'
                (folder / 'handoff.json').write_text(json.dumps(self._packet(task), indent=2), encoding='utf-8')
                self.store.save(task, 'run_ended', {'message': task['activity'], 'run_id': run_id, 'exit_code': code})
                if task.get('workflow'):
                    try:
                        self.workflows.finish(task, folder, code, stopped)
                    except Exception as exc:
                        self.workflows.hold(task, str(exc))
                if task.get('internal'):
                    try:
                        if task['internal'] == 'transcription':
                            self.voice.finish(task, folder, code, stopped)
                        else:
                            self.conversation.finish(task, folder, code, stopped)
                    except Exception as exc:
                        if task['internal'] == 'transcription':
                            task.update(status='paused', activity=str(exc))
                            self.store.save(task, 'transcription_held', {'message': str(exc)})
                        else:
                            self.conversation.fail(task, str(exc), retry=False)
                if task.get('active_child_id'):
                    child = self.store.get(task['active_child_id'])
                    child_status = 'paused' if stopped else ('failed' if code or task['timed_out'] else 'completed')
                    child.update(status=child_status, active_agent=None, pid=None, exit_code=code,
                                 activity=task['activity'], next_step='Inspect parent task handoff')
                    child['runs'][-1].update(ended=now(), exit_code=code)
                    self.store.save(child, 'workflow_step_finished', {'message': child['activity'],
                                    'parent_task_number': task['task_number'], 'status': child_status})
                    task['active_child_id'] = None
                    self.store.save(task, 'workflow_child_link_cleared', {'message': 'Numbered workflow step recorded.'})
                self.controls.apply(task_id)
                self.process, self.running_task = None, None
        except Exception as exc:
            with self.lock:
                task = self.store.get(task_id)
                task.update(status='interrupted', activity='Runner supervision failed; inspect process before resuming.')
                self.recovery_required = True
                self.store.save(task, 'supervisor_error', {'message': str(exc)})
                if task.get('active_child_id'):
                    child = self.store.get(task['active_child_id'])
                    child.update(status='interrupted', active_agent=None, activity=task['activity'])
                    self.store.save(child, 'workflow_step_interrupted', {'message': child['activity']})
        finally:
            proc.stdout.close()

    def stop(self, task_id, force=False):
        """force=False (the default) never discards in-flight work it cannot actually interrupt:
        a job already claimed by a remote worker keeps running to a safe boundary, the same way
        offline mode already lets an in-flight cloud step finish instead of killing it. force=True
        is the "absolutely unwanted, stop it now" escape hatch (also used by _timeout): it asks
        the worker to stop itself, since ACC has no process of its own to kill for a remote job.
        """
        with self.lock:
            if self.running_task != task_id:
                raise Conflict('This task has no supervised active runner.')
            task = self.store.get(task_id)
            job_id = (task.get('workflow') or {}).get('job_id')
            job_backed = job_id and self.agents.get(task.get('active_agent'), {}).get('kind') == 'job'
            claimed = job_backed and (self.integrations.get(job_id) or {}).get('status') == 'running'
            if job_backed and claimed and not force:
                task['workflow']['enabled'] = False
                task.update(activity='Stop requested: a job already claimed by a remote worker '
                                      "can't be forcibly interrupted; letting it finish, then "
                                      'pausing before the next step.',
                            next_step='Waiting for the in-flight job to finish, then paused')
                self.store.save(task, 'stop_requested_graceful', {'message': task['activity']})
                return task
            if task.get('workflow'):
                task['workflow']['enabled'] = False
            task.update(status='stopping', activity='Stopping runner and child processes…')
            self.store.save(task, 'stop_requested', {'message': task['activity']})
            proc = self.process
        if job_backed:
            if claimed:
                # force=True and already claimed: ACC still cannot kill a process it doesn't own.
                # Flag it for the worker's own lease check-in to notice and kill the subprocess
                # it does own; on_job_finished() resolves the task once that report arrives.
                self.integrations.request_cancel(job_id)
                return self.store.get(task_id)
            try:
                cancelled = self.integrations.cancel(job_id)
            except (KeyError, Conflict):
                cancelled = None
            if cancelled and cancelled['status'] == 'cancelled':
                with self.lock:
                    task = self.store.get(task_id)
                    if self.deadline:
                        self.deadline.cancel()
                    self.workflows.hold(task, 'Workflow stopped before the job was claimed.')
                    task = self.store.get(task_id)
                    self._conclude_job_child(task, 'paused')
                    self.controls.apply(task_id)
                    self.process, self.running_task = None, None
            return self.store.get(task_id)
        stop_tree(proc)
        return self.store.get(task_id)

    def report(self, task_id, payload):
        with self.lock:
            task = self.store.get(task_id)
            message = payload.get('message', '')
            if not isinstance(message, str) or not message.strip() or len(message) > 20000:
                raise ValueError('Report needs a message of at most 20,000 characters.')
            entry = {'id': identifier(), 'at': now(), 'message': message,
                     'source': 'orchestrator report', 'revision': task['revision'],
                     'reference': str(payload.get('reference', ''))[:1000]}
            task['evidence'].append(entry)
            self.store.save(task, 'report', entry)
            return task

    def review(self, task_id, payload):
        with self.lock:
            task = self.store.get(task_id)
            if task.get('workflow'):
                raise Conflict('Managed acceptance requires the bound reviewer result and coordinator decision.')
            if task['status'] != 'awaiting_review':
                raise Conflict('Only completed runs awaiting review can be accepted.')
            if payload.get('revision') != task['revision'] or payload.get('run_id') != task['run_id']:
                raise Conflict('Review does not match the current run and requirement revision.')
            if not payload.get('message') or not payload.get('reference'):
                raise ValueError('Review requires findings and a code snapshot/commit reference.')
            task['review'] = {'at': now(), 'message': str(payload['message'])[:20000],
                              'reference': str(payload['reference'])[:1000], 'revision': task['revision'],
                              'run_id': task['run_id'], 'source': 'reported review'}
            task.update(status='accepted', next_step='Publish under project policy', activity='Review acceptance recorded.')
            self.store.save(task, 'review_recorded', task['review'])
            return task

    def recover(self, task_id):
        with self.lock:
            task = self.store.get(task_id)
            if task['status'] != 'interrupted':
                raise Conflict('Task is not interrupted.')
            pid = task.get('pid')
            if pid:
                if os.name == 'nt':
                    result = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                                            capture_output=True, text=True, timeout=10)
                    if result.returncode:
                        raise Conflict('Cannot inspect the previous Windows process.')
                    if any(len(row) > 1 and row[1] == str(pid) for row in csv.reader(result.stdout.splitlines())):
                        raise Conflict('Previous PID still exists; inspect and stop it outside ACC first.')
                else:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        raise Conflict('Cannot establish whether the previous runner is alive.')
                    else:
                        raise Conflict('Previous PID still exists; inspect and stop it outside ACC first.')
            publication = task.get('publication')
            task.update(status='accepted' if publication and task.get('review') else 'paused', active_agent=None,
                        activity='Operator confirmed previous process tree inspected.')
            if publication:
                publication.update(status='needs_attention', error='Interrupted publication inspected. Preview again to reconcile Git and PR state.')
                if self.github.busy == task_id:
                    self.github.busy = None
                    self.github.unsafe_process = False
            if task.get('pending_switch'):
                task['pending_switch'] = None  # Restart recovery requires an explicit new assignment.

            self.store.save(task, 'recovery_acknowledged', {'message': task['activity']})
            if task.get('internal') == 'conversation':
                self.conversation.fail(task, 'Previous process inspected. Retry the saved conversation when ready.', retry=False)
            self.recovery_required = any(t['status'] == 'interrupted' for t in self.store.tasks())
            self.running_task, self.process = None, None
            return task

    def _observe(self):
        while not self.halt.is_set():
            try:
                snap = git_snapshot(self.project)
                with self.lock:
                    if snap != self.git:
                        self.git = snap
                        self.store.event('git_changed', {'message': 'Local Git state changed.', 'git': snap})
            except (OSError, subprocess.TimeoutExpired) as exc:
                with self.lock:
                    self.git = {'available': False, 'message': str(exc)}
            self.halt.wait(1)

    def close(self):
        self.halt.set()
        if self.process:
            try:
                self.stop(self.running_task)
            except (OSError, Conflict, subprocess.TimeoutExpired):
                pass
        if self.worker_thread:
            self.worker_thread.join(timeout=8)
        self.workflows.wake.set()
        self.workflows.thread.join(timeout=10)
        self.observer.join(timeout=10)
        if self.process or (self.worker_thread and self.worker_thread.is_alive()):
            raise Conflict('Runner termination is unconfirmed; workspace lock is retained until process exit.')
        self.github.close()
        self.ownership.close()
        self.project_ownership.close()
