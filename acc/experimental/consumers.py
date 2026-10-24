"""Explicit observation entrypoints, never imported by schedulers or routers."""
from .decisions import Context


async def observe_orchestrator(adapter, context: Context, minimized_facts, existing_report):
    if context.category != 'orchestrator':
        raise ValueError('orchestrator context required')
    # The caller must still inspect EVERY mandatory inventory item. No skipping
    # based on summaries, scores, or an unchanged recommendation is supported.
    return await adapter.observe(context, minimized_facts, existing_report)
