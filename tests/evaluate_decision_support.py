"""Offline fixture comparison. Supplied mock labels are NOT model predictions."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import statistics
from test_decision_support import context, response, FakeTransport
from acc.experimental.decisions import Config, DecisionsAdapter


async def evaluate():
    cases=json.loads((Path(__file__).parent/'fixtures/decision_support/cases.json').read_text())
    rows=[]
    for case in cases:
        ctx=context(case['category'])
        ctx=replace(ctx,stale=case.get('stale',False),scope_refs=() if case.get('missing_scope') else ctx.scope_refs,
            inventory_checked=() if case.get('inventory_gap') else ctx.inventory_checked,
            denied_writes=tuple(case.get('denied_writes',())),
            sources=(replace(ctx.sources[0],kind=case.get('kind','claim')),))
        transport=FakeTransport(response(ctx.category,case['candidate']))
        result=await DecisionsAdapter(Config('shadow'),transport).observe(ctx,case['facts'],case['baseline'])
        audit=result['audit']
        rows.append({'fixture':case['id'],'synthetic_baseline':case['baseline'],'supplied_mock_choice':case['candidate'],
                     'accepted_recommendation':audit['recommendation'],'guard_status':audit['status'],
                     'effective_result':result['effective_result'],'latency_ms':audit['latency_ms'],
                     'expected_guard_result':case['expected'],
                     'lost_unresolved_ids':len(set(ctx.unresolved_ids)-set(audit['context']['unresolved_ids'])),
                     'unauthorized_actions':int(audit['permission']),
                     'claim_promotions':int(audit['verified_completion'])})
    output={'test_kind':'mocked offline contract replay; synthetic baseline labels',
        'fixture_count':len(rows),'guard_contract_matches':sum(r['accepted_recommendation']==r['expected_guard_result'] for r in rows),
        'existing_results_preserved':sum(r['effective_result']==r['synthetic_baseline'] for r in rows),
        'lost_unresolved_ids':sum(r['lost_unresolved_ids'] for r in rows),
        'unauthorized_actions':sum(r['unauthorized_actions'] for r in rows),
        'claim_promotions':sum(r['claim_promotions'] for r in rows),
        'mock_false_change_alerts':sum(r['accepted_recommendation']=='material_change' and r['synthetic_baseline']!='material_change' for r in rows),
        'mock_missed_material_changes':sum(r['accepted_recommendation']!='material_change' and r['synthetic_baseline']=='material_change' for r in rows),
        'mock_local_latency_median_ms':statistics.median(r['latency_ms'] for r in rows),
        'real_api_requests':0,'api_test_spend_usd':0,'real_model_accuracy':None,
        'real_false_alert_rate':None,'real_missed_change_rate':None,'real_latency_ms':None,
        'measured_api_cost_usd':None,'existing_live_workflow_comparison':'not run',
        'limitations':'Fake answers exercise guards and schema only. This does not measure model quality, vendor latency, voice playback or live workflow efficiency.',
        'rows':rows}
    print(json.dumps(output,indent=2))


if __name__=='__main__': asyncio.run(evaluate())
