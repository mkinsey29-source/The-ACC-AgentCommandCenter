import unittest

from acc.connectors import ConnectorRegistry, ConnectorSpec, SUGGESTED_CONNECTORS
from acc.contracts import capability_list, capability_name
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
                         {'kind': 'credential', 'authority': 'node', 'sync': 'never'})
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


if __name__ == '__main__':
    unittest.main()
