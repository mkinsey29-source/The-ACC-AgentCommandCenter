"""Cross-repository mocked contract, requires explicit SKILLOPT_CHECKOUT path."""
import importlib.util
import os
from pathlib import Path
import unittest
from acc.experimental.decisions import DecisionsAdapter,Config
from test_decision_support import context,FakeTransport,response


class SkillOptBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_companion_uses_one_shared_adapter_without_replacing_outcomes(self):
        root=os.environ.get('SKILLOPT_CHECKOUT')
        if not root: self.skipTest('set SKILLOPT_CHECKOUT for cross-repository contract')
        spec=importlib.util.spec_from_file_location('decision_shadow_companion',Path(root)/'skillopt_sleep/decision_shadow.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        transport=FakeTransport(response('skillopt','reported_claim'))
        old={'status':'unverified','pending_ids':['APK9-01']}
        result=await module.observe_attempt(DecisionsAdapter(Config('shadow'),transport),context('skillopt'),{'claim':'done'},old)
        self.assertEqual(result['effective_result'],old)
        self.assertEqual(result['audit']['recommendation'],'reported_claim')
        self.assertEqual(result['audit']['skill_family'],'evidence_verification')
        self.assertEqual(len(transport.calls),1)
