"""Optional external-client interoperability test: install mcp in a test venv."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import threading
import unittest

from acc.server import Server
import test_workflow as fixtures


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Optional MCP SDK not installed')
class MCPInteropTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_stdio_client_drives_complete_workflow(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        fixture = fixtures.WorkflowTests()
        fixture.setUp()
        server = Server(('127.0.0.1',0), fixture.c, 'sdk-fixture')
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        token = fixture.root/'token'; token.write_text('sdk-fixture')
        try:
            params = StdioServerParameters(command=sys.executable, args=[
                str(Path(__file__).resolve().parents[1]/'acc/bridge.py'),
                '--url','http://127.0.0.1:'+str(server.server_port),'--token-file',str(token)])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertIn('acc_configure_workflow',[t.name for t in tools.tools])
                    created = await session.call_tool('acc_create_task', {'title':'SDK handoff','instruction':'normal'})
                    task = json.loads(created.content[0].text)
                    configured = await session.call_tool('acc_configure_workflow', {
                        'task_id':task['id'],'implementer':'builder','reviewer':'reviewer','coordinator':'hermes-coordinator'})
                    self.assertFalse(getattr(configured, 'is_error', getattr(configured, 'isError', True)))
                    for _ in range(200):
                        state = await session.call_tool('acc_state', {})
                        result = json.loads(state.content[0].text)['tasks'][0]
                        if result['status']=='accepted': break
                        await asyncio.sleep(.05)
                    self.assertEqual(result['status'],'accepted')
        finally:
            server.shutdown(); server.server_close(); thread.join()
            fixture.tearDown()
