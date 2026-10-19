"""Build 2 runtime boundary tests. Fake executables are not live inference evidence."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("acc_mobile_runtime", Path(__file__).parents[1] / "mobile/runtime/acc_mobile_runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)

FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args=sys.argv[1:]
provider=pathlib.Path(sys.argv[0]).name
if args[:2]==['auth','status']:
    print(json.dumps({'loggedIn':True,'authMethod':'claude.ai'})); sys.exit()
if args[:2]==['login','status']:
    print('Logged in using ChatGPT'); sys.exit()
model=args[args.index('--model')+1] if '--model' in args else ''
text=sys.stdin.read()
if model=='slow': time.sleep(30)
if model=='failure': print('private diagnostic, must never reach client'); sys.exit(2)
if model=='incomplete': print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'partial'}})); sys.exit()
reply='reply: '+text.split('\n',1)[-1]
if any(k in os.environ for k in ['ACC_STAGING_BOOTSTRAP_SECRET','ANTHROPIC_API_KEY','TYPESAFE_API_KEY']):
    sys.exit(7)
if provider=='claude': print(json.dumps({'subtype':'success','result':reply,'is_error':False}))
else:
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':reply}}))
    print(json.dumps({'type':'turn.completed'}))
'''


class MobileRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.executables = {}
        for p in runtime.PROVIDERS:
            file = Path(self.tmp.name) / p
            file.write_text(FAKE)
            file.chmod(0o700)
            self.executables[p] = str(file)
        self.secret = secrets.token_urlsafe(32)
        self.runtime = runtime.Runtime(self.secret, self.executables)
        self.server = runtime.Server(('127.0.0.1', 0), self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.runtime.close()
        self.thread.join(2)
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None, authenticated=True):
        raw = json.dumps(body).encode() if body is not None else b''
        h = self.signed_headers(path, raw, 'POST' if body is not None else 'GET') if authenticated else {}
        if body is not None:
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        req = Request(self.base + path, data=raw if body is not None else None, headers=h)
        try:
            with urlopen(req, timeout=20) as response:
                return response.status, json.load(response)
        except HTTPError as e:
            return e.code, json.load(e)

    def signed_headers(self, path, body=b'', method='GET', nonce=None):
        nonce=nonce or secrets.token_hex(16);stamp=str(int(time.time()))
        signed='\n'.join(('acc-request-v2',self.runtime.boot,nonce,stamp,method,path,hashlib.sha256(body).hexdigest()))
        return {'X-ACC-Nonce':nonce,'X-ACC-Time':stamp,'X-ACC-Boot':self.runtime.boot,'X-ACC-Signature':self.runtime.mac(signed)}

    def payload(self, p='claude', **overrides):
        return dict(id=secrets.token_hex(16), provider=p, model='', messages=[{'role':'user','text':'Hi, RTL שלום and newline\n $(touch do-not-run)'}], **overrides)

    def wait(self, jid):
        for _ in range(200):
            job = self.runtime.snapshot(jid)
            if job['state'] != 'running':
                return job
            time.sleep(0.02)
        self.fail('Job did not finish')

    def test_auth_and_origin_checked_before_dispatch(self):
        self.assertEqual(401, self.request('/v1/chat', self.payload(), authenticated=False)[0])
        self.assertEqual(403, self.request('/v1/chat', self.payload(), headers={'Origin':'https://evil.example'})[0])
        self.assertEqual({}, self.runtime.jobs)

    def test_handshake_proof_and_transport_replay_protection(self):
        nonce=secrets.token_hex(16)
        code,hello=self.request('/v1/handshake',{'nonce':nonce},authenticated=False)
        self.assertEqual(200,code)
        self.assertEqual(self.runtime.mac('acc-server-v2\n'+nonce+'\n'+self.runtime.boot),hello['proof'])
        headers=self.signed_headers('/v1/status')
        self.assertEqual(200,self.request('/v1/status',headers=headers,authenticated=False)[0])
        self.assertEqual(409,self.request('/v1/status',headers=headers,authenticated=False)[0])
        headers=self.signed_headers('/v1/status');headers['X-ACC-Boot']='0'*32
        self.assertEqual(401,self.request('/v1/status',headers=headers,authenticated=False)[0])
        data=self.payload();raw=json.dumps(data).encode();headers=self.signed_headers('/v1/chat',raw,'POST')
        data['messages'][0]['text']='changed after signing'
        self.assertEqual(401,self.request('/v1/chat',data,headers=headers,authenticated=False)[0])
        self.assertEqual({},self.runtime.jobs)

    def test_both_cli_adapters_execute_through_http(self):
        code, status = self.request('/v1/status')
        self.assertEqual(200, code)
        self.assertEqual(['subscription','subscription'], [p['billing'] for p in status['providers']])
        with patch.dict(os.environ, {'ACC_STAGING_BOOTSTRAP_SECRET':'fixture-only','ANTHROPIC_API_KEY':'fixture-only','TYPESAFE_API_KEY':'fixture-only'}):
            for p in runtime.PROVIDERS:
                data = self.payload(p)
                self.assertEqual(202, self.request('/v1/chat', data)[0])
                job = self.wait(data['id'])
                self.assertEqual('completed', job['state'])
                self.assertIn('$(touch do-not-run)', job['reply'])
                self.assertEqual(p, job['provider'])

    def test_replay_deduplicates_and_changed_replay_is_rejected(self):
        data = self.payload()
        self.request('/v1/chat', data)
        self.wait(data['id'])
        self.assertEqual(202, self.request('/v1/chat', data)[0])
        self.assertEqual(1, len(self.runtime.jobs))
        data['messages'][0]['text'] = 'different'
        self.assertEqual(409, self.request('/v1/chat', data)[0])

    def test_unsupported_fields_and_bad_types_are_rejected(self):
        for field,value in [('provider',[]),('model',{}),('messages',[{'role':'system','text':'x'}]),('allow_api','yes'),('argv',['sh','-c','touch x'])]:
            data = self.payload(); data[field] = value
            self.assertEqual(400, self.request('/v1/chat', data)[0], field)
        self.assertEqual({}, self.runtime.jobs)

    def test_api_billing_requires_explicit_consent(self):
        with patch.object(runtime, 'provider_status', return_value={'id':'claude','state':'signed_in','billing':'api'}):
            data = self.payload(); self.runtime.submit(data)
            self.assertEqual('failed', self.wait(data['id'])['state'])
            data = self.payload(allow_api=True); self.runtime.submit(data)
            self.assertEqual('completed', self.wait(data['id'])['state'])

    def test_no_claimed_success_for_missing_login_or_binary(self):
        for state in ['sign_in_needed','not_installed','check_failed']:
            with patch.object(runtime, 'provider_status', return_value={'id':'claude','state':state,'billing':'unknown'}):
                data = self.payload(); self.runtime.submit(data)
                self.assertEqual('failed', self.wait(data['id'])['state'])

    def test_failure_diagnostics_are_not_returned(self):
        data = self.payload(); data['model']='failure'; self.runtime.submit(data)
        job=self.wait(data['id'])
        self.assertEqual('failed',job['state'])
        self.assertNotIn('private diagnostic', json.dumps(job))
        data=self.payload('codex');data['model']='incomplete'; self.runtime.submit(data)
        self.assertEqual('failed',self.wait(data['id'])['state'])

    def test_cancel_and_busy_cannot_spawn_second_job(self):
        data=self.payload();data['model']='slow';self.runtime.submit(data)
        time.sleep(0.15)
        self.assertEqual(409,self.request('/v1/chat',self.payload())[0])
        self.assertEqual(200,self.request('/v1/jobs/'+data['id']+'/cancel',{})[0])
        self.assertEqual('cancelled',self.wait(data['id'])['state'])

    def test_timeout_and_unread_stdin_are_bounded(self):
        started=time.monotonic()
        with self.assertRaises(runtime.BridgeError):
            runtime.run_process([sys.executable,'-c','import time; time.sleep(30)'], 'x'*50_000, timeout=0.2)
        self.assertLess(time.monotonic()-started,4)

    def test_output_limit_and_malformed_result_fail_closed(self):
        with self.assertRaises(runtime.BridgeError):
            runtime.run_process([sys.executable,'-c','print("x"*3000000)'], timeout=4)
        for p in runtime.PROVIDERS:
            for text in ['{}','[]','{"is_error":true,"result":"oops"}']:
                with self.assertRaises(runtime.BridgeError):
                    runtime.parse_reply(p,0,text)

    def test_successful_json_is_not_corrupted_by_stderr_warning(self):
        code,text=runtime.run_process([sys.executable,'-c','import sys; print("private warning",file=sys.stderr); print(\'{"subtype":"success","result":"answer"}\')'])
        self.assertEqual('answer',runtime.parse_reply('claude',code,text))
        self.assertNotIn('private warning',text)

    @unittest.skipUnless(os.name=='posix','POSIX process-group shutdown')
    def test_runtime_close_terminates_a_running_request(self):
        data=self.payload();data['model']='slow';self.runtime.submit(data)
        time.sleep(0.15)
        started=time.monotonic();self.runtime.close()
        self.assertLess(time.monotonic()-started,4)
        self.assertEqual('cancelled',self.runtime.snapshot(data['id'])['state'])
        with self.assertRaises(runtime.BridgeError): self.runtime.submit(self.payload())

    def test_token_file_permissions_and_symlink(self):
        file=Path(self.tmp.name)/'private'/'token'
        token=runtime.token_at(file)
        self.assertEqual(token,runtime.token_at(file))
        if os.name=='posix':
            self.assertEqual(0,file.stat().st_mode & 0o077)
            file.chmod(0o644)
            with self.assertRaises(ValueError): runtime.token_at(file)
            link=Path(self.tmp.name)/'link';link.symlink_to(file)
            with self.assertRaises((ValueError,OSError)): runtime.token_at(link)
            directory=Path(self.tmp.name)/'shared';directory.mkdir(mode=0o777);directory.chmod(0o777)
            with self.assertRaises(ValueError): runtime.token_at(directory/'token')


if __name__=='__main__':
    unittest.main()
