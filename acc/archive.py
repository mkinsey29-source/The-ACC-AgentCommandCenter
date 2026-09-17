"""Searchable task history and explicit human-readable/database exports."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path


class Archive:
    def __init__(self, coordinator):
        self.c = coordinator

    def search(self, filters=None):
        filters = filters or {}
        query = str(filters.get('q', '')).strip().lower()
        status = str(filters.get('status', '')).strip()
        agent = str(filters.get('agent', '')).strip()
        try:
            start = int(filters.get('number_from', 0) or 0)
            end = int(filters.get('number_to', 0) or 0)
            limit = int(filters.get('limit', 100) or 100)
        except (TypeError, ValueError):
            raise ValueError('Task ranges and limits must be integers.')
        if start < 0 or end < 0 or (end and start > end) or not 1 <= limit <= 500:
            raise ValueError('Invalid task-number range or result limit.')
        results = []
        for task in reversed(self.c.store.tasks()):
            if task.get('internal'):
                continue
            number = task['task_number']
            if start and number < start or end and number > end:
                continue
            if status and task.get('status') != status:
                continue
            related = {task.get('agent'), task.get('active_agent')}
            related.update((task.get('workflow') or {}).get(role) for role in ('implementer', 'reviewer', 'coordinator'))
            if agent and agent not in related:
                continue
            searchable = json.dumps({k: task.get(k) for k in ('task_number', 'title', 'instruction', 'status',
                                     'agent', 'active_agent', 'activity', 'next_step', 'publication', 'baseline',
                                     'runs', 'evidence', 'workflow', 'parent_task_number')}, default=str).lower()
            if query and query not in searchable:
                continue
            results.append(task)
            if len(results) == limit:
                break
        return {'tasks': results, 'count': len(results), 'filters': filters}

    def export(self, payload):
        raw = payload.get('directory')
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('Choose an existing absolute export directory.')
        destination = Path(raw).expanduser()
        if not destination.is_absolute() or not destination.is_dir() or destination.is_symlink():
            raise ValueError('Export directory must be an existing absolute non-symlink directory.')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')
        markdown = destination / f'acc-task-archive-{stamp}.md'
        database = destination / f'acc-state-backup-{stamp}.sqlite3'
        tasks = [task for task in self.c.store.tasks() if not task.get('internal')]
        lines = ['# ACC task archive', '', f'Exported: {datetime.now(timezone.utc).isoformat()}',
                 f'Project: {self.c.project}', f'Tasks: {len(tasks)}', '']
        for task in tasks:
            lines += [f"## Task {task['task_number']}: {task['title']}", '',
                      f"- Status: {task['status']}", f"- Created: {task['created']}",
                      f"- Assigned: {task.get('agent') or 'None'}", f"- Internal ID: `{task['id']}`",
                      f"- Parent task: {task.get('parent_task_number') or 'None'}",
                      f"- Revision: {task['revision']}", '', '### Current instruction', '', task['instruction'], '',
                      '### Current state', '', task.get('activity', ''), '', f"Next: {task.get('next_step', '')}", '',
                      '### Requirements history', '', '```json', json.dumps(task.get('requirements_history', []), indent=2), '```', '',
                      '### Evidence, runs, review, and publication', '', '```json',
                      json.dumps({k: task.get(k) for k in ('evidence', 'runs', 'review', 'publication', 'baseline')}, indent=2, default=str),
                      '```', '']
        temporary = markdown.with_suffix('.tmp')
        temporary.write_text('\n'.join(lines), encoding='utf-8')
        temporary.replace(markdown)
        with self.c.store.connect() as source:
            import sqlite3
            target = sqlite3.connect(database)
            try:
                source.backup(target)
            finally:
                target.close()
        self.c.store.event('archive_exported', {'message': f'Exported {len(tasks)} tasks.',
                           'markdown': str(markdown), 'database': str(database)})
        return {'markdown': str(markdown), 'database': str(database), 'tasks': len(tasks)}
