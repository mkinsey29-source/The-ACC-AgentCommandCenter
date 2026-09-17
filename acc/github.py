"""GitHub CLI transport, cached remote activity, and reviewed task publication."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from urllib.parse import quote
from .core import Conflict, identifier, now, stop_tree
from . import snapshots


def repository_from_url(url):
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?', url.strip())
    return match.group(1) if match else None


class GitHub:
    def __init__(self, coordinator, config=None):
        self.c = coordinator
        self.executable = (config or {}).get('executable', 'gh')
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.publisher = None
        self.busy = None
        self.unsafe_process = False
        self.generation = 0
        cached = self.c.conversation.meta('github_cache', {})
        self.state = {**cached, 'connected': False, 'stale': True, 'message': 'Checking GitHub connection.'}
        self.settings = self.c.conversation.meta('github_settings', None) or {
            'enabled': True, 'remote': 'origin', 'source': 'temporary', 'base': 'main', 'interval': 30}

    def command(self, argv, task_id=None, timeout=30):
        opts = {'start_new_session': True} if os.name != 'nt' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        proc = subprocess.Popen(argv, cwd=self.c.project, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GH_PROMPT_DISABLED': '1', 'GIT_LITERAL_PATHSPECS': '1'}, **opts)
        try:
            if task_id:
                with self.c.lock:
                    task = self.c.store.get(task_id); task['pid'] = proc.pid
                    self.c.store.save(task, 'publication_process', {'message': 'Running ' + Path(argv[0]).name, 'pid': proc.pid})
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                raise ValueError('Operation timed out. Inspect publication state before retrying.')
            if proc.returncode:
                # Never echo credential-bearing remote URLs or arbitrary CLI output.
                raise ValueError('Command failed: ' + Path(argv[0]).name + '. Check host authentication, branch access, and local tool output.')
            return out.rstrip('\r\n')
        finally:
            try:
                # Also stop descendants of an exited command (e.g. detached hooks).
                stop_tree(proc)
                proc.communicate(timeout=5)
            except (OSError, Conflict, subprocess.SubprocessError):
                self.unsafe_process = True
                self.c.recovery_required = True
                raise Conflict('Command termination is unconfirmed; project ownership is retained for recovery.')


    def git(self, *args, **kwargs):
        return self.command(['git', *args], **kwargs)

    def gh(self, *args, **kwargs):
        return self.command([self.executable, *args], **kwargs)

    def repo(self, settings=None):
        settings = settings or self.settings
        urls = self.git('remote', 'get-url', '--all', settings['remote']).splitlines()
        if len(urls) != 1:
            raise ValueError('Use one GitHub fetch URL for this remote.')
        repo = repository_from_url(urls[0])
        if not repo:
            raise ValueError('Selected remote must point directly to a github.com repository.')
        return repo

    def configure(self, payload):
        with self.c.lock:
            if self.busy:
                raise Conflict('Wait for publication to finish before changing GitHub settings.')
            value = {**self.settings, **payload}
            if set(value) != {'enabled', 'remote', 'source', 'base', 'interval'} or type(value['enabled']) is not bool:
                raise ValueError('Invalid GitHub settings.')
            for key in ('remote', 'source', 'base'):
                if not isinstance(value[key], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,150}', value[key]) or '..' in value[key]:
                    raise ValueError('Invalid ' + key + '.')
            if value['source'] == value['base'] or value['source'] in ('main', 'master'):
                raise ValueError('Choose a source branch separate from the protected base.')
            if type(value['interval']) is not int or not 15 <= value['interval'] <= 300:
                raise ValueError('Refresh interval must be 15–300 seconds.')
            self.settings = value
            self.generation += 1
            with self.c.store.connect() as db:
                self.c.conversation.put(db, 'github_settings', value)
            self.state.update(connected=False, stale=True, message='Settings changed; refreshing GitHub.')
            self.wake.set()
            return self.snapshot()

    def snapshot(self):
        with self.c.lock:
            return copy.deepcopy({**self.state, 'settings': self.settings, 'publishing_task': self.busy,
                    'installed': bool(shutil.which(self.executable))})

    def refresh(self):
        self.wake.set()
        return self.snapshot()

    def _refresh(self):
        with self.c.lock:
            settings, generation = dict(self.settings), self.generation
            enabled = settings['enabled']
        try:
            if not enabled:
                raise ValueError('GitHub refresh is paused; cached activity remains visible.')
            if not shutil.which(self.executable):
                raise ValueError('Install GitHub CLI, then run gh auth login on this computer.')
            repo = self.repo(settings)
            self.gh('auth', 'status', '--hostname', 'github.com', timeout=10)
            prs = json.loads(self.gh('pr', 'list', '--repo', repo, '--state', 'all', '--limit', '30', '--json',
                  'number,title,url,state,headRefName,baseRefName,headRefOid,isDraft,reviewDecision,statusCheckRollup,updatedAt,mergedAt'))
            commits = json.loads(self.gh('api', 'repos/' + repo + '/commits?sha=' + quote(settings['source'], safe='') + '&per_page=10'))
            value = {'connected': True, 'stale': False, 'repository': repo, 'pull_requests': prs,
                     'commits': [{'sha': x['sha'], 'url': x.get('html_url'), 'subject': x.get('commit', {}).get('message', '').split('\n')[0]} for x in commits],
                     'last_success': now(), 'message': 'GitHub activity refreshed.'}
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            value = {**self.state, 'connected': False, 'stale': True, 'message': str(exc)}
        with self.c.lock:
            if generation != self.generation:
                return
            self.state = value
            with self.c.store.connect() as db:
                self.c.conversation.put(db, 'github_cache', value)
            self.c.store.event('github_refreshed', {'message': value['message']})

    def _loop(self):
        while not self.c.halt.is_set():
            self._refresh()
            self.wake.wait(self.settings['interval']); self.wake.clear()

    def preview(self, task_id):
        with self.c.lock:
            task = self.c.store.get(task_id)
            if task['status'] != 'accepted' or not task.get('workflow') or not task.get('review'):
                raise Conflict('Publication requires completed snapshot-bound independent review.')
            snap = task['workflow']['snapshot']
            if task['review'].get('reference') != snap['id'] or task['review'].get('revision') != task['revision']:
                raise Conflict('Review must identify the current snapshot and task revision.')
            snapshots.verify(snap, self.c.project)
            head = self.git('rev-parse', 'HEAD')
            expected_tree = self.expected_tree(snap, head)
            baseline = task.get('baseline')
            if not baseline or baseline['revision'] != task['revision']:
                raise Conflict('This task predates publication tracking. Run a new implementation/review cycle to establish its baseline.')
            branch = self.git('branch', '--show-current')
            if branch != self.settings['source']:
                raise Conflict('Switch the project to the configured source branch: ' + self.settings['source'])
            changed = sorted(k for k in set(baseline['entries']) | set(snap['entries']) if baseline['entries'].get(k) != snap['entries'].get(k))
            # Block pre-existing edits to files the task also changed: file attribution is not hunk attribution.
            overlap = sorted(set(changed) & set(baseline.get('dirty_paths', [])))
            if overlap:
                raise Conflict('Task overlaps edits present before it started: ' + ', '.join(overlap))
            staged = self.git('diff', '--cached', '--name-only', '-z').split('\0')
            if any(staged):
                raise Conflict('The Git index already contains staged work; finish or unstage it before ACC publication.')
            dirty = set(self.git('diff', '--name-only', '-z').split('\0')) | set(self.git('ls-files', '--others', '--exclude-standard', '-z').split('\0'))
            dirty.discard('')
            excluded = sorted(dirty - set(changed))
            if excluded:
                raise Conflict('Unrelated local changes remain outside this task: ' + ', '.join(excluded))
            repo = self.repo()
            push_urls = self.git('remote', 'get-url', '--push', '--all', self.settings['remote']).splitlines()
            if len(push_urls) != 1 or repository_from_url(push_urls[0]) != repo:
                raise Conflict('Fetch and push must target the same single GitHub repository.')
            head = self.git('rev-parse', 'HEAD')
            remote_heads = self.remote_heads(self.settings['remote'], branch, self.settings['base'])
            remote_base = remote_heads.get(self.settings['base'])
            if not remote_base:
                raise Conflict('The configured base branch does not exist on GitHub.')
            remote_source = remote_heads.get(branch)
            for oid in remote_heads.values():
                try:
                    self.git('cat-file', '-e', oid + '^{commit}')
                except ValueError:
                    raise Conflict('Fetch the selected remote before previewing; remote commits are missing locally.')
            if remote_source and self.git('merge-base', remote_source, head) != remote_source:
                raise Conflict('Source branch diverged from GitHub; reconcile it before publication.')
            outgoing = self.commit_list((remote_source or remote_base) + '..' + head)
            pr_commits = self.commit_list(remote_base + '..' + head)
            with self.c.store.connect() as db:
                key = 'publish_preview_' + task_id
                preview = {'id': identifier(), 'task_id': task_id, 'revision': task['revision'], 'snapshot_id': snap['id'],
                           'repository': repo, 'branch': branch, 'base': self.settings['base'], 'remote': self.settings['remote'], 'push_url': push_urls[0],
                           'head': head, 'expected_tree': expected_tree, 'remote_heads': remote_heads, 'outgoing_commits': outgoing, 'pr_commits': pr_commits, 'paths': sorted(dirty), 'changed_paths': changed, 'created': now(),
                           'title': 'Task ' + str(task['task_number']) + ': ' + task['title'],
                           'body': '## Requested change\n\n' + task['instruction'] + '\n\n## Validation\n\n' + task['review']['message'] + '\n\nACC Task ' + str(task['task_number']) + ' · internal ID ' + task_id + ' · revision ' + str(task['revision'])}
                self.c.conversation.put(db, key, preview)
            return preview

    def publish(self, task_id, payload):
        with self.c.lock:
            task = self.c.store.get(task_id)
            prior = task.get('publication', {})
            if payload.get('request_id') == prior.get('request_id') and prior:
                if payload.get('preview_id') != prior.get('preview', {}).get('id'):
                    raise Conflict('A publication request ID cannot be reused for a different preview.')
                return task
            if (prior.get('status') == 'confirmed'
                    and prior.get('preview', {}).get('revision') == task['revision']
                    and prior.get('preview', {}).get('snapshot_id') == (task.get('workflow', {}).get('snapshot') or {}).get('id')):
                return task
            if self.busy or self.c.running_task or self.c.recovery_required or self.c.conversation._expire():
                raise Conflict('Wait for the active worker/orchestrator to release the project.')
            saved = self.c.conversation.meta('publish_preview_' + task_id)
            if not saved or payload.get('preview_id') != saved['id'] or now() - saved['created'] > 600:
                raise Conflict('Preview publication again before submitting.')
            if not isinstance(payload.get('request_id'), str) or not 1 <= len(payload['request_id']) <= 100:
                raise ValueError('Use a stable publication request ID.')
            if task['status'] != 'accepted':
                raise Conflict('Only accepted work can be published.')
            fresh = self.preview(task_id)
            if any(saved[k] != fresh[k] for k in ('head', 'snapshot_id', 'revision', 'paths', 'repository', 'branch', 'base', 'remote', 'remote_heads', 'outgoing_commits', 'pr_commits', 'title', 'body', 'push_url', 'expected_tree')):
                raise Conflict('Publication target changed; inspect a new preview.')
            task.update(status='publishing', pid=None)
            task['publication'] = {'request_id': payload['request_id'], 'status': 'preparing', 'preview': saved, 'started': now()}
            self.c.store.save(task, 'publication_requested', {'message': 'Publishing reviewed files to ' + saved['branch'] + ' → ' + saved['base']})
            self.busy = task_id
            self.publisher = threading.Thread(target=self._publish, args=(task_id,), daemon=True)
            self.publisher.start()
            return task

    def remote_heads(self, remote, source, base):
        output = self.git('ls-remote', remote, 'refs/heads/' + source, 'refs/heads/' + base)
        result = {}
        for row in output.splitlines():
            oid, ref = row.split('\t', 1)
            if not re.fullmatch(r'[0-9a-f]{40,64}', oid):
                raise ValueError('Invalid remote commit identifier.')
            result[ref.removeprefix('refs/heads/')] = oid
        return result

    def commit_list(self, revisions):
        output = self.git('log', '--format=%H%x00%s', revisions, '--')
        return [dict(zip(('sha', 'subject'), row.split('\0', 1))) for row in output.splitlines()]

    def commit_tree(self, head):
        tree = {}
        for record in self.git('ls-tree', '-rz', head).split('\0'):
            if record:
                info, path = record.split('\t', 1)
                tree[path] = info.split()
        return tree

    def expected_tree(self, snapshot, base_head):
        """Canonicalize frozen bytes using Git's built-in text conversions only.

        Arbitrary clean filters (including LFS) are deliberately unsupported: they
        can execute programs and transform reviewed text beyond Git normalization.
        File modes follow Git's core.filemode policy, preserving pre-publication
        tracked modes when the host filesystem cannot represent executable bits.
        """
        for path in snapshot['entries']:
            attribute = self.git('check-attr', '-z', 'filter', '--', path).split('\0')
            if len(attribute) != 4 or attribute[2] not in ('unspecified', 'unset'):
                raise Conflict('Publication does not support clean filters: ' + path + '. Publish this repository manually.')
        before = self.commit_tree(base_head)
        filemode = self.git('config', '--type=bool', '--default', 'true', '--get', 'core.filemode') == 'true'
        expected = {}
        for path, entry in snapshot['entries'].items():
            oid = self.git('hash-object', '--path=' + path, '--', str(Path(snapshot['path']) / path))
            if filemode:
                mode = '100755' if entry['executable'] else '100644'
            else:
                prior_mode = before.get(path, ['100644'])[0]
                mode = prior_mode if prior_mode in ('100644', '100755') else '100644'
            expected[path] = [mode, 'blob', oid]
        return expected

    def verify_commit(self, snapshot, head, expected=None):
        """Compare the actual tree with the canonical tree bound to the preview."""
        expected = expected if expected is not None else self.expected_tree(snapshot, head)
        if self.commit_tree(head) != expected:
            raise Conflict('Committed files differ from the reviewed snapshot; pushing is blocked.')

    def _save(self, task_id, status, **fields):
        with self.c.lock:
            task = self.c.store.get(task_id)
            task['publication'].update(status=status, **fields)
            task['activity'] = 'Publication: ' + status
            self.c.store.save(task, 'publication_' + status, {'message': task['activity'], **fields})
            return task

    def _publish(self, task_id):
        try:
            task = self.c.store.get(task_id); p = task['publication']['preview']
            snapshots.verify(task['workflow']['snapshot'], self.c.project)
            if self.expected_tree(task['workflow']['snapshot'], p['head']) != p['expected_tree']:
                raise Conflict('Git conversion settings changed; inspect a new preview.')
            head = self.git('rev-parse', 'HEAD')
            if head != p['head'] or self.git('branch', '--show-current') != p['branch']:
                raise Conflict('HEAD or branch changed after publication was requested.')
            if self.git('diff', '--cached', '--name-only', '-z'):
                raise Conflict('The Git index changed after publication was requested.')
            if self.repo() != p['repository']:
                raise Conflict('Remote changed after publication was requested.')
            push_urls = self.git('remote', 'get-url', '--push', '--all', p['remote']).splitlines()
            if push_urls != [p['push_url']]:
                raise Conflict('Push target changed after publication was requested.')
            if self.remote_heads(p['remote'], p['branch'], p['base']) != p['remote_heads']:
                raise Conflict('Remote branches changed; inspect a new preview.')
            if p['paths']:
                self._save(task_id, 'committing')
                self.git('add', '--', *p['paths'], task_id=task_id)
                staged = sorted(filter(None, self.git('diff', '--cached', '--name-only', '-z').split('\0')))
                if staged != p['paths']:
                    raise Conflict('Staged files changed. Inspect the index before retrying.')
                self.git('commit', '-m', p['title'], '-m', 'ACC-Task: ' + str(task['task_number']) + '\nACC-Task-ID: ' + task_id + '\nACC-Revision: ' + str(task['revision']), task_id=task_id, timeout=60)
                head = self.git('rev-parse', 'HEAD')
            self.verify_commit(task['workflow']['snapshot'], head, p['expected_tree'])
            self._save(task_id, 'committed', commit=head)
            snapshots.verify(task['workflow']['snapshot'], self.c.project)
            if self.git('status', '--porcelain'):
                raise Conflict('Commit hooks or another editor changed the project. Review the changes before pushing.')
            if self.git('branch', '--show-current') != p['branch'] or self.git('rev-parse', 'HEAD') != head:
                raise Conflict('Local branch changed during publication.')
            if self.remote_heads(p['remote'], p['branch'], p['base']) != p['remote_heads']:
                raise Conflict('Remote branches changed before push; inspect a new preview.')
            self._save(task_id, 'pushing')
            self.git('push', '--no-follow-tags', p['push_url'], head + ':refs/heads/' + p['branch'], task_id=task_id, timeout=60)
            remote = self.git('ls-remote', p['push_url'], 'refs/heads/' + p['branch'], task_id=task_id)
            if not remote.startswith(head + '\t'):
                raise Conflict('Remote branch does not match the reviewed commit.')
            self._save(task_id, 'pushed', commit=head)
            found = json.loads(self.gh('pr', 'list', '--repo', p['repository'], '--head', p['branch'], '--base', p['base'], '--state', 'open', '--json', 'number,url,headRefOid', task_id=task_id))
            if found:
                if len(found) != 1 or found[0].get('headRefOid') != head:
                    raise Conflict('Existing pull request head does not match the reviewed commit; refresh and inspect it.')
                url = found[0]['url']
            else:
                self._save(task_id, 'creating_pr')
                body = self.c.state / ('pr-' + task_id + '.md'); body.write_text(p['body'], encoding='utf-8')
                url = self.gh('pr', 'create', '--repo', p['repository'], '--head', p['branch'], '--base', p['base'], '--title', p['title'], '--body-file', str(body), task_id=task_id, timeout=60)
            if not re.fullmatch(r'https://github\.com/' + re.escape(p['repository']) + r'/pull/[1-9][0-9]*', url):
                raise Conflict('GitHub returned an unexpected pull request URL.')
            confirmed = json.loads(self.gh('pr', 'view', url, '--repo', p['repository'], '--json',
                                          'url,headRefOid,headRefName,baseRefName,state', task_id=task_id))
            if (confirmed.get('headRefOid') != head or confirmed.get('headRefName') != p['branch']
                    or confirmed.get('baseRefName') != p['base'] or confirmed.get('state') != 'OPEN'):
                raise Conflict('Pull request changed before confirmation; inspect it before retrying.')
            self._save(task_id, 'confirmed', commit=head, url=confirmed['url'])
        except Exception as exc:
            self._save(task_id, 'needs_attention', error=str(exc))
        finally:
            with self.c.lock:
                task = self.c.store.get(task_id)
                if self.unsafe_process:
                    task.update(status='interrupted', next_step='Confirm publication processes stopped before recovery')
                    self.c.recovery_required = True
                else:
                    task.update(status='accepted', pid=None, next_step='Inspect publication status')
                self.c.store.save(task, 'publication_finished', {'message': task['publication']['status']})
                if not self.unsafe_process:
                    self.busy = None
            self.wake.set()

    def close(self):
        self.wake.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=35)
        if self.publisher:
            self.publisher.join(timeout=5)
            if self.publisher.is_alive():
                raise Conflict('Publication still owns the project. Wait for its terminal result before closing.')
        if self.unsafe_process or self.thread.is_alive():
            raise Conflict('GitHub command termination is unconfirmed; retain the project lock.')
