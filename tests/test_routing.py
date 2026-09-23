"""Autonomous routing tests: no paid model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from acc.core import Coordinator
from acc.bridge import TOOLS
from acc.routing import TypeSafeEvaluator
from test_acc import eventually
import test_workflow


class FakeTypeSafe:
    def __init__(self, answers):
        self.answers = answers

    def configured(self):
        return True

    def evaluate(self, state, questions):
        assert set(questions) == {'implementer', 'reviewer', 'coordinator'}
        return {'model': 'fixture', 'answers': self.answers,
                'usage': {'input_tokens': 100, 'output_tokens': 20}}


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.worker = self.root / 'worker.py'; self.worker.write_text(test_workflow.WORKER)
        agents = [
            {'id': 'python-cheap', 'name': 'Python cheap',
             'argv': [sys.executable, str(self.worker), '{prompt_file}', 'python-cheap'],
             'routing': {'roles': ['implementer'], 'capabilities': ['code.python'],
                         'quality_tier': 4, 'cost_tier': 1}},
            {'id': 'python-premium', 'name': 'Python premium',
             'argv': [sys.executable, str(self.worker), '{prompt_file}', 'python-premium'],
             'routing': {'roles': ['implementer'], 'capabilities': ['code.python'],
                         'quality_tier': 5, 'cost_tier': 5}},
            {'id': 'reviewer', 'name': 'Reviewer',
             'argv': [sys.executable, str(self.worker), '{prompt_file}', 'reviewer'],
             'routing': {'roles': ['reviewer'], 'capabilities': ['code.python'],
                         'quality_tier': 5, 'cost_tier': 3}},
            {'id': 'coordinator', 'name': 'Coordinator', 'local': True,
             'argv': [sys.executable, str(self.worker), '{prompt_file}', 'coordinator'],
             'routing': {'roles': ['coordinator'], 'quality_tier': 4, 'cost_tier': 1}},
        ]
        self.config = self.root / 'agents.json'
        self.config.write_text(json.dumps({'agents': agents, 'routing': {'enabled': True}}))
        self.c = Coordinator(self.project, self.root / 'state', self.config)

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    @staticmethod
    def task():
        return {'title': 'Build parser', 'instruction': 'Implement and test a Python parser.',
                'task_area': 'code.python', 'required_capabilities': ['code.python'],
                'risk': 'medium'}

    def test_deterministic_policy_routes_without_credential_or_approval(self):
        spec, decision = self.c.router.route(self.task())
        self.assertEqual(spec['implementer'], 'python-cheap')
        self.assertEqual(spec['reviewer'], 'reviewer')
        self.assertEqual(spec['coordinator'], 'coordinator')
        self.assertEqual(decision['source'], 'deterministic_no_typesafe_credential')
        self.assertIn('Automatic assignment', decision['policy'])

    def test_typesafe_probabilities_are_one_signal_and_low_confidence_still_routes(self):
        self.c.router.evaluator = FakeTypeSafe({
            'implementer': {'type': 'choice', 'choice': 'python-premium',
                            'probabilities': {'python-cheap': .45, 'python-premium': .55},
                            'confidence': .1},
            'reviewer': {'type': 'choice', 'choice': 'reviewer',
                         'probabilities': {'reviewer': 1.}, 'confidence': 1.},
            'coordinator': {'type': 'choice', 'choice': 'coordinator',
                            'probabilities': {'coordinator': 1.}, 'confidence': 1.}})
        spec, decision = self.c.router.route(self.task())
        self.assertIn(spec['implementer'], ('python-cheap', 'python-premium'))
        self.assertTrue(decision['low_confidence'])
        self.assertEqual(decision['source'], 'typesafe_jev')
        self.assertEqual(decision['usage']['input_tokens'], 100)

    def test_outcomes_are_persisted_by_area_and_affect_reliability(self):
        task = self.c.build_task({'title': 'Parser', 'instruction': 'Build it',
                                  'agent': 'python-cheap', 'task_area': 'code.python'})
        task['runs'].append({'id': 'run-1', 'agent': 'python-cheap', 'started': 10., 'ended': 15.})
        self.c.router.record_outcome(task, 'implementer', False, switched=True,
                                     cost={'usd': .03}, usage={'input_tokens': 10, 'output_tokens': 5})
        profiles = self.c.router.profiles('code.python')
        item = profiles[('python-cheap', 'implementer')]
        self.assertEqual(item['samples'], 1)
        self.assertLess(item['reliability'], .5)
        self.assertEqual(item['mean_cost_usd'], .03)

    def test_offline_conversation_routes_only_to_declared_local_agents(self):
        self.c.agents['python-cheap']['local'] = True
        self.c.agents['reviewer']['local'] = True
        spec, _ = self.c.router.route(self.task(), {'mode': 'offline'})
        self.assertEqual(spec['implementer'], 'python-cheap')
        self.assertEqual(spec['reviewer'], 'reviewer')
        self.assertEqual(spec['coordinator'], 'coordinator')

    def test_direct_orchestrator_session_rejects_worker_only_adapter(self):
        with self.assertRaises(ValueError):
            self.c.orchestrators.select({'session_id': 'agent:python-cheap'})

    def test_automatic_orchestrator_selection_uses_typed_choice_as_one_signal(self):
        self.c.agents['python-premium']['routing']['roles'].append('coordinator')

        class CoordinatorChoice:
            def configured(inner_self):
                return True

            def evaluate(inner_self, state, questions):
                self.assertEqual(set(questions), {'coordinator'})
                return {'answers': {'coordinator': {
                    'type': 'choice', 'choice': 'python-premium',
                    'probabilities': {'coordinator': .01, 'python-premium': .99},
                    'confidence': .99}}, 'usage': {'input_tokens': 8, 'output_tokens': 2}}

        self.c.router.evaluator = CoordinatorChoice()
        agent, decision = self.c.router.select_orchestrator(
            'Plan a difficult Python system', preferred='coordinator')

        self.assertEqual(agent, 'python-premium')
        self.assertEqual(decision['source'], 'typesafe_jev')
        self.assertEqual(decision['selected'], 'python-premium')


class AutonomousReplacementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        worker = self.root / 'worker.py'
        worker.write_text("""import json,pathlib,sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text())
if sys.argv[-1]=='bad' and p['workflow']['stage']=='implement': sys.exit(7)
if sys.argv[-1]=='bad-review' and p['workflow']['stage']=='review':
 pathlib.Path(p['result_file']).write_text('{}'); sys.exit(0)
""" + test_workflow.WORKER)
        def agent(agent_id, role, quality=3):
            caps = ['code.python'] if role != 'coordinator' else []
            return {'id': agent_id, 'name': agent_id,
                    'argv': [sys.executable, str(worker), '{prompt_file}', agent_id],
                    'routing': {'roles': [role], 'capabilities': caps,
                                'quality_tier': quality, 'cost_tier': 2}}
        config = {'agents': [agent('bad', 'implementer', 5), agent('good', 'implementer', 4),
                             agent('bad-review', 'reviewer', 5), agent('review', 'reviewer', 4),
                             agent('coord', 'coordinator', 4)],
                  'routing': {'enabled': True, 'max_failures': 3}}
        path = self.root / 'agents.json'; path.write_text(json.dumps(config))
        self.c = Coordinator(self.project, self.root / 'state', path)

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def test_failed_worker_is_replaced_and_task_finishes_without_user_approval(self):
        task = self.c.create({'title': 'Autonomous repair', 'instruction': 'normal',
                              'agent': 'bad', 'task_area': 'code.python',
                              'required_capabilities': ['code.python']})
        self.c.workflows.configure(task['id'],
                                   {'implementer': 'bad', 'reviewer': 'review', 'coordinator': 'coord'})
        eventually(lambda: self.c.store.get(task['id']).get('workflow', {}).get('phase') == 'complete',
                   timeout=15)
        result = self.c.store.get(task['id'])
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['runs'][0]['agent'], 'bad')
        self.assertEqual(result['runs'][1]['agent'], 'good')
        self.assertEqual(result['workflow']['route_failures'][0]['agent'], 'bad')

    def test_malformed_reviewer_result_is_replaced_automatically(self):
        task = self.c.create({'title': 'Autonomous review recovery', 'instruction': 'normal',
                              'agent': 'good', 'task_area': 'code.python',
                              'required_capabilities': ['code.python']})
        self.c.workflows.configure(task['id'],
                                   {'implementer': 'good', 'reviewer': 'bad-review',
                                    'coordinator': 'coord'})
        eventually(lambda: self.c.store.get(task['id']).get('workflow', {}).get('phase') == 'complete',
                   timeout=15)
        result = self.c.store.get(task['id'])
        review_runs = [run for run in result['runs'] if run['stage'] == 'review']
        self.assertEqual([run['agent'] for run in review_runs], ['bad-review', 'review'])
        self.assertEqual(result['status'], 'accepted')


class AutonomousDecompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        worker = self.root / 'worker.py'; worker.write_text(test_workflow.WORKER)
        def agent(agent_id, role):
            caps = ['code.python'] if role != 'coordinator' else []
            return {'id': agent_id, 'name': agent_id,
                    'argv': [sys.executable, str(worker), '{prompt_file}', agent_id],
                    'routing': {'roles': [role], 'capabilities': caps,
                                'quality_tier': 4, 'cost_tier': 2}}
        config = {'agents': [agent('builder', 'implementer'), agent('review', 'reviewer'),
                             agent('coord', 'coordinator')],
                  'routing': {'enabled': True},
                  'conversation': {'enabled': False,
                      'workflow': {'implementer': 'builder', 'reviewer': 'review',
                                   'coordinator': 'coord'}}}
        path = self.root / 'agents.json'; path.write_text(json.dumps(config))
        self.c = Coordinator(self.project, self.root / 'state', path)

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def test_project_turn_creates_dependency_graph_and_runs_it_to_completion(self):
        self.c.conversation.append({'id': 'message-1', 'text': 'Build both parts', 'source': 'fixture'})
        claim = self.c.conversation.claim({'owner': 'remote orchestrator'})
        result = self.c.conversation.complete({
            'token': claim['token'], 'reply': 'I will complete both parts.', 'intent': 'request',
            'actions': [
                {'type': 'create', 'action_id': 'foundation', 'title': 'Foundation',
                 'instruction': 'normal', 'source_ids': ['message-1'], 'task_area': 'code.python',
                 'required_capabilities': ['code.python'], 'priority': 10},
                {'type': 'create', 'action_id': 'finish', 'title': 'Finish',
                 'instruction': 'normal', 'source_ids': ['message-1'], 'task_area': 'code.python',
                 'required_capabilities': ['code.python'], 'priority': 100,
                 'depends_on': ['foundation']} ]})
        first_id, second_id = result['task_ids']
        self.assertEqual(self.c.store.get(second_id)['depends_on'], [first_id])
        eventually(lambda: self.c.store.get(first_id)['status'] == 'accepted' and
                            self.c.store.get(second_id)['status'] == 'accepted', timeout=20)
        first, second = self.c.store.get(first_id), self.c.store.get(second_id)
        self.assertLess(first['runs'][0]['started'], second['runs'][0]['started'])
        self.assertEqual(first['routing']['selected']['implementer'], 'builder')
        self.assertEqual(second['routing']['selected']['reviewer'], 'review')


class TypeSafeHTTPContractTests(unittest.TestCase):
    def test_mcp_contract_exposes_task_graph_fields(self):
        tool = next(item for item in TOOLS if item[0] == 'acc_conversation_complete')
        fields = tool[2]['actions']['items']['properties']
        self.assertTrue({'action_id', 'task_area', 'required_capabilities', 'priority',
                         'depends_on'} <= set(fields))

    def test_real_http_adapter_sends_documented_systemone_shape(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                captured['authorization'] = self.headers.get('Authorization')
                captured['body'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                response = json.dumps({'model': 'jev-fixture', 'answers': {
                    'pick': {'type': 'choice', 'choice': 'a',
                             'probabilities': {'a': 1.0}, 'confidence': 1.0}},
                    'usage': {'input_tokens': 2, 'output_tokens': 1}}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response))); self.end_headers()
                self.wfile.write(response)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            evaluator = TypeSafeEvaluator({'endpoint': 'http://127.0.0.1:' + str(server.server_port),
                                           'api_key_env': 'ACC_TYPESAFE_TEST_KEY'})
            with patch.dict(os.environ, {'ACC_TYPESAFE_TEST_KEY': 'fixture-secret'}):
                result = evaluator.evaluate({'task': 'test'}, {
                    'pick': {'type': 'choice', 'instructions': 'Pick one', 'criteria': {'a': None}}})
            self.assertEqual(result['answers']['pick']['choice'], 'a')
            self.assertEqual(captured['authorization'], 'Bearer fixture-secret')
            self.assertEqual(captured['body']['model'], 'jev-latest')
            self.assertEqual(captured['body']['state'], {'task': 'test'})
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
