"""The worker instructions every model-driven adapter sends, regardless of how it talks to the
model (a spawned CLI's query file, a direct HTTP call, or anything else). Kept in one place so the
result contract two different drivers rely on can't quietly drift apart, the way echoing back
task_id/run_id/revision/snapshot_id already once did between the subprocess and job-backed paths."""
import json
import os
import re

_CREDENTIAL_NAME = re.compile(r'KEY|TOKEN|SECRET', re.IGNORECASE)

INSTRUCTIONS = (
    'You are a worker in ACC. Follow the original requirements and applicable project instructions.\n'
    'The JSON below is your task packet. workflow.stage defines your current role.\n'
    'Implementers edit the project and run checks. Reviewers inspect the frozen snapshot and original '
    'requirements independently, using a separate scratch folder for generated test artifacts. '
    'Coordinators interpret the supplied reports and propose exactly one allowed action.\n'
    'Do not start other ACC tasks, use ACC mutation tools, publish, merge, or delegate detached work. '
    'ACC executes your next-step decision. Never change project or snapshot files during review or coordination.\n'
    'When the packet contains knowledge, knowledge checkout is mandatory. Before changing project files, '
    'read the supplied sources and update the checkout note section marked ACC:CHECKOUT-SYNTHESIS with what '
    'you learned, how it applies, conflicts or uncertainty, and known mistakes to avoid. At completion include '
    'the required structured knowledge object. Record real issues, loops, abandoned approaches, solutions, '
    'decisions, corrections, and evidence; use empty arrays rather than inventing entries.\n'
    'If you cannot write the local checkout absolute_path, return knowledge.checkout_synthesis with '
    'nonempty learned, application, conflicts, and mistakes_to_avoid strings so ACC can complete the '
    'checkout note on your behalf before accepting the check-in.\n'
    'When workflow, conversation, or knowledge is present, your FINAL response must be one JSON '
    'object with no markdown, reasoning, or commentary around it. Follow the applicable result_contract. '
    'Copy task_id, run_id, revision, and snapshot_id exactly. Include summary. Report checks honestly; '
    'an unrun check is not a pass. For an unmanaged task without knowledge, complete it and report '
    'the result in plain language.\n\n'
)

CONVERSATION_OVERRIDE = (
    '\nCONVERSATION ROLE OVERRIDE: You are the conversational orchestrator. Do not edit files, '
    'execute code, or call ACC mutation tools. Read conversation.result_contract and return that '
    'JSON shape, not the workflow shape. Treat brainstorming as discussion. Only propose work '
    'explicitly requested by the user. Preserve original messages via source_ids.\n'
)


def build(packet):
    return (INSTRUCTIONS
            + (CONVERSATION_OVERRIDE if packet.get('conversation') else '')
            + json.dumps(packet, indent=2))


def subprocess_env():
    """A copy of this process's environment with anything whose name looks like a credential
    (KEY/TOKEN/SECRET) removed, before it's handed to a spawned worker CLI. These adapters give
    the underlying model shell/tool access; without this, a prompt-injected or simply mistaken
    command (`env`, `printenv`, a curl with a var interpolated in) could read and exfiltrate any
    of ACC's own unrelated secrets, not just the ones the worker actually needs. A driver that
    needs a real provider credential passed through (DeepAstra's --key-file, for example) still
    can -- it's just explicit rather than inherited ambiently."""
    return {name: value for name, value in os.environ.items() if not _CREDENTIAL_NAME.search(name)}


def extract_json_object(text):
    """Best-effort recovery of the required JSON object from a model's raw response.

    Reasoning models (DeepSeek-R1 among them) commonly emit a <think>...</think> block before
    their actual answer even when told not to, and some models wrap JSON in a markdown fence
    despite instructions otherwise. Try the strict parse first; only fall back to stripping
    known wrapper patterns, never to guessing at malformed JSON.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    if '<think>' in text:
        _, _, after = text.partition('</think>')
        text = after.strip()
        try:
            return json.loads(text)
        except ValueError:
            pass
    if text.startswith('```'):
        text = text.strip('`')
        if text.startswith('json'):
            text = text[4:]
        text = text.strip()
        try:
            return json.loads(text)
        except ValueError:
            pass
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError('Response did not contain a recoverable JSON object.')
