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
    ('acc_state', 'Read tasks, workers, autonomous-routing profiles, local Git state, and current event cursor.', {}, []),
    ('acc_create_task', 'Record an instruction and optional explicit local command. Does not start it.',
     {'title': {'type': 'string'}, 'instruction': {'type': 'string'}, 'agent': {'type': 'string'},
      'argv': {'type': 'array', 'items': {'type': 'string'}}, 'task_area': {'type': 'string'},
      'knowledge_scopes': {'type': 'array', 'items': {'type': 'string'}},
      'required_capabilities': {'type': 'array', 'items': {'type': 'string'}},
      'risk': {'type': 'string', 'enum': ['low', 'medium', 'high']}}, ['title', 'instruction']),
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
     {'after': {'type': 'integer', 'minimum': 0}, 'session_id': {'type': 'string'}}, []),
    ('acc_conversation_send', "Save the user's exact words with a stable id. Retry with the same id to avoid duplication. Claim before sending a remote request, then renew to capture it.",
     {'id': {'type': 'string'}, 'text': {'type': 'string'}, 'source': {'type': 'string'},
      'session_id': {'type': 'string'}}, ['id', 'text']),
    ('acc_conversation_claim', 'Reserve the next conversation decision for this orchestrator for 120 seconds. Returns shared history, pending requests, tasks, and result contract. Never start another writer during the lease.',
     {'owner': {'type': 'string'}, 'session_id': {'type': 'string'}}, ['owner']),
    ('acc_conversation_renew', 'Extend your lease by 120 seconds and refresh the pending message batch. Renew while reasoning; expired owners cannot commit.',
     {'token': {'type': 'string'}}, ['token']),
    ('acc_conversation_complete', 'Atomically save your reply and a dependency-aware task decomposition, route agents automatically, mark captured messages handled, and release ownership. Retrying the identical result is idempotent.',
     {'token': {'type': 'string'}, 'reply': {'type': 'string'},
      'intent': {'type': 'string', 'enum': ['discussion', 'clarification', 'request']},
      'actions': {'type': 'array', 'items': {'type': 'object', 'properties': {
          'type': {'type': 'string', 'enum': ['create', 'revise']}, 'title': {'type': 'string'},
          'instruction': {'type': 'string'}, 'source_ids': {'type': 'array', 'items': {'type': 'string'}},
          'task_id': {'type': 'string'}, 'revision': {'type': 'integer'},
          'action_id': {'type': 'string'}, 'task_area': {'type': 'string'},
          'required_capabilities': {'type': 'array', 'items': {'type': 'string'}},
          'risk': {'type': 'string', 'enum': ['low', 'medium', 'high']},
          'priority': {'type': 'integer', 'minimum': 0, 'maximum': 100},
          'depends_on': {'type': 'array', 'items': {'type': 'string'}}},
          'required': ['type', 'instruction', 'source_ids'], 'additionalProperties': False}}},
     ['token', 'reply', 'intent', 'actions']),
    ('acc_conversation_release', 'Release your external lease without consuming pending messages.', {'token': {'type': 'string'}}, ['token']),
    ('acc_conversation_retry', 'Retry retained messages after inspecting a held conversation. Does not bypass interrupted process recovery.', {}, []),
    ('acc_orchestrator_select', 'Switch the ACC conversation to Automatic, ChatGPT Remote, or a configured direct model session. An external owner is fenced immediately; an active supervised direct turn hands off at its safe boundary.',
     {'session_id': {'type': 'string'}}, ['session_id']),
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
    ('acc_submit_integration_job', 'Durably queue a capability job. Remote jobs wait while offline; a stable idempotency key makes retries safe.',
     {'capability': {'type': 'string'}, 'provider': {'type': 'string'}, 'task_id': {'type': 'string'},
      'input': {'type': 'object'}, 'budget': {'type': 'object'}, 'priority': {'type': 'integer'},
      'data_classification': {'type': 'string', 'enum': ['public', 'internal', 'confidential']},
      'workspace_scope': {'type': 'string', 'enum': ['project', 'isolated_repository']},
      'idempotency_key': {'type': 'string'}}, ['capability', 'input']),
    ('acc_claim_integration_job', 'Claim the highest-priority eligible job with a fenced, expiring worker lease.',
     {'owner': {'type': 'string'}, 'provider': {'type': 'string'},
      'capabilities': {'type': 'array', 'items': {'type': 'string'}},
      'lease_seconds': {'type': 'integer'}}, ['owner']),
    ('acc_renew_integration_job', 'Renew an active fenced integration-job lease.',
     {'job_id': {'type': 'string'}, 'lease_token': {'type': 'string'}, 'fence': {'type': 'integer'},
      'lease_seconds': {'type': 'integer'}}, ['job_id', 'lease_token', 'fence']),
    ('acc_finish_integration_job', 'Finish a leased job and atomically record structured results, costs, and artifacts.',
     {'job_id': {'type': 'string'}, 'lease_token': {'type': 'string'}, 'fence': {'type': 'integer'},
      'status': {'type': 'string', 'enum': ['succeeded', 'failed']}, 'result': {'type': 'object'},
      'cost': {'type': 'object'}, 'error': {'type': 'string'},
      'artifacts': {'type': 'array', 'items': {'type': 'object'}}},
     ['job_id', 'lease_token', 'fence', 'status']),
    ('acc_cancel_integration_job', 'Cancel a queued integration job. Running jobs retain lease ownership.',
     {'job_id': {'type': 'string'}}, ['job_id']),
    ('acc_retry_integration_job', 'Return a failed integration job to the policy-controlled durable queue.',
     {'job_id': {'type': 'string'}}, ['job_id']),
    ('acc_memory_search', 'Search reviewed shared project memory, or explicitly request proposed/superseded entries.',
     {'q': {'type': 'string'}, 'kind': {'type': 'string'}, 'status': {'type': 'string'},
      'task_id': {'type': 'string'}, 'limit': {'type': 'integer'}}, []),
    ('acc_memory_propose', 'Propose a versioned project-memory entry for ACC review. Does not silently alter project truth.',
     {'title': {'type': 'string'}, 'body': {'type': 'string'}, 'kind': {'type': 'string'},
      'key': {'type': 'string'}, 'source': {'type': 'string'}, 'task_id': {'type': 'string'},
      'branch': {'type': 'string'}, 'commit': {'type': 'string'},
      'tags': {'type': 'array', 'items': {'type': 'string'}}, 'request_id': {'type': 'string'}},
     ['title', 'body']),
    ('acc_memory_review', 'Accept or reject a proposed memory version. Acceptance supersedes the prior active version.',
     {'memory_id': {'type': 'string'}, 'status': {'type': 'string', 'enum': ['active', 'rejected']},
      'reviewer': {'type': 'string'}}, ['memory_id', 'status']),
    ('acc_knowledge_state', 'Read the configured project-scoped Obsidian knowledge-vault status.', {}, []),
    ('acc_knowledge_search', 'Search the current project vault. Inactive conclusions are excluded unless explicitly requested.',
     {'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
      'include_inactive': {'type': 'boolean'}, 'include_checkouts': {'type': 'boolean'},
      'include_reviews': {'type': 'boolean'},
      'scopes': {'type': 'array', 'items': {'type': 'string'}}}, ['query']),
    ('acc_knowledge_checkout', 'Start a task knowledge checkout: search the project vault and create the required pre-work synthesis note.',
     {'title': {'type': 'string'}, 'instruction': {'type': 'string'}, 'worker': {'type': 'string'},
      'task_id': {'type': 'string'}, 'run_id': {'type': 'string'}, 'stage': {'type': 'string'},
      'limit': {'type': 'integer'},
      'scopes': {'type': 'array', 'items': {'type': 'string'}}}, ['title', 'instruction', 'worker']),
    ('acc_knowledge_checkin', 'Create a factual task check-in and route issues, solved problems, loops, proposed corrections, and unvalidated claims into a separate pending review note.',
     {'title': {'type': 'string'}, 'summary': {'type': 'string'}, 'worker': {'type': 'string'},
      'task_id': {'type': 'string'}, 'run_id': {'type': 'string'}, 'stage': {'type': 'string'},
      'checkout_path': {'type': 'string'}, 'status': {'type': 'string'},
      'scopes': {'type': 'array', 'items': {'type': 'string'}},
      **{key: {'type': 'array', 'items': {'type': 'string'}} for key in
         ('learnings', 'issues', 'solutions', 'loops', 'decisions', 'corrections',
          'unvalidated', 'evidence')},
      'review_items': {'type': 'array', 'items': {'type': 'object', 'properties': {
          'title': {'type': 'string'},
          'kind': {'type': 'string', 'enum': ['correction', 'solved-issue', 'unresolved-issue',
                                             'unfinished-work', 'failed-loop', 'workaround']},
          'situation': {'type': 'string'}, 'handling': {'type': 'string'},
          'outcome': {'type': 'string'}, 'uncertainty': {'type': 'string'},
          'evidence': {'type': 'array', 'items': {'type': 'string'}},
      }, 'required': ['title', 'kind', 'situation', 'handling', 'outcome', 'uncertainty'],
          'additionalProperties': False}},
      'completed_knowledge': {'type': 'array', 'items': {'type': 'object', 'properties': {
          'title': {'type': 'string'}, 'body': {'type': 'string'},
          'scope': {'type': 'string'}, 'type': {'type': 'string'},
          'status': {'type': 'string', 'enum': ['supported', 'verified']},
          'evidence': {'type': 'array', 'items': {'type': 'string'}},
      }, 'required': ['title', 'body', 'scope', 'evidence'], 'additionalProperties': False}}},
     ['title', 'summary', 'worker']),
    ('acc_knowledge_note', 'Create a linked Obsidian knowledge note in the configured project vault.',
     {'title': {'type': 'string'}, 'body': {'type': 'string'}, 'worker': {'type': 'string'},
      'type': {'type': 'string'}, 'status': {'type': 'string'}, 'folder': {'type': 'string'},
      'task_id': {'type': 'string'}, 'run_id': {'type': 'string'}, 'stage': {'type': 'string'},
      'source_notes': {'type': 'array', 'items': {'type': 'string'}},
      'scopes': {'type': 'array', 'items': {'type': 'string'}},
      'tags': {'type': 'array', 'items': {'type': 'string'}}}, ['title', 'body']),
    ('acc_knowledge_review', 'Create a pending review of any note, including another review. The target gains a backlink; no truth status changes automatically.',
     {'target_path': {'type': 'string'}, 'title': {'type': 'string'},
      'summary': {'type': 'string'}, 'worker': {'type': 'string'},
      'verdict': {'type': 'string',
                  'enum': ['supports', 'challenges', 'mixed', 'needs-evidence']},
      'task_id': {'type': 'string'}, 'run_id': {'type': 'string'},
      'stage': {'type': 'string'},
      'scopes': {'type': 'array', 'items': {'type': 'string'}},
      'findings': {'type': 'array', 'items': {'type': 'string'}},
      'evidence': {'type': 'array', 'items': {'type': 'string'}},
      'source_notes': {'type': 'array', 'items': {'type': 'string'}}},
     ['target_path', 'title', 'summary', 'worker', 'verdict']),
    ('acc_knowledge_transition', 'Update a note status while preserving history and adding an idempotent warning banner when inactive.',
     {'path': {'type': 'string'}, 'status': {'type': 'string'}, 'corrected_by': {'type': 'string'},
      'evidence': {'type': 'array', 'items': {'type': 'string'}}}, ['path', 'status']),
    ('acc_knowledge_rebuttal', 'Create a correction note, link it to the original, and mark the original disproven or obsolete without deleting it.',
     {'original_path': {'type': 'string'}, 'title': {'type': 'string'},
      'explanation': {'type': 'string'}, 'worker': {'type': 'string'},
      'task_id': {'type': 'string'}, 'run_id': {'type': 'string'},
      'original_status': {'type': 'string', 'enum': ['disproven', 'obsolete', 'superseded']},
      'evidence': {'type': 'array', 'items': {'type': 'string'}}},
     ['original_path', 'title', 'explanation']),
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
                       'capabilities': {'tools': {}}, 'serverInfo': {'name': 'acc', 'version': '0.8.0'}})
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
            from urllib.parse import urlencode
            after = args.get('after', 0)
            if type(after) is not int or after < 0:
                raise ValueError('Invalid conversation cursor')
            query = {'after': after}
            if args.get('session_id'):
                query['session_id'] = args['session_id']
            path, data = '/api/conversation?' + urlencode(query), None
        elif name.startswith('acc_conversation_'):
            path, data = '/api/conversation/' + name.removeprefix('acc_conversation_'), args
        elif name == 'acc_orchestrator_select':
            path, data = '/api/orchestrators/select', args
        elif name.startswith('acc_github_'):
            path, data = '/api/github/' + name.removeprefix('acc_github_'), args
        elif name == 'acc_set_project_mode':
            path, data = '/api/project/mode', args
        elif name == 'acc_search_archive':
            from urllib.parse import urlencode
            path, data = '/api/archive?' + urlencode(args), None
        elif name == 'acc_export_archive':
            path, data = '/api/archive/export', args
        elif name == 'acc_submit_integration_job':
            path, data = '/api/integrations/jobs', args
        elif name == 'acc_claim_integration_job':
            path, data = '/api/integrations/claim', args
        elif name in ('acc_renew_integration_job', 'acc_finish_integration_job',
                      'acc_cancel_integration_job', 'acc_retry_integration_job'):
            job_id = args.pop('job_id')
            if not isinstance(job_id, str) or not job_id.isalnum():
                raise ValueError('Invalid integration job ID')
            operation = name.removeprefix('acc_').removesuffix('_integration_job')
            path, data = f'/api/integrations/jobs/{job_id}/{operation}', args
        elif name == 'acc_memory_search':
            from urllib.parse import urlencode
            path, data = '/api/memory?' + urlencode(args), None
        elif name == 'acc_memory_propose':
            path, data = '/api/memory/propose', args
        elif name == 'acc_memory_review':
            memory_id = args.pop('memory_id')
            if not isinstance(memory_id, str) or not memory_id.isalnum():
                raise ValueError('Invalid memory ID')
            path, data = f'/api/memory/{memory_id}/review', args
        elif name == 'acc_knowledge_state':
            path, data = '/api/knowledge', None
        elif name.startswith('acc_knowledge_'):
            path, data = '/api/knowledge/' + name.removeprefix('acc_knowledge_'), args
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
