import asyncio
import json
import unittest

from acc.staging import (STAGING_ACCOUNT_ID, STAGING_ORIGIN_DEFAULT, STAGING_PROJECT_ID,
                         StagingApplication)

SECRET='staging-secret-12345678901234567890'

async def request(app, method, path, *, origin=STAGING_ORIGIN_DEFAULT, headers=()):
    sent=[]
    incoming=[{'type':'http.request','body':b'','more_body':False}]
    async def receive(): return incoming.pop(0)
    async def send(message): sent.append(message)
    all_headers=[(b'origin',origin.encode())]+list(headers)
    scope={'type':'http','method':method,'path':path,'query_string':b'','headers':all_headers}
    await app(scope,receive,send)
    start=next(x for x in sent if x['type']=='http.response.start')
    raw=b''.join(x.get('body',b'') for x in sent if x['type']=='http.response.body')
    return start["status"], json.loads(raw) if raw else None

class StagingApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app=StagingApplication(origin=STAGING_ORIGIN_DEFAULT,bootstrap_secret=SECRET)

    async def test_health_contains_no_secret_and_requires_no_session(self):
        status,body=await request(self.app,'GET','/health')
        self.assertEqual(status,200)
        self.assertEqual(body['service'],'acc-m09-staging')
        self.assertNotIn(SECRET,repr(body))

    async def test_session_bootstrap_requires_exact_origin_and_secret(self):
        headers=[(b'x-acc-staging-secret',SECRET.encode())]
        status,body=await request(self.app,'POST','/staging/session',headers=headers)
        self.assertEqual(status,201)
        self.assertTrue(body['access_token'].startswith('accs_'))
        self.assertEqual(body['account_id'],STAGING_ACCOUNT_ID)
        self.assertEqual(body['project_id'],STAGING_PROJECT_ID)
        denied,_=await request(self.app,'POST','/staging/session',origin='https://evil.example',headers=headers)
        self.assertEqual(denied,403)
        denied,_=await request(self.app,'POST','/staging/session',headers=[(b'x-acc-staging-secret',b'wrong')])
        self.assertEqual(denied,401)

    async def test_bootstrapped_session_reads_shared_project(self):
        _,session=await request(self.app,'POST','/staging/session',
                                headers=[(b'x-acc-staging-secret',SECRET.encode())])
        auth=('Bearer '+session['access_token']).encode()
        path=f'/v1/accounts/{STAGING_ACCOUNT_ID}/projects/{STAGING_PROJECT_ID}'
        status,body=await request(self.app,'GET',path,headers=[(b'authorization',auth)])
        self.assertEqual(status,200)
        self.assertEqual(body['project']['revision'],0)
        self.assertEqual(body['workers'][0]['id'],'worker-1')

    async def test_command_and_event_use_same_state(self):
        # Exercise the application services directly after bootstrap; transport command framing
        # is already covered by the M09 command-transport suite.
        _,session=await request(self.app,'POST','/staging/session',
                                headers=[(b'x-acc-staging-secret',SECRET.encode())])
        auth='Bearer '+session['access_token']
        command={'operation_id':'stage-op','kind':'task.create','expected_revision':0,
                 'payload':{'task_id':'stage-task'}}
        applied=self.app.api.command(auth,STAGING_ACCOUNT_ID,STAGING_PROJECT_ID,command)
        self.assertEqual(applied.status,201)
        events=self.app.api.events_after(auth,STAGING_ACCOUNT_ID,STAGING_PROJECT_ID,0)
        self.assertEqual(events.body['events'][0]['kind'],'command.applied')
        state=self.app.api.project_state(auth,STAGING_ACCOUNT_ID,STAGING_PROJECT_ID)
        self.assertEqual(state.body['tasks'][0]['id'],'stage-task')
        self.assertEqual(state.body['project']['revision'],1)

if __name__=='__main__': unittest.main()
