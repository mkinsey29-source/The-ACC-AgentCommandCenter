"""Persistent assignment requests and dependency-aware task scheduling."""
import json
from .core import Conflict, now


class Controls:
    def __init__(self, coordinator):
        self.c = coordinator

    def mode(self):
        with self.c.store.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='project_mode'").fetchone()
        return row[0] if row and row[0] in ('online', 'offline') else 'online'

    def set_mode(self, payload):
        mode = payload.get('mode')
        if mode not in ('online', 'offline'):
            raise ValueError('Project mode must be online or offline.')
        with self.c.lock:
            previous = self.mode()
            with self.c.store.connect() as db:
                db.execute("INSERT OR REPLACE INTO meta VALUES ('project_mode',?)", (mode,))
            running = self.c.store.get(self.c.running_task) if self.c.running_task else None
            message = 'Project is ' + mode + '. '
            if mode == 'offline' and running and not self.c.agents[running.get('active_agent') or running.get('agent')].get('local'):
                message += 'The active cloud step may finish; no new cloud step will start.'
            else:
                message += 'New assignments now follow this policy.'
            self.c.store.event('project_mode_changed', {'message': message, 'mode': mode, 'previous': previous})
            self.c.workflows.wake.set()
            return {'mode': mode, 'previous': previous, 'message': message}

    def recovery_handoff(self, task_id, payload):
        """Create a numbered successor only after the prior writer is no longer active."""
        c = self.c
        with c.lock:
            task = c.store.get(task_id)
            if c.running_task == task_id or task['status'] in ('launching', 'running', 'stopping', 'processing_result', 'publishing'):
                raise Conflict('Stop the active worker and wait for its terminal state before replacement.')
            replacement = payload.get('agent')
            if replacement not in c.agents or not c.agents[replacement]['available']:
                raise ValueError('Choose an available replacement agent.')
            if self.mode() == 'offline' and c.agents[replacement].get('local') is not True:
                raise Conflict('Offline mode permits only a configured local replacement.')
            reason = payload.get('reason', '')
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
                raise ValueError('Provide the unavailable-worker reason (up to 2,000 characters).')
            git = c.git if c.git.get('available') else {'message': c.git.get('message')}
            latest = task.get('evidence', [])[-1] if task.get('evidence') else None
            packet = {
                'kind': 'incomplete-worker recovery', 'parent_task_id': task['id'],
                'parent_task_number': task['task_number'], 'original_requirement': task['instruction'],
                'requirement_revision': task['revision'], 'status': task['status'],
                'active_phase': (task.get('workflow') or {}).get('stage'), 'starting_commit': (task.get('baseline') or {}).get('head'),
                'current_commit': git.get('head'), 'branch': git.get('branch'), 'changed_files': git.get('files', []),
                'last_report': latest, 'runs': task.get('runs', [])[-3:], 'unavailable_reason': reason.strip(),
                'warning': 'Inspect the actual worktree and process state. Preserve correct work, repair or remove incomplete work, then continue.'}
            successor = c.build_task({'title': f"Recover Task {task['task_number']}: {task['title']}",
                                      'instruction': json.dumps(packet, indent=2), 'agent': replacement})
            successor.update(parent_task_id=task['id'], parent_task_number=task['task_number'], task_kind='recovery')
            c.store.save(successor, 'recovery_task_created', {'message': successor['title'],
                         'parent_task_number': task['task_number']})
            task.update(next_step=f"Continue through Task {successor['task_number']}", recovery_successor_id=successor['id'])
            c.store.save(task, 'recovery_handoff_created', {'message': task['next_step'],
                         'successor_task_number': successor['task_number']})
            return successor

    def switch(self, task_id, payload):
        c = self.c
        with c.lock:
            task = c.store.get(task_id)
            if task.get('internal') or not task.get('workflow'):
                raise Conflict('Switching roles requires a managed task; use routing for conversation agents.')
            agent, role, request = payload.get('agent'), payload.get('role'), payload.get('request_id')
            if not isinstance(request, str) or not 1 <= len(request) <= 100:
                raise ValueError('Provide a stable request ID.')
            prior = next((x for x in task.get('switch_history', []) if x['id'] == request), None)
            pending = task.get('pending_switch')
            prior = prior or (pending if pending and pending['id'] == request else None)
            if prior:
                if (prior['role'], prior['agent']) != (role, agent):
                    raise Conflict('Request ID was already used for a different switch.')
                return task
            if task['status'] in ('accepted', 'publishing', 'interrupted'):
                raise Conflict('Inspect or restart this task before changing its roles.')
            if pending:
                raise Conflict('A switch is already waiting for the current step to finish.')
            if role not in ('implementer', 'reviewer', 'coordinator') or agent not in c.agents or not c.agents[agent]['available']:
                raise ValueError('Choose an available agent and a workflow role.')
            c.workflows.specification({role: agent}, task['workflow'])
            if role == 'reviewer' and agent == task['workflow'].get('implementation_agent'):
                raise Conflict('The reviewer must differ from the actual implementer.')
            task['pending_switch'] = {'id': request, 'role': role, 'agent': agent, 'at': now(), 'run_id': task['run_id']}
            c.store.save(task, 'switch_requested', {'message': 'Switch ' + role + ' to ' + agent + ' after the current step.'})
            if task['status'] not in ('launching', 'running', 'stopping', 'processing_result'):
                self.apply(task_id)
            return c.store.get(task_id)

    def apply(self, task_id):
        c = self.c
        task = c.store.get(task_id)
        pending = task.get('pending_switch')
        if not pending:
            return
        w = task['workflow']
        role, agent = pending['role'], pending['agent']
        w[role] = agent
        w['fallback_used'] = False
        if role == 'implementer':
            task['agent'] = agent
        if role == 'reviewer' and w.get('snapshot'):
            # A verdict from the previous reviewer cannot satisfy the replacement's review.
            w['review_result'] = None
            task['review'] = None
            w['stage'] = 'coordinate'
            if w['phase'] == 'complete':
                w.update(phase='queued', enabled=True)
                task['status'] = 'queued'
        pending['applied_at'] = now()
        task.setdefault('switch_history', []).append(pending)
        task['pending_switch'] = None
        c.store.save(task, 'switch_applied', {'message': role + ' is now assigned to ' + agent + '. Completed work is retained.'})
        c.workflows.wake.set()

    def schedule(self, task_id, payload):
        c = self.c
        with c.lock:
            task = c.store.get(task_id)
            if task.get('internal') or task.get('task_kind') == 'workflow_step' or task['status'] in ('launching', 'running', 'stopping', 'processing_result', 'publishing', 'interrupted'):
                raise Conflict('Schedule only idle project tasks.')
            priority = payload.get('priority', task.get('priority', 50))
            dependencies = payload.get('depends_on', task.get('depends_on', []))
            if type(priority) is not int or not 0 <= priority <= 100:
                raise ValueError('Priority must be an integer from 0 to 100; higher runs first.')
            if not isinstance(dependencies, list) or not all(isinstance(x, str) for x in dependencies) or len(dependencies) != len(set(dependencies)):
                raise ValueError('Dependencies must be distinct task IDs.')
            tasks = {t['id']: t for t in c.store.tasks() if not t.get('internal') and t.get('task_kind') != 'workflow_step'}
            if any(x not in tasks or x == task_id for x in dependencies):
                raise ValueError('Dependencies must be other existing project tasks.')
            def reaches(current, seen):
                if current == task_id:
                    return True
                if current in seen:
                    return False
                seen.add(current)
                return any(reaches(x, seen) for x in tasks[current].get('depends_on', []))
            if any(reaches(x, set()) for x in dependencies):
                raise Conflict('These dependencies would create a cycle.')
            task.update(priority=priority, depends_on=dependencies)
            c.store.save(task, 'schedule_updated', {'message': 'Task priority and dependencies updated.'})
            c.workflows.wake.set()
            return task

    def blocked(self, task):
        return [x for x in task.get('depends_on', []) if self.c.store.get(x)['status'] != 'accepted']
