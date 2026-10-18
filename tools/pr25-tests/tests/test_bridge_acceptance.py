"""Real SDK -> stdio bridge -> loopback HTTP -> SQLite, without paid models."""
import asyncio
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from acc.core import Coordinator
from acc.server import Server


class BridgeAcceptance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        project = self.root / 'project'; project.mkdir()
        subprocess.run(['git', 'init', '-q', str(project)], check=True)
        self.c = Coordinator(project, self.root / 'state')
        self.c.conversation.configure({'enabled': False})

    def tearDown(self):
        self.c.close()
        self.temp.cleanup()

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'Install optional pinned SDK with bootstrap.py --sdk')
    async def test_real_sdk_six_step_discussion_and_privacy(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        credential = secrets.token_urlsafe(32)
        server = Server(('127.0.0.1', 0), self.c, credential)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        token_file = self.root / 'fixture-credential'; token_file.write_text(credential)
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'acc/bridge.py'),
            '--url', 'http://127.0.0.1:' + str(server.server_port), '--token-file', str(token_file)])
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertIn('acc_conversation_complete', [t.name for t in tools.tools])
                    async def call(name, args):
                        result = await session.call_tool(name, args)
                        self.assertFalse(result.isError, 'MCP call failed: ' + name)
                        return json.loads(result.content[0].text)
                    await call('acc_orchestrator_select', {'session_id': 'chatgpt-remote'})
                    claim = await call('acc_conversation_claim', {'owner': 'SDK test client', 'session_id': 'chatgpt-remote'})
                    message = 'idea: isolated SDK test discussion; no work actions'
                    await call('acc_conversation_send', {'id': 'sdk-discussion-1', 'text': message, 'session_id': 'chatgpt-remote'})
                    await call('acc_conversation_renew', {'token': claim['token']})
                    await call('acc_conversation_complete', {'token': claim['token'], 'reply': 'Discussion recorded.', 'intent': 'discussion', 'actions': []})
                    transcript = await call('acc_conversation_read', {'session_id': 'chatgpt-remote'})
                    self.assertEqual(len(transcript['messages']), 2)
                    self.assertEqual(transcript['messages'][0]['status'], 'handled')
            status = self.c.bridge_status.snapshot()
            self.assertTrue(status['tools_discovered'])
            self.assertIsNotNone(status['conversation_verified_at'])
            self.assertEqual(tuple(status['conversation_steps']), self.c.bridge_status.FLOW)
            stored = json.dumps(self.c.conversation.meta('bridge_status'))
            self.assertNotIn(message, stored)
            self.assertNotIn(credential, stored)
            self.assertNotIn(claim['token'], stored)
            self.assertEqual(self.c.store.tasks(), [])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    async def test_new_client_cannot_inherit_previous_client_verification(self):
        status = self.c.bridge_status
        status.observe({'bridge_id': 'first', 'phase': 'initialize', 'client': {'name': 'verified-client', 'version': '1'}})
        status.observe({'bridge_id': 'first', 'phase': 'tools_list'})
        for tool in status.FLOW:
            status.observe({'bridge_id': 'first', 'phase': 'tool_call', 'tool': tool, 'remote_session': True})
        status.observe({'bridge_id': 'second', 'phase': 'initialize', 'client': {'name': 'unverified-client', 'version': '2'}})
        new = status.snapshot()
        self.assertTrue(new['conversation_verified_at'] is None or new.get('verified_client', {}).get('name') == 'verified-client',
                        'A new client inherited another client\'s verified timestamp without verified-client provenance')

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'Install optional pinned SDK with bootstrap.py --sdk')
    async def test_real_sdk_workflow_tracks_parent_by_id(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        worker = self.root / 'worker.py'
        worker.write_text('''import json,pathlib,sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text()); w=p['workflow']
r={k:w[k] for k in ('task_id','run_id','revision','snapshot_id')}
r.update(summary='Local fixture completed',checks=['Local fixture check'])
if w['stage']=='implement':pathlib.Path(p['project'],'answer.txt').write_text('42')
if w['stage']=='review':r.update(verdict='approve',findings=[])
if w['stage']=='coordinate':r['action']='accept' if w['review_result'] else 'request_review'
pathlib.Path(p['result_file']).write_text(json.dumps(r))
''')
        for name in ('fixture-builder', 'fixture-reviewer', 'fixture-coordinator'):
            self.c.agents[name] = {'id': name, 'name': name, 'kind': 'model', 'local': True,
                                  'available': True, 'argv': [sys.executable, str(worker), '{prompt_file}']}
        credential = secrets.token_urlsafe(32)
        server = Server(('127.0.0.1', 0), self.c, credential)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        token_file = self.root / 'fixture-credential'; token_file.write_text(credential)
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'acc/bridge.py'),
            '--url', 'http://127.0.0.1:' + str(server.server_port), '--token-file', str(token_file)])
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    async def call(name, args):
                        result = await session.call_tool(name, args)
                        self.assertFalse(result.isError, 'MCP call failed: ' + name)
                        return json.loads(result.content[0].text)
                    task = await call('acc_create_task', {'title': 'Parent workflow fixture', 'instruction': 'Local fixture only'})
                    await call('acc_configure_workflow', {'task_id': task['id'], 'implementer': 'fixture-builder',
                        'reviewer': 'fixture-reviewer', 'coordinator': 'fixture-coordinator'})
                    for _ in range(200):
                        state = await call('acc_state', {})
                        parent = next(t for t in state['tasks'] if t['id'] == task['id'])
                        if parent['status'] == 'accepted': break
                        await asyncio.sleep(.05)
                    self.assertEqual(parent['status'], 'accepted', parent.get('activity'))
        finally:
            server.shutdown(); server.server_close(); thread.join()
