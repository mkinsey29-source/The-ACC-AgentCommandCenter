"""Agent 3D Studio worker: fulfils agent-3d-studio integration jobs with a coding-agent CLI.

Polls ACC's integration job queue for jobs routed to the ``agent-3d-studio`` provider,
asks a coding-agent CLI (Claude Code by default) to build the asset itself using this
project's own 3D skill chain (``3d-production-routing`` / ``img2threejs`` / etc.) instead
of a remote mesh-generation API, and reports the produced files back as job artifacts.
No SDK dependency; talks to ACC over its existing HTTP API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROVIDER = 'agent-3d-studio'
RESULT_SHAPE = (
    '{"status": "succeeded" or "failed", "summary": "...", '
    '"artifacts": [{"kind": "model|texture|rig|preview", "uri": "path relative to the '
    'project root", "metadata": {}}], "error": "present only when status is failed"}'
)


def _request(url, token, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url.rstrip('/') + path, data=data, method=method,
                                  headers={'Authorization': 'Bearer ' + token,
                                           'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{method} {path} failed ({exc.code}): {exc.read().decode()}') from exc


def _prompt_for(job, result_file):
    return (
        'You are the agent-3d-studio worker for ACC. A capability job is waiting; build its '
        "3D asset yourself using this project's own skills, not a third-party mesh-generation "
        'API. Use `3d-production-routing` to pick the route from the supplied reference '
        '(typically `image-reference-workflow` / `character-sheet-pipeline` to prepare it, then '
        '`img2threejs` for a quality-gated procedural reconstruction, then `materials-to-game` '
        'and `blender-game-animation` as the capability requires).\n\n'
        f'capability: {job["capability"]}\n'
        f'job_id: {job["id"]}\n'
        'input (JSON):\n' + json.dumps(job.get('input', {}), indent=2) + '\n\n'
        "Write every produced file under the input's `output_dir` (relative to this project). "
        'Do not fabricate results: if a step fails, say so honestly.\n\n'
        f'When finished, write a single JSON object to {result_file} (no markdown fences, no '
        f'other content in that file), shaped exactly as:\n{RESULT_SHAPE}\n'
    )


def _run_agent(executable, workspace, prompt, permission_mode, extra_args, timeout_seconds):
    argv = [executable, '-p', '--output-format', 'json', '--permission-mode', permission_mode]
    argv += extra_args
    proc = subprocess.Popen(argv, cwd=str(workspace), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding='utf-8')
    try:
        output, _ = proc.communicate(prompt, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise TimeoutError(f'{executable} exceeded {timeout_seconds}s and was stopped.')
    return proc.returncode, output


def _load_result(result_file, workspace):
    if not result_file.exists():
        raise ValueError('Agent did not write a result manifest at ' + str(result_file))
    manifest = json.loads(result_file.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('status') not in ('succeeded', 'failed'):
        raise ValueError('Result manifest must be a JSON object with status succeeded/failed.')
    root = workspace.resolve()
    artifacts = []
    for item in manifest.get('artifacts', []):
        uri = item['uri']
        path = (root / uri).resolve()
        if path != root and root not in path.parents:
            raise ValueError('Artifact uri must stay inside the project workspace: ' + uri)
        if not path.is_file():
            raise ValueError('Declared artifact is missing on disk: ' + uri)
        artifacts.append({'kind': item.get('kind', 'model'), 'uri': uri,
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'metadata': item.get('metadata', {})})
    return manifest, artifacts


class LeaseKeeper(threading.Thread):
    """Renews a claimed job's lease in the background while the agent is still working."""

    def __init__(self, url, token, job_id, lease_token, fence, lease_seconds):
        super().__init__(daemon=True)
        self.url, self.token, self.job_id = url, token, job_id
        self.lease_token, self.fence, self.lease_seconds = lease_token, fence, lease_seconds
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.wait(max(5, self.lease_seconds // 3)):
            try:
                _request(self.url, self.token, 'POST',
                          f'/api/integrations/jobs/{self.job_id}/renew',
                          {'lease_token': self.lease_token, 'fence': self.fence,
                           'lease_seconds': self.lease_seconds})
            except RuntimeError:
                return


def process_once(args, token):
    claimed = _request(args.url, token, 'POST', '/api/integrations/claim',
                        {'owner': args.owner, 'provider': PROVIDER,
                         'lease_seconds': args.lease_seconds})
    job = claimed.get('job')
    if job is None:
        return False
    lease_token, fence = claimed['lease_token'], job['fence']
    workspace = Path(args.workspace)
    result_file = workspace.resolve() / f".acc-agent3d-{job['id']}.json"
    keeper = LeaseKeeper(args.url, token, job['id'], lease_token, fence, args.lease_seconds)
    keeper.start()
    try:
        prompt = _prompt_for(job, result_file)
        code, output = _run_agent(args.executable, workspace, prompt, args.permission_mode,
                                   args.extra_args, args.timeout_seconds)
        if code != 0:
            raise ValueError(f'{args.executable} exited {code}:\n{output[-2000:]}')
        manifest, artifacts = _load_result(result_file, workspace)
        payload = {'lease_token': lease_token, 'fence': fence, 'status': manifest['status'],
                   'result': {'summary': manifest.get('summary', '')}, 'artifacts': artifacts}
        if manifest['status'] == 'failed':
            payload['error'] = str(manifest.get('error', 'Agent reported failure.'))[:5000]
    except Exception as exc:
        payload = {'lease_token': lease_token, 'fence': fence, 'status': 'failed',
                   'error': str(exc)[:5000]}
    finally:
        keeper.stop()
        result_file.unlink(missing_ok=True)
    _request(args.url, token, 'POST', f"/api/integrations/jobs/{job['id']}/finish", payload)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description='Agent 3D Studio worker for ACC')
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--workspace', required=True, help='The managed project directory')
    parser.add_argument('--executable', default='claude')
    parser.add_argument('--owner', default='agent-3d-studio-worker')
    parser.add_argument('--permission-mode', default='acceptEdits')
    parser.add_argument('--extra-args', nargs='*', default=[],
                         help='Extra argv passed through to the agent CLI, e.g. --model sonnet')
    parser.add_argument('--lease-seconds', type=int, default=300)
    parser.add_argument('--timeout-seconds', type=int, default=1800)
    parser.add_argument('--poll-seconds', type=float, default=5.0)
    parser.add_argument('--once', action='store_true', help='Process at most one job and exit')
    args = parser.parse_args(argv)
    token = Path(args.token_file).read_text(encoding='utf-8').strip()
    while True:
        try:
            worked = process_once(args, token)
        except RuntimeError as exc:
            print('agent3d: ' + str(exc), file=sys.stderr)
            worked = False
        if args.once:
            return 0
        if not worked:
            time.sleep(args.poll_seconds)


if __name__ == '__main__':
    sys.exit(main())
