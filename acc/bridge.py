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
                       'capabilities': {'tools': {}}, 'serverInfo': {'name': 'acc', 'version': '0.1.0'}})
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
        elif name == 'acc_create_task':
            path, data = '/api/tasks', args
        else:
            task_id = args.pop('task_id')
            if not isinstance(task_id, str) or not task_id.isalnum():
                raise ValueError('Invalid task ID')
            action = {'acc_start_task': 'start', 'acc_stop_task': 'stop', 'acc_assign_task': 'assign',
                      'acc_update_instructions': 'instructions', 'acc_report': 'report', 'acc_record_review': 'review'}[name]
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
