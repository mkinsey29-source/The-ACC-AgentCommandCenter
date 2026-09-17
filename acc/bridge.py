"""Minimal MCP stdio bridge to the running ACC coordinator. No model calls."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit


TOOLS = [
    ('acc_configure_workflow', 'Configure or resume sequential implementation, coordinator, and independent review. Starts approved work in the background; use enabled:false to pause between runs.',
     {'task_id': {'type': 'string'}, 'enabled': {'type': 'boolean'}, 'restart': {'type': 'boolean'},
      'implementer': {'type': 'string'}, 'reviewer': {'type': 'string'}, 'coordinator': {'type': 'string'},
      'max_rounds': {'type': 'integer'}, 'mode': {'type': 'string', 'enum': ['online', 'offline']},
      'fallbacks': {'type': 'object', 'properties': {r: {'type': 'string'} for r in ('implementer', 'reviewer', 'coordinator')}, 'additionalProperties': False}}, ['task_id']),
    ('acc_state', 'Read tasks, workers, local Git state, and current event cursor.', {}, []),
    ('acc_create_task', 'Record an instruction and optional explicit local command. Does not start it.',
     {'title': {'type': 'string'}, 'instruction': {'type': 'string'}, 'agent': {'type': 'string'},
      'argv': {'type': 'array', 'items': {'type': 'string'}}}, ['title', 'instruction']),
    ('acc_start_task', 'Start the assigned configured worker. Executes local commands.', {'task_id': {'type': 'string'}}, ['task_id']),
    ('acc_stop_task', 'Stop the supervised worker and retain its files.', {'task_id': {'type': 'string'}}, ['task_id']),
    ('acc_assign_task', 'Assign an idle task to an available configured agent.',
     {'task_id': {'type': 'string'}, 'agent': {'type': 'string'}}, ['task_id', 'agent']),
    ('acc_update_instructions', 'Revise instructions for an idle task; preserves prior revisions.',
     {'task_id': {'type': 'string'}, 'instruction': {'type': 'string'}}, ['task_id', 'instruction']),
    ('acc_report', 'Attach an attributed orchestrator report and optional evidence reference.',
     {'task_id': {'type': 'string'}, 'message': {'type': 'string'}, 'reference': {'type': 'string'}}, ['task_id', 'message']),
    ('acc_record_review', 'Record reported acceptance for the current run and requirement revision.',
     {'task_id': {'type': 'string'}, 'message': {'type': 'string'}, 'reference': {'type': 'string'},
      'revision': {'type': 'integer'}, 'run_id': {'type': 'string'}}, ['task_id', 'message', 'reference', 'revision', 'run_id']),
]


CONVERSATION_TOOLS = [
    ('acc_conversation_read', 'Read original conversation messages in sequence. Call after reconnecting; also read acc_state for task outcomes. Paginate using the last message seq.',
     {'after': {'type': 'integer', 'minimum': 0}}, []),
    ('acc_conversation_send', "Save the user's exact words with a stable id. Retry with the same id to avoid duplication. Claim before sending a remote request, then renew to capture it.",
     {'id': {'type': 'string'}, 'text': {'type': 'string'}, 'source': {'type': 'string'}}, ['id', 'text']),
    ('acc_conversation_claim', 'Reserve the next conversation decision for this orchestrator for 120 seconds. Returns shared history, pending requests, tasks, and result contract. Never start another writer during the lease.',
     {'owner': {'type': 'string'}}, ['owner']),
    ('acc_conversation_renew', 'Extend your lease by 120 seconds and refresh the pending message batch. Renew while reasoning; expired owners cannot commit.',
     {'token': {'type': 'string'}}, ['token']),
    ('acc_conversation_complete', 'Atomically save your reply and requested task actions, mark the captured messages handled, and release ownership. Execution uses configured roles. Retrying the identical result is idempotent.',
     {'token': {'type': 'string'}, 'reply': {'type': 'string'},
      'intent': {'type': 'string', 'enum': ['discussion', 'clarification', 'request']},
      'actions': {'type': 'array', 'items': {'type': 'object', 'properties': {
          'type': {'type': 'string', 'enum': ['create', 'revise']}, 'title': {'type': 'string'},
          'instruction': {'type': 'string'}, 'source_ids': {'type': 'array', 'items': {'type': 'string'}},
          'task_id': {'type': 'string'}, 'revision': {'type': 'integer'}},
          'required': ['type', 'instruction', 'source_ids'], 'additionalProperties': False}}},
     ['token', 'reply', 'intent', 'actions']),
    ('acc_conversation_release', 'Release your external lease without consuming pending messages.', {'token': {'type': 'string'}}, ['token']),
    ('acc_conversation_retry', 'Retry retained messages after inspecting a held conversation. Does not bypass interrupted process recovery.', {}, []),
]
TOOLS.extend(CONVERSATION_TOOLS)
TOOLS.extend([
    ('acc_set_project_mode', 'Set the authoritative project-wide assignment mode. Offline blocks new cloud work and lets an already-running supervised step reach a safe boundary.',
     {'mode': {'type': 'string', 'enum': ['online', 'offline']}}, ['mode']),
    ('acc_search_archive', 'Search permanent task history by text, status, agent, or task-number range.',
     {'q': {'type': 'string'}, 'status': {'type': 'string'}, 'agent': {'type': 'string'},
      'number_from': {'type': 'integer'}, 'number_to': {'type': 'integer'}, 'limit': {'type': 'integer'}}, []),
    ('acc_export_archive', 'Export a Markdown task archive and consistent SQLite backup to an existing absolute local directory.',
     {'directory': {'type': 'string'}}, ['directory']),
    ('acc_create_recovery_handoff', 'Create a numbered successor task from known state after an unavailable worker has stopped.',
     {'task_id': {'type': 'string'}, 'agent': {'type': 'string'}, 'reason': {'type': 'string'}}, ['task_id', 'agent', 'reason']),
    ('acc_switch_agent', 'Request a role change after the current step ends. Completed files and reports are retained; does not interrupt the runner.',
     {'task_id': {'type': 'string'}, 'role': {'type': 'string', 'enum': ['implementer', 'reviewer', 'coordinator']}, 'agent': {'type': 'string'}, 'request_id': {'type': 'string'}}, ['task_id', 'role', 'agent', 'request_id']),
    ('acc_schedule_task', 'Set priority (higher first) and tasks that must be accepted before this task runs. Rejects dependency cycles.',
     {'task_id': {'type': 'string'}, 'priority': {'type': 'integer', 'minimum': 0, 'maximum': 100}, 'depends_on': {'type': 'array', 'items': {'type': 'string'}}}, ['task_id']),
    ('acc_github_refresh', 'Request a remote GitHub refresh. Read acc_state for timestamped cached results.', {}, []),
    ('acc_github_configure', 'Configure GitHub remote, source branch, base branch, and polling interval.',
     {'enabled': {'type': 'boolean'}, 'remote': {'type': 'string'}, 'source': {'type': 'string'}, 'base': {'type': 'string'}, 'interval': {'type': 'integer'}}, []),
    ('acc_publish_preview', 'Read the exact publication target and changed paths for accepted, independently reviewed work. Does not publish.',
     {'task_id': {'type': 'string'}}, ['task_id']),
    ('acc_publish_task', 'Publish a reviewed preview under existing user authorization: commit, non-force push, and create or reuse a PR. Never merges.',
     {'task_id': {'type': 'string'}, 'preview_id': {'type': 'string'}, 'request_id': {'type': 'string'}}, ['task_id', 'preview_id', 'request_id']),
])


def dispatch(message, url, token):
    method, request_id = message.get('method'), message.get('id')
    if request_id is None:
        return None
    def result(value):
        return {'jsonrpc': '2.0', 'id': request_id, 'result': value}
    if method == 'initialize':
        supported = ('2024-11-05', '2025-03-26', '2025-06-18')
        requested = message.get('params', {}).get('protocolVersion')
        return result({'protocolVersion': requested if requested in supported else supported[-1],
                       'capabilities': {'tools': {}}, 'serverInfo': {'name': 'acc', 'version': '0.5.0'}})
    if method == 'ping':
        return result({})
    if method == 'tools/list':
        return result({'tools': [{'name': name, 'description': desc,
                                 'inputSchema': {'type': 'object', 'properties': props, 'required': required,
                                                 'additionalProperties': False}}
                                for name, desc, props, required in TOOLS]})
    if method != 'tools/call':
        return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32601, 'message': 'Method not found'}}
    try:
        params = message['params']
        name, args = params['name'], dict(params.get('arguments', {}))
        if name not in {t[0] for t in TOOLS}:
            raise ValueError('Unknown tool')
        if name == 'acc_state':
            path, data = '/api/state', None
        elif name == 'acc_conversation_read':
            after = args.get('after', 0)
            if type(after) is not int or after < 0:
                raise ValueError('Invalid conversation cursor')
            path, data = '/api/conversation?after=' + str(after), None
        elif name.startswith('acc_conversation_'):
            path, data = '/api/conversation/' + name.removeprefix('acc_conversation_'), args
        elif name.startswith('acc_github_'):
            path, data = '/api/github/' + name.removeprefix('acc_github_'), args
        elif name == 'acc_set_project_mode':
            path, data = '/api/project/mode', args
        elif name == 'acc_search_archive':
            from urllib.parse import urlencode
            path, data = '/api/archive?' + urlencode(args), None
        elif name == 'acc_export_archive':
            path, data = '/api/archive/export', args
        elif name == 'acc_create_task':
            path, data = '/api/tasks', args
        else:
            task_id = args.pop('task_id')
            if not isinstance(task_id, str) or not task_id.isalnum():
                raise ValueError('Invalid task ID')
            action = {'acc_start_task': 'start', 'acc_stop_task': 'stop', 'acc_assign_task': 'assign',
                      'acc_update_instructions': 'instructions', 'acc_report': 'report', 'acc_record_review': 'review',
                      'acc_configure_workflow': 'workflow', 'acc_switch_agent': 'switch', 'acc_schedule_task': 'schedule',
                      'acc_publish_preview': 'publish-preview', 'acc_publish_task': 'publish',
                      'acc_create_recovery_handoff': 'recovery-handoff'}[name]
            path, data = f'/api/tasks/{task_id}/{action}', args
        request = urllib.request.Request(url + path, data=None if data is None else json.dumps(data).encode(),
                                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode()
        return result({'content': [{'type': 'text', 'text': body}], 'isError': False})
    except urllib.error.HTTPError as exc:
        return result({'content': [{'type': 'text', 'text': exc.read().decode()}], 'isError': True})
    except Exception as exc:
        return result({'content': [{'type': 'text', 'text': str(exc)}], 'isError': True})


def main():
    parser = argparse.ArgumentParser(description='ACC stdio MCP bridge')
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--token-file', required=True)
    args = parser.parse_args()
    target = urlsplit(args.url)
    if target.scheme != 'http' or target.hostname not in ('127.0.0.1', 'localhost') or target.username:
        parser.error('Bridge connects only to a local HTTP coordinator.')
    token = Path(args.token_file).read_text().strip()
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = dispatch(message, args.url.rstrip('/'), token)
        except (ValueError, TypeError, AttributeError):
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON-RPC message'}}
        if response is not None:
            print(json.dumps(response), flush=True)


if __name__ == '__main__':
    main()
