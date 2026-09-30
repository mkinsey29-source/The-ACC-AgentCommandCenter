import tempfile
from pathlib import Path
import subprocess
import threading
import time
import unittest

from acc.bridge import dispatch
from acc.connectors import ConnectorRegistry, ConnectorSpec, SUGGESTED_CONNECTORS
from acc.contracts import capability_list, capability_name
from acc.core import Conflict, Coordinator
from acc.server import Server
from acc.domain import normalize_task_fields
from acc.state_authority import policy_for, policy_snapshot, sync_envelope


class ArchitectureSpineTests(unittest.TestCase):
    def test_capability_contract_is_shared_and_deduplicated(self):
        self.assertEqual(capability_name('email.send'), 'email.send')
        self.assertEqual(capability_list(['code.edit', 'code.edit', 'docs.author']),
                         ('code.edit', 'docs.author'))
        with self.assertRaises(ValueError):
            capability_name('Email Send')

    def test_general_task_contract_defaults_preserve_old_callers(self):
        fields = normalize_task_fields({})
        self.assertEqual(fields['task_area'], 'general')
        self.assertEqual(fields['required_capabilities'], [])
        self.assertEqual(fields['inputs'], {})
        self.assertEqual(fields['permissions'], [])
        self.assertEqual(fields['expected_artifacts'], [])
        self.assertEqual(fields['acceptance_requirements'], [])
        self.assertEqual(fields['resource_requirements'], [])
        self.assertEqual(fields['risk'], 'medium')
        self.assertEqual(fields['data_classification'], 'internal')
        self.assertEqual(fields['workspace_scope'], 'project')

    def test_general_task_contract_accepts_non_coding_work(self):
        fields = normalize_task_fields({
            'task_area': 'communications.email',
            'required_capabilities': ['email.read', 'email.send'],
            'permissions': ['email.send'],
            'expected_artifacts': ['artifact.email'],
            'resource_requirements': ['account.gmail'],
            'inputs': {'thread_id': '123'},
            'acceptance_requirements': ['Draft addresses the requested change.',
                                        'Do not send without policy approval.'],
            'risk': 'high',
            'priority': 80,
            'depends_on': ['task-a', 'task-a'],
            'data_classification': 'confidential',
        })
        self.assertEqual(fields['required_capabilities'], ['email.read', 'email.send'])
        self.assertEqual(fields['depends_on'], ['task-a'])
        self.assertEqual(fields['inputs']['thread_id'], '123')
        self.assertEqual(fields['risk'], 'high')

    def test_invalid_task_contract_is_rejected_before_routing(self):
        for payload in (
            {'task_area': 'Not Valid'},
            {'required_capabilities': ['CODE']},
            {'priority': 101},
            {'risk': 'critical'},
            {'permissions': ['email send']},
            {'workspace_scope': 'everything'},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_task_fields(payload)

    def test_state_authority_separates_platform_and_node_truth(self):
        self.assertEqual(policy_for('task')['authority'], 'platform')
        self.assertEqual(policy_for('credential'),
                         {'kind': 'credential', 'authority': 'holder', 'sync': 'never'})
        self.assertEqual(policy_for('local_resource')['authority'], 'node')
        snapshot = policy_snapshot()
        self.assertEqual(snapshot['connected_system_of_record'], 'platform')
        self.assertEqual(snapshot['local_only_system_of_record'], 'local_store')

    def test_sync_envelope_is_revisioned_and_idempotent(self):
        env = sync_envelope('task', 'task-1', 4, {'status': 'paused'}, 'op-1')
        self.assertEqual(env['operation_id'], 'op-1')
        self.assertEqual(env['base_revision'], 4)
        with self.assertRaises(ValueError):
            sync_envelope('credential', 'secret-1', 0, {'value': 'nope'})

    def test_connector_registry_keeps_enabled_health_and_configuration_separate(self):
        registry = ConnectorRegistry([
            ConnectorSpec('gmail', 'Gmail', ('app',), ('email.read', 'email.send'), implemented=True)
        ])
        self.assertEqual(registry.eligible('email.send'), [])
        with self.assertRaises(ValueError):
            registry.update_state('gmail', enabled=True)
        registry.update_state('gmail', configured=True, enabled=True, healthy=False)
        self.assertEqual(registry.eligible('email.send'), [])
        registry.update_state('gmail', healthy=True)
        self.assertEqual(registry.eligible('email.send')[0]['id'], 'gmail')

    def test_suggested_connector_facets_preserve_boundaries(self):
        specs = {item.id: item for item in SUGGESTED_CONNECTORS}
        self.assertIn('knowledge', specs['obsidian'].facets)
        self.assertIn('app', specs['google-drive'].facets)
        self.assertNotIn('capability', specs['google-drive'].facets)
        self.assertEqual(specs['blender'].facets, ('capability',))
        self.assertTrue(specs['blender'].requires_resource_lease)


class SpineReviewRegressionTests(unittest.TestCase):
    """Defects found in the independent review of PR #27."""

    def registry(self):
        return ConnectorRegistry([ConnectorSpec('gmail', 'Gmail', ('app',), ('email.read',))])

    def test_rejected_connector_transition_leaves_no_partial_state(self):
        registry = self.registry()
        before = registry.get('gmail')
        for kwargs in ({'configured': True, 'healthy': True, 'last_error': 'x' * 1001},
                       {'configured': True, 'metadata': ['not', 'an', 'object']},
                       {'configured': True, 'metadata': {'bad': object()}},
                       {'enabled': True, 'healthy': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                registry.update_state('gmail', **kwargs)
            self.assertEqual(registry.get('gmail'), before)

    def test_connector_last_error_survives_unrelated_transition(self):
        registry = self.registry()
        registry.update_state('gmail', configured=True, healthy=False, last_error='token expired')
        registry.update_state('gmail', enabled=True)
        state = registry.get('gmail')
        self.assertEqual(state['last_error'], 'token expired')
        self.assertTrue(state['enabled'])
        self.assertEqual(registry.eligible('email.read'), [])
        registry.update_state('gmail', healthy=True, last_error=None)
        self.assertIsNone(registry.get('gmail')['last_error'])

    def test_suggested_catalog_claims_no_unbuilt_adapter(self):
        self.assertFalse(any(spec.implemented for spec in SUGGESTED_CONNECTORS))

    def test_account_and_lease_state_cannot_be_mutated_offline(self):
        for kind in ('account', 'lease', 'credential', 'local_resource', 'execution_node'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                sync_envelope(kind, 'id-1', 0, {'x': 1}, 'op-1')
        self.assertEqual(policy_for('lease')['authority'], 'platform')

    def test_task_contract_bounds_inputs_and_items(self):
        for payload in ({'inputs': {'value': float('nan')}},
                        {'inputs': {'value': object()}},
                        {'inputs': {'blob': 'x' * 100_001}},
                        {'inputs': ['not', 'object']},
                        {'acceptance_requirements': ['x' * 2001]},
                        {'depends_on': ['x' * 201]},
                        {'expected_artifacts': ['Report']},
                        {'resource_requirements': ['blender scene']},
                        {'priority': True}):
            with self.subTest(payload=str(payload)[:60]), self.assertRaises(ValueError):
                normalize_task_fields(payload)

    def test_non_coding_domains_fit_the_contract(self):
        for area, capabilities, resources in (
                ('communications.email', ['email.read', 'email.draft'], ['account.gmail']),
                ('research.market', ['research.web', 'docs.author'], []),
                ('documents.report', ['docs.author', 'spreadsheet.analyze'], ['account.google-drive']),
                ('business.operations', ['docs.author', 'calendar.schedule'], ['account.google-calendar']),
                ('game.assets', ['blender.edit', 'unity.build'], ['blender.scene', 'unity.project'])):
            with self.subTest(area=area):
                fields = normalize_task_fields({'task_area': area, 'required_capabilities': capabilities,
                                                'resource_requirements': resources})
                self.assertEqual(fields['required_capabilities'], capabilities)
                self.assertEqual(fields['workspace_scope'], 'project')


class SpineCoordinatorCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        self.c = Coordinator(self.project, root / 'state')

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_old_coding_caller_gets_safe_defaults(self):
        task = self.c.create({'title': 'Fix parser', 'instruction': 'Fix and test it.',
                              'task_area': 'code.python', 'required_capabilities': ['code.python']})
        stored = self.c.store.get(task['id'])
        self.assertEqual(stored['task_area'], 'code.python')
        self.assertEqual(stored['inputs'], {})
        self.assertEqual(stored['permissions'], [])
        self.assertEqual(stored['data_classification'], 'internal')
        self.assertEqual(stored['workspace_scope'], 'project')
        self.assertEqual(stored['knowledge_scopes'], [])

    def test_direct_dependencies_must_be_existing_project_tasks(self):
        first = self.c.create({'title': 'First', 'instruction': 'One'})
        with self.assertRaises(ValueError):
            self.c.create({'title': 'Dangling', 'instruction': 'Two', 'depends_on': ['missing-task']})
        self.assertEqual(len(self.c.store.tasks()), 1)
        second = self.c.create({'title': 'Second', 'instruction': 'Two', 'depends_on': [first['id']]})
        self.assertEqual(self.c.controls.blocked(self.c.store.get(second['id'])), [first['id']])

    def test_invalid_generalized_fields_are_rejected_before_persistence(self):
        with self.assertRaises(ValueError):
            self.c.create({'title': 'Mail', 'instruction': 'Draft', 'permissions': ['Send Email']})
        self.assertEqual(self.c.store.tasks(), [])

    def test_mcp_create_task_accepts_generalized_contract(self):
        server = Server(('127.0.0.1', 0), self.c, 'test-token')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = 'http://127.0.0.1:' + str(server.server_port)
            listed = dispatch({'id': 1, 'method': 'tools/list'}, url, 'test-token')
            schema = next(tool for tool in listed['result']['tools']
                          if tool['name'] == 'acc_create_task')['inputSchema']['properties']
            for key in ('workstream_id', 'inputs', 'permissions', 'expected_artifacts',
                        'acceptance_requirements', 'resource_requirements', 'data_classification',
                        'workspace_scope', 'priority', 'depends_on'):
                self.assertIn(key, schema)
            arguments = {'title': 'Vendor reply', 'instruction': 'Draft; do not send.',
                         'task_area': 'communications.email',
                         'required_capabilities': ['email.read', 'email.draft'],
                         'permissions': ['email.read', 'email.draft'],
                         'expected_artifacts': ['artifact.email-draft'],
                         'resource_requirements': ['account.gmail'],
                         'inputs': {'thread_id': 't-1'}, 'data_classification': 'confidential',
                         'acceptance_requirements': ['No message is sent.'], 'risk': 'high'}
            response = dispatch({'id': 2, 'method': 'tools/call', 'params': {
                'name': 'acc_create_task', 'arguments': arguments}}, url, 'test-token')
            self.assertFalse(response['result']['isError'])
            stored = self.c.store.tasks()[0]
            for key, value in arguments.items():
                self.assertEqual(stored[key], value)
            old = dispatch({'id': 3, 'method': 'tools/call', 'params': {
                'name': 'acc_create_task',
                'arguments': {'title': 'Old caller', 'instruction': 'Unchanged'}}}, url, 'test-token')
            self.assertFalse(old['result']['isError'])
            bad = dispatch({'id': 4, 'method': 'tools/call', 'params': {
                'name': 'acc_create_task',
                'arguments': {'title': 'Bad', 'instruction': 'x', 'permissions': ['Send Mail']}}},
                url, 'test-token')
            self.assertTrue(bad['result']['isError'])
            self.assertEqual(len(self.c.store.tasks()), 2)
        finally:
            server.shutdown()
            server.server_close()



class SpineConversationTests(unittest.TestCase):
    """Conversation create/revise must produce the same task contract as direct creation."""

    def setUp(self):
        import test_conversation
        import test_workflow
        self.base = test_conversation.ConversationTests
        test_workflow.WorkflowTests.setUp(self)

    def tearDown(self):
        # Coordinator.close() raises while a background runner's exit is still unconfirmed; that is a
        # known pre-existing fixture race (see ACC-MODULE-CHECKLIST.md), so wait it out here.
        import test_workflow
        for _ in range(20):
            try:
                test_workflow.WorkflowTests.tearDown(self)
                return
            except Conflict:
                time.sleep(.5)
        test_workflow.WorkflowTests.tearDown(self)

    def complete(self, actions):
        self.base.configure(self, enabled=False)
        self.base.append(self)
        claim = self.base.claim(self)
        return self.c.conversation.complete(self.base.result(self, claim, actions=actions))

    def test_conversation_create_matches_direct_contract_and_maps_dependencies(self):
        general = {'task_area': 'research.market', 'workstream_id': 'launch',
                   'required_capabilities': ['research.web'], 'permissions': ['research.web'],
                   'expected_artifacts': ['artifact.report'], 'inputs': {'region': 'US'},
                   'acceptance_requirements': ['Cites sources.'], 'data_classification': 'public'}
        response = self.complete([
            {'type': 'create', 'action_id': 'research', 'title': 'Research', 'instruction': 'Compare.',
             'source_ids': ['message-1'], **general},
            {'type': 'create', 'action_id': 'plan', 'title': 'Plan', 'instruction': 'Write plan.',
             'source_ids': ['message-1'], 'depends_on': ['research']}])
        research, plan = (self.c.store.get(item) for item in response['task_ids'])
        direct = self.c.create({'title': 'Direct', 'instruction': 'Compare.', **general})
        for key in ('task_area', 'workstream_id', 'required_capabilities', 'permissions',
                    'expected_artifacts', 'inputs', 'acceptance_requirements',
                    'data_classification', 'workspace_scope', 'resource_requirements', 'risk', 'priority'):
            self.assertEqual(research[key], direct[key], key)
        self.assertEqual(plan['depends_on'], [research['id']])
        self.assertEqual(plan['inputs'], {})

    def test_conversation_rejects_cycles_duplicates_and_bad_fields_atomically(self):
        for actions in (
                [{'type': 'create', 'action_id': 'a', 'title': 'A', 'instruction': 'a',
                  'source_ids': ['message-1'], 'depends_on': ['b']},
                 {'type': 'create', 'action_id': 'b', 'title': 'B', 'instruction': 'b',
                  'source_ids': ['message-1'], 'depends_on': ['a']}],
                [{'type': 'create', 'action_id': 'a', 'title': 'A', 'instruction': 'a',
                  'source_ids': ['message-1']},
                 {'type': 'create', 'action_id': 'b', 'title': 'B', 'instruction': 'b',
                  'source_ids': ['message-1'], 'depends_on': ['a', 'a']}],
                [{'type': 'create', 'title': 'A', 'instruction': 'a', 'source_ids': ['message-1'],
                  'expected_artifacts': ['Not Valid']}]):
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                self.complete(actions)
            self.assertEqual(self.c.store.tasks(), [])
            self.tearDown()
            self.setUp()

    def test_conversation_revise_preserves_unspecified_generalized_fields(self):
        task = self.c.create({'title': 'Mail', 'instruction': 'Draft.', 'task_area': 'communications.email',
                              'permissions': ['email.read'], 'inputs': {'thread_id': 't-1'},
                              'data_classification': 'confidential', 'priority': 70})
        self.complete([{'type': 'revise', 'task_id': task['id'], 'revision': 1,
                        'instruction': 'Draft a shorter reply.', 'source_ids': ['message-1'],
                        'expected_artifacts': ['artifact.email-draft']}])
        revised = self.c.store.get(task['id'])
        self.assertEqual(revised['revision'], 2)
        self.assertEqual(revised['instruction'], 'Draft a shorter reply.')
        self.assertEqual(revised['permissions'], ['email.read'])
        self.assertEqual(revised['inputs'], {'thread_id': 't-1'})
        self.assertEqual(revised['data_classification'], 'confidential')
        self.assertEqual(revised['priority'], 70)
        self.assertEqual(revised['expected_artifacts'], ['artifact.email-draft'])

    def test_rejected_conversation_revise_leaves_task_unchanged(self):
        task = self.c.create({'title': 'Mail', 'instruction': 'Draft.', 'permissions': ['email.read']})
        with self.assertRaises(ValueError):
            self.complete([{'type': 'revise', 'task_id': task['id'], 'revision': 1,
                            'instruction': 'New', 'source_ids': ['message-1'],
                            'permissions': ['Email Send']}])
        stored = self.c.store.get(task['id'])
        self.assertEqual((stored['revision'], stored['instruction'], stored['permissions']),
                         (1, 'Draft.', ['email.read']))


class CodexReviewFindingTests(unittest.TestCase):
    """The four review-bot findings on PR #27, verified and fixed during independent review."""

    def test_empty_operation_id_is_rejected_not_regenerated(self):
        with self.assertRaises(ValueError):
            sync_envelope('task', 'task-1', 0, {'status': 'paused'}, '')
        self.assertEqual(len(sync_envelope('task', 'task-1', 0, {})['operation_id']), 32)

    def test_unimplemented_connector_is_never_eligible(self):
        registry = ConnectorRegistry(SUGGESTED_CONNECTORS)
        registry.update_state('google-drive', configured=True, healthy=True, enabled=True)
        self.assertEqual(registry.eligible('docs.read'), [])
        built = ConnectorRegistry([ConnectorSpec('drive-v1', 'Drive', ('app',), ('docs.read',),
                                                 implemented=True)])
        built.update_state('drive-v1', configured=True, healthy=True, enabled=True)
        self.assertEqual([item['id'] for item in built.eligible('docs.read')], ['drive-v1'])

    def test_conversation_complete_schema_accepts_generalized_fields(self):
        import acc.bridge as bridge
        tool = next(item for item in bridge.TOOLS if item[0] == 'acc_conversation_complete')
        properties = tool[2]['actions']['items']['properties']
        for key in ('workstream_id', 'inputs', 'permissions', 'expected_artifacts',
                    'acceptance_requirements', 'resource_requirements', 'data_classification',
                    'workspace_scope'):
            self.assertIn(key, properties)


class JobDispatchContractTests(unittest.TestCase):
    def setUp(self):
        import test_job_backed_workflow
        self.base = test_job_backed_workflow.JobBackedWorkflowTests
        self.base.setUp(self)

    def tearDown(self):
        self.base.tearDown(self)

    def test_job_dispatch_carries_contract_and_data_classification(self):
        task = self.c.create({'title': 'Build a tank', 'instruction': 'Reconstruct the tank asset.',
                              'task_area': 'game.assets', 'required_capabilities': ['mesh.generate'],
                              'inputs': {'reference': 'tank.png'}, 'data_classification': 'confidential',
                              'acceptance_requirements': ['Mesh is watertight.'],
                              'resource_requirements': ['blender.scene']})
        self.c.workflows.configure(task['id'], {'implementer': 'asset-builder', 'reviewer': 'reviewer',
                                                'coordinator': 'coordinator'})
        job = self.base.outstanding_job(self, task['id'])
        self.assertEqual(job['data_classification'], 'confidential')
        self.assertEqual(job['workspace_scope'], 'project')
        contract = job['input']['contract']
        self.assertEqual(contract['inputs'], {'reference': 'tank.png'})
        self.assertEqual(contract['acceptance_requirements'], ['Mesh is watertight.'])
        self.assertEqual(contract['resource_requirements'], ['blender.scene'])

    def test_pre_spine_task_keeps_old_dispatch_defaults(self):
        task_id = self.base.start_task(self)
        job = self.base.outstanding_job(self, task_id)
        self.assertEqual((job['data_classification'], job['workspace_scope']), ('internal', 'project'))
        self.assertEqual(job['input']['title'], 'Build a tank')

if __name__ == '__main__':
    unittest.main()
