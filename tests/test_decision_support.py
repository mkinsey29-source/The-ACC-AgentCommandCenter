import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from acc.experimental.decisions import CATEGORIES, FAMILIES, Config, Context, Criterion, DecisionsAdapter, Source, canonical
from acc.experimental.openai_http import ApiTestPermit, OpenAIHTTP, SafeFailure
from acc.experimental.consumers import observe_orchestrator

AT = '2026-10-24T15:00:00Z'


def context(category='orchestrator', **kwargs):
    values = dict(category=category, source_as_of=AT,
        sources=(Source('source-1', 'https://example.test/evidence', 'rev-1', 'a'*64, AT, AT, 'claim'),),
        scope_refs=('assignment-1',), unresolved_ids=('APK9-01', 'APK9-02', 'APK9-03', 'APK4-01', 'APK4-09'),
        user_gates=('no-merge',), deferred_tests=('physical-device',),
        inventory_required=('active', 'inactive', 'carryover'), inventory_checked=('active', 'inactive', 'carryover'))
    values.update(kwargs)
    return Context(**values)


def response(category, choice, *, family='evidence_verification', confidence=.95):
    def answer(name, values, selected):
        return {'type': 'choice', 'name': name, 'choice': selected, 'confidence': confidence,
                'probabilities': [{'value': v, 'probability': 1. if v == selected else 0.} for v in values]}
    answers = [answer(category, CATEGORIES[category], choice)]
    if category == 'skillopt':
        answers.append(answer('skill_family', FAMILIES, family))
    return {'model': 'gpt-6-luna', 'answers': answers,
            'usage': {'input_tokens': 200, 'output_tokens': 0, 'total_tokens': 200,
                      'input_tokens_details': {'cached_tokens': 20, 'cache_write_tokens': 0}}}


class FakeTransport:
    def __init__(self, output=None, error=None, wait=False):
        self.output, self.error, self.wait, self.calls = output, error, wait, []

    async def create(self, payload, timeout):
        self.calls.append(deepcopy(payload))
        if self.wait:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return deepcopy(self.output)


class DecisionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_cases_preserve_all_ids_gates_sources_and_original_result(self):
        cases = json.loads((Path(__file__).parent/'fixtures/decision_support/cases.json').read_text())
        for case in cases:
            with self.subTest(case=case['id']):
                c = context(case['category'])
                c = replace(c, stale=case.get('stale', False),
                    denied_writes=tuple(case.get('denied_writes', ())),
                    scope_refs=() if case.get('missing_scope') else c.scope_refs,
                    inventory_checked=() if case.get('inventory_gap') else c.inventory_checked,
                    sources=(replace(c.sources[0], kind=case.get('kind', 'claim')),))
                frozen = canonical(case)
                before = canonical({'facts':case['facts']})
                baseline = {'category':case['baseline'], 'all_open_ids': list(c.unresolved_ids), 'owner':'Chase'}
                existing = deepcopy(baseline)
                transport = FakeTransport(response(c.category, case['candidate']))
                result = await DecisionsAdapter(Config('shadow'), transport).observe(c, case['facts'], baseline)
                self.assertEqual(result['audit']['recommendation'], case['expected'])
                self.assertEqual(result['effective_result'], existing)
                self.assertEqual(baseline, existing)
                for field in ('unresolved_ids','user_gates','deferred_tests','denied_writes','inventory_required','inventory_checked','source_as_of'):
                    self.assertEqual(result['audit']['context'][field], getattr(c, field))
                self.assertEqual(canonical(case), frozen)
                self.assertEqual(canonical({'facts':case['facts']}), before)
                self.assertFalse(result['audit']['permission'])
                self.assertFalse(result['audit']['verified_completion'])
                self.assertNotIn('facts', result['audit'])
                self.assertEqual(bool(transport.calls), not (case.get('missing_scope') or case.get('inventory_gap')))

    async def test_off_switch_performs_no_serialization_network_or_key_access(self):
        class Poison:
            def __deepcopy__(self, memo): raise AssertionError('unknown-scope facts touched')
        transport = FakeTransport(error=AssertionError('called'))
        result = await DecisionsAdapter(transport=transport).observe(context(), {'unknown':Poison()}, 'old')
        self.assertEqual(result['audit']['status'], 'off')
        self.assertFalse(transport.calls)
        result = await DecisionsAdapter(Config('shadow'), transport).observe(context(scope_refs=()), {'unknown':Poison()}, 'old')
        self.assertEqual(result['audit']['status'], 'missing_scope')

    async def test_timeout_rate_limit_auth_and_errors_fallback_without_exception_content(self):
        for error, wait in [(SafeFailure('rate_limited'),False), (SafeFailure('credential_unavailable'),False),
                            (RuntimeError('SECRET and private message'),False), (None, True)]:
            adapter = DecisionsAdapter(Config('shadow', timeout_seconds=.01), FakeTransport(error=error,wait=wait))
            result = await adapter.observe(context(),{},'working')
            self.assertEqual(result['effective_result'],'working')
            self.assertIsNone(result['audit']['recommendation'])
            self.assertNotIn('SECRET',canonical(result))
            self.assertEqual(result['audit']['status'], 'timeout' if wait else getattr(error,'safe_code','invalid_or_unavailable'))

    async def test_malformed_refusal_duplicate_nonfinite_and_low_confidence(self):
        valid = response('orchestrator','unchanged')
        invalid = [None, {}, {'model':'other','answers':[],'usage':valid['usage']}]
        a = deepcopy(valid); a['answers'][0]['type']='refusal'; invalid.append(a)
        a = deepcopy(valid); a['answers'] *= 2; invalid.append(a)
        for field,value in [('choice', True),('choice','invented'),('confidence',float('nan')),('confidence',True),('confidence',.2),('probabilities',[])]:
            a = deepcopy(valid); a['answers'][0][field]=value; invalid.append(a)
        a = deepcopy(valid); a['usage']['input_tokens']=-1; invalid.append(a)
        a = deepcopy(valid); a['answers'][0]['probabilities'][0]['probability']=.1; invalid.append(a)
        for output in invalid:
            result = await DecisionsAdapter(Config('shadow'),FakeTransport(output)).observe(context(),{},'original')
            self.assertEqual(result['effective_result'],'original')
            self.assertIsNone(result['audit']['recommendation'])

    async def test_verified_outcome_requires_current_criterion_evidence_and_stays_advice(self):
        c = context('skillopt',sources=(Source('s','https://example.test/test','rev-1','b'*64,AT,AT,'verified'),),
                    criteria=(Criterion('original-acceptance','verified',('s',)),), current_revision='rev-1',
                    unresolved_ids=(),user_gates=(),deferred_tests=())
        t = FakeTransport(response('skillopt','verified_outcome'))
        result=await DecisionsAdapter(Config('shadow'),t).observe(c,{},'unverified')
        self.assertEqual(result['audit']['recommendation'],'verified_outcome')
        self.assertFalse(result['audit']['verified_completion'])
        self.assertEqual(result['effective_result'],'unverified')
        for change in [dict(current_revision='rev-2'),dict(criteria=()),dict(stale=True),dict(denied_writes=('denied',)),
                       dict(criteria=(Criterion('missing','verified',('unknown',)),)),dict(user_gates=('device',))]:
            result=await DecisionsAdapter(Config('shadow'),t).observe(replace(c,**change),{},'unverified')
            self.assertIsNone(result['audit']['recommendation'])

    async def test_audit_contains_usage_configuration_hashes_but_not_private_facts(self):
        result=await DecisionsAdapter(Config('shadow'),FakeTransport(response('orchestrator','material_change'))).observe(
            context(), {'minimal_fact':'private-for-provider-only'}, {'old':True})
        record=result['audit']
        self.assertEqual(record['usage']['input_tokens'],200)
        self.assertGreaterEqual(record['latency_ms'],0)
        self.assertEqual(len(record['input_sha256']),64)
        self.assertIsNone(record['measured_cost_usd'])
        self.assertNotIn('private-for-provider-only',canonical(record))

    async def test_orchestrator_explicit_consumer_never_skips_inventory(self):
        t=FakeTransport(response('orchestrator','unchanged'))
        result=await observe_orchestrator(DecisionsAdapter(Config('shadow'),t),context(inventory_checked=()),{},'existing')
        self.assertEqual(result['audit']['status'],'inventory_incomplete')
        self.assertFalse(t.calls)

    def test_invalid_config_is_not_activation(self):
        for args in [dict(mode='on'),dict(mode='enforce'),dict(model='invented'),dict(timeout_seconds=True),dict(confidence_threshold=float('nan'))]:
            with self.assertRaises(ValueError): Config(**args)


class HttpTests(unittest.IsolatedAsyncioTestCase):
    def permit(self): return ApiTestPermit('org-confirmed','proj-confirmed',Decimal('.10'),Decimal('.05'),2)

    async def test_real_transport_never_calls_network_without_credential(self):
        with patch.dict('os.environ',{},clear=True),patch('urllib.request.build_opener') as network:
            with self.assertRaises(SafeFailure): await OpenAIHTTP(self.permit()).create({},1)
            network.assert_not_called()

    def test_budget_reserves_failures_and_limits_requests(self):
        p=self.permit(); p.reserve(); p.reserve()
        with self.assertRaises(SafeFailure): p.reserve()
        with self.assertRaises(ValueError): ApiTestPermit('org','proj',Decimal('0'),Decimal('.05'),2)

    async def test_fixed_origin_disallows_arbitrary_paths(self):
        for path in ('https://evil.test','/v1/responses','/v1/live/sessions/a/../../commands'):
            with self.assertRaises(ValueError): await OpenAIHTTP(self.permit()).post(path,{},1)

class InFlightSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_turning_off_discards_late_recommendation(self):
        entered,release=asyncio.Event(),asyncio.Event()
        class Delayed:
            async def create(self,*args):
                entered.set(); await release.wait(); return response('orchestrator','unchanged')
        adapter=DecisionsAdapter(Config('shadow'),Delayed())
        task=asyncio.create_task(adapter.observe(context(),{},'existing'))
        await entered.wait(); adapter.config=Config(); release.set()
        result=await task
        self.assertIsNone(result['audit']['recommendation'])
        self.assertEqual(result['effective_result'],'existing')
