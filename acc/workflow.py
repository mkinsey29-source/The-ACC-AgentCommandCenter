"""Persisted sequential handoffs. Models propose; ACC validates and executes."""
import json
from pathlib import Path
import threading
from . import snapshots
from .core import Conflict, now


class Workflows:
    def __init__(self, coordinator):
        self.c = coordinator
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def specification(self, payload, old=None):
        c = self.c
        if c.state.is_relative_to(c.project):
            raise ValueError('Managed workflows require the state directory outside the project.')
        roles = {role: payload.get(role, (old or {}).get(role)) for role in ('implementer', 'reviewer', 'coordinator')}
        for role, agent in roles.items():
            if agent not in c.agents or agent == 'local-command':
                raise ValueError('Configure a command adapter for ' + role + '.')
        if roles['implementer'] == roles['reviewer']:
            raise ValueError('Use a separate reviewer adapter.')
        rounds = payload.get('max_rounds', (old or {}).get('max_rounds', 3))
        if type(rounds) is not int or not 1 <= rounds <= 10:
            raise ValueError('Correction limit must be 1–10 rounds.')
        mode = payload.get('mode', (old or {}).get('mode', 'online'))
        if mode not in ('online', 'offline'):
            raise ValueError('Mode must be online or offline.')
        fallback = payload.get('fallbacks', (old or {}).get('fallbacks', {}))
        if not isinstance(fallback, dict) or set(fallback) - set(roles):
            raise ValueError('Fallbacks must map workflow roles to configured local adapters.')
        for agent in fallback.values():
            if agent not in c.agents or c.agents[agent].get('local') is not True:
                raise ValueError('Fallback adapters must explicitly declare local: true.')
        return {**roles, 'max_rounds': rounds, 'mode': mode, 'fallbacks': fallback}

    @staticmethod
    def initial(spec):
        return {**spec, 'enabled': True, 'stage': 'implement', 'phase': 'queued', 'round': 1,
                'snapshot': None, 'implementation': None, 'review_result': None,
                'fallback_used': False, 'history': []}

    def configure(self, task_id, payload):
        c = self.c
        with c.lock:
            task = c.store.get(task_id)
            if task['status'] == 'publishing':
                raise Conflict('Wait for publication to finish.')
            if task.get('pending_switch'):
                raise Conflict('Wait for the requested role switch to finish.')
            if payload.get('enabled') is False:
                if task.get('workflow'):
                    task['workflow']['enabled'] = False
                    task['next_step'] = 'Pause after the current run; resume when ready'
                c.store.save(task, 'workflow_paused', {'message': 'Background coordination paused; current runner may finish.'})
                return task
            if task['status'] in ('launching', 'running', 'stopping', 'processing_result', 'publishing', 'interrupted'):
                raise Conflict('Stop and inspect the active runner before changing its workflow.')
            if task['status'] == 'accepted' and payload.get('restart') is not True:
                raise Conflict('Task is already accepted. Select Restart implementation to begin another cycle.')
            old = task.get('workflow')
            spec = self.specification(payload, old)
            roles = {r: spec[r] for r in ('implementer', 'reviewer', 'coordinator')}
            rounds, mode, fallback = spec['max_rounds'], spec['mode'], spec['fallbacks']
            if old and task['status'] != 'accepted' and payload.get('restart') is not True:
                if old['reviewer'] != roles['reviewer'] or old['fallbacks'].get('reviewer') != fallback.get('reviewer'):
                    old['review_result'] = None
                    if old.get('snapshot'):
                        old['stage'] = 'coordinate'
                old.update(**roles, max_rounds=rounds, mode=mode, fallbacks=fallback, enabled=True)
                old['fallback_used'] = False
                # Resume the held stage. Never replay a completed implementation implicitly.
                old['phase'] = 'queued'
            else:
                task['workflow'] = {**roles, 'max_rounds': rounds, 'mode': mode, 'fallbacks': fallback,
                                    'enabled': True, 'stage': 'implement', 'phase': 'queued', 'round': 1,
                                    'snapshot': None, 'implementation': None, 'review_result': None,
                                    'fallback_used': False, 'history': []}
                task['review'] = None
                task.pop('baseline', None)
            task['agent'] = roles['implementer']
            task.update(status='queued', next_step='Background coordinator will run ' + task['workflow']['stage'])
            c.store.save(task, 'workflow_configured', {'message': task['next_step'], 'roles': roles, 'mode': mode})
            self.wake.set()
            return task

    def agent_for(self, workflow):
        role = {'implement': 'implementer', 'review': 'reviewer', 'coordinate': 'coordinator'}[workflow['stage']]
        preferred = workflow[role]
        fallback = workflow['fallbacks'].get(role)
        if workflow['mode'] == 'offline' and self.c.agents[preferred].get('local') is not True:
            if not fallback:
                raise ValueError('Offline: no permitted local adapter for ' + role + '. Waiting for connectivity or assignment.')
            return fallback
        return fallback if workflow.get('fallback_used') and fallback else preferred

    def packet(self, task, run_id):
        w = task['workflow']
        snapshot = w.get('snapshot')
        if snapshot and w['stage'] != 'implement':
            snapshots.verify(snapshot, self.c.project)
        return {'stage': w['stage'], 'round': w['round'], 'max_rounds': w['max_rounds'],
                'task_id': task['id'], 'run_id': run_id, 'revision': task['revision'],
                'snapshot_id': snapshot['id'] if snapshot else None,
                'snapshot_path': snapshot['path'] if snapshot else None,
                'implementation': w['implementation'], 'review_result': w['review_result'],
                'history': w['history'][-6:],
                'allowed_actions': (['request_review', 'hold'] if not w['review_result'] else
                                    ['accept', 'request_changes', 'hold']),
                'result_contract': {
                    'common': 'Return a JSON object with task_id, run_id, revision, snapshot_id, summary.',
                    'implement': 'Include checks: array of actual checks and results; do not claim review approval.',
                    'review': 'Include verdict: approve or changes_requested, findings: array, checks: array. Review original requirements against snapshot; do not edit project or snapshot.',
                    'coordinate': 'Include action from allowed_actions. Interpret reports; do not edit code, run workers directly, or claim acceptance without approving review.'}}

    def hold(self, task, message):
        task['workflow'].update(enabled=False, phase='held')
        task.update(status='paused', activity=message, next_step='Inspect the report, then resume or change assignment')
        self.c.store.save(task, 'workflow_held', {'message': message})

    def finish(self, task, folder, code, stopped):
        w = task['workflow']
        if stopped or task['timed_out']:
            self.hold(task, 'Workflow stopped or timed out; retained files need inspection.')
            return
        if code:
            role = {'implement': 'implementer', 'review': 'reviewer', 'coordinate': 'coordinator'}[w['stage']]
            # Only coordinator failure can safely retry automatically: workers may have partial edits.
            fallback = w['fallbacks'].get(role)
            if w['stage'] == 'coordinate' and not w.get('fallback_used') and fallback and task['runs'][-1]['agent'] != fallback:
                snapshots.verify(w['snapshot'], self.c.project)
                w.update(fallback_used=True, phase='queued')
                task.update(status='queued', next_step='Use the permitted local coordinator fallback')
                self.c.store.save(task, 'coordinator_fallback', {'message': task['next_step']})
                self.wake.set()
                return
            self.hold(task, 'Runner failed. Inspect output and partial work before retrying.')
            return
        result_file = folder / 'result.json'
        if result_file.is_symlink() or not result_file.is_file() or result_file.stat().st_size > 100000:
            raise ValueError('Runner must return a structured result.json of at most 100 KB.')
        result = json.loads(result_file.read_text(encoding='utf-8'))
        expected = {'task_id': task['id'], 'run_id': task['run_id'], 'revision': task['revision'],
                    'snapshot_id': w['snapshot']['id'] if w['snapshot'] else None}
        if not isinstance(result, dict) or type(result.get('revision')) is not int or any(result.get(k) != v for k, v in expected.items()):
            raise ValueError('Result does not match the current task, run, revision, and snapshot.')
        if not isinstance(result.get('summary'), str) or not result['summary'].strip():
            raise ValueError('Result needs a plain-language summary.')
        stage = w['stage']
        if stage != 'implement':
            snapshots.verify(w['snapshot'], self.c.project)
        if stage in ('implement', 'review') and not isinstance(result.get('checks'), list):
            raise ValueError('Worker result must list actual checks (an empty list means none).')
        w['history'].append({'stage': stage, 'agent': task['runs'][-1]['agent'], 'at': now(), 'result': result})
        if stage == 'implement':
            w['snapshot'] = snapshots.freeze(self.c.project, folder / 'snapshot')
            w['implementation'] = result
            w['implementation_agent'] = task['runs'][-1]['agent']
            w['review_result'] = None
            w['stage'] = 'coordinate'
        elif stage == 'review':
            if result.get('verdict') not in ('approve', 'changes_requested') or not isinstance(result.get('findings'), list):
                raise ValueError('Review must include a verdict and findings list.')
            w['review_result'] = result
            w['stage'] = 'coordinate'
        else:
            action = result.get('action')
            if action == 'request_review' and w['review_result'] is None:
                w['stage'] = 'review'
            elif action == 'request_changes' and w['review_result'] is not None:
                if w['round'] >= w['max_rounds']:
                    self.hold(task, 'Correction limit reached; main orchestrator needs to inspect findings.')
                    return
                w['round'] += 1
                w['stage'] = 'implement'
                # Keep findings in history, but invalidate old snapshot and approval.
                w['snapshot'] = None
                w['review_result'] = None
            elif action == 'accept' and w['review_result'] and w['review_result']['verdict'] == 'approve':
                w.update(enabled=False, phase='complete')
                task['review'] = {'source': 'managed independent review', 'reference': w['snapshot']['id'],
                                  'revision': task['revision'], 'run_id': w['implementation']['run_id'],
                                  'review_run_id': w['review_result']['run_id'], 'at': now(),
                                  'message': w['review_result']['summary']}
                task.update(status='accepted', next_step='Publish under project policy', activity=result['summary'])
                self.c.store.save(task, 'workflow_accepted', task['review'])
                return
            elif action == 'hold':
                self.hold(task, result['summary'])
                return
            else:
                raise ValueError('Coordinator proposed an invalid transition or acceptance without approval.')
        w.update(phase='queued', fallback_used=False)
        task.update(status='queued', next_step='Run ' + w['stage'], activity=result['summary'])
        self.c.store.save(task, 'handoff_ready', {'message': task['next_step'], 'summary': result['summary']})
        self.wake.set()

    def _loop(self):
        c = self.c
        while not c.halt.is_set():
            self.wake.wait(.5)
            self.wake.clear()
            with c.lock:
                if c.halt.is_set() or c.running_task or c.recovery_required or c.github.busy:
                    continue
                try:
                    if c.conversation.tick():
                        continue
                except Exception as exc:
                    c.conversation.scheduler_error(str(exc))
                for task in sorted(c.store.tasks(), key=lambda t: (-t.get('priority', 50), t['created'])):
                    if c.controls.blocked(task):
                        continue
                    w = task.get('workflow')
                    if w and w['enabled'] and w['phase'] == 'queued':
                        try:
                            c.start(task['id'], _managed=True)
                        except Exception as exc:
                            self.hold(c.store.get(task['id']), str(exc))
                        break
