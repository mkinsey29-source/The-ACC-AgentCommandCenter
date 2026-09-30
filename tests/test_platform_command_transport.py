import asyncio
import json
import unittest

from acc.auth import (AccountState, AuthService, EntitlementSnapshot, InMemoryAuthRepository,
                      Membership, UserState, VerifiedIdentity)
from acc.platform import InMemoryCommandRepository, PlatformApi
from acc.platform.transport import HostedTransport, OriginPolicy

ORIGIN='https://acc.example'

class Verifier:
    id='openai'
    def verify(self, assertion):
        return VerifiedIdentity('openai', assertion['subject'])

class Reads: pass
class Events: pass

async def http(app, body, authorization):
    sent=[]
    raw=json.dumps(body).encode()
    incoming=[{'type':'http.request','body':raw,'more_body':False}]
    async def receive(): return incoming.pop(0)
    async def send(message): sent.append(message)
    scope={
        'type':'http','method':'POST',
        'path':'/v1/accounts/acct-1/projects/project-1/commands',
        'query_string':b'',
        'headers':[(b'origin',ORIGIN.encode()),(b'authorization',authorization.encode())],
    }
    await app(scope,receive,send)
    start=next(x for x in sent if x['type']=='http.response.start')
    data=b''.join(x.get('body',b'') for x in sent if x['type']=='http.response.body')
    return start["status"], json.loads(data)

class CommandTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        repo=InMemoryAuthRepository()
        repo.put_user(UserState('user-1'))
        ident=VerifiedIdentity('openai','subject-1')
        repo.bind_identity(ident,'user-1')
        repo.put_account(AccountState('acct-1'))
        repo.put_membership(Membership('acct-1','user-1',permissions=('task.write',)))
        repo.put_entitlements(EntitlementSnapshot('acct-1',features=('acc.web',)))
        auth=AuthService(repo,identity_verifiers=(Verifier(),))
        token=auth.exchange_identity('openai',{'subject':'subject-1'},'acct-1')
        self.authorization='Bearer '+token
        commands=InMemoryCommandRepository()
        commands.put_project('acct-1','project-1')
        self.app=HostedTransport(PlatformApi(auth,Reads(),Events(),commands),
                                 origins=OriginPolicy(frozenset((ORIGIN,))))

    async def test_post_command_route(self):
        body={'operation_id':'op-1','kind':'task.create','expected_revision':0,
              'payload':{'task_id':'task-1'}}
        status,result=await http(self.app,body,self.authorization)
        self.assertEqual(status,201)
        self.assertEqual(result['status'],'applied')

    async def test_retry_over_http_is_replayed_not_reapplied(self):
        body={'operation_id':'op-1','kind':'task.create','expected_revision':0,
              'payload':{'task_id':'task-1'}}
        self.assertEqual((await http(self.app,body,self.authorization))[0],201)
        status,result=await http(self.app,body,self.authorization)
        self.assertEqual(status,200)
        self.assertTrue(result['replayed'])

    async def test_invalid_json_is_400(self):
        sent=[]
        incoming=[{'type':'http.request','body':b'{bad','more_body':False}]
        async def receive(): return incoming.pop(0)
        async def send(message): sent.append(message)
        scope={'type':'http','method':'POST','path':'/v1/accounts/acct-1/projects/project-1/commands',
               'query_string':b'','headers':[(b'origin',ORIGIN.encode()),
               (b'authorization',self.authorization.encode())]}
        await self.app(scope,receive,send)
        start=next(x for x in sent if x['type']=='http.response.start')
        self.assertEqual(start['status'],400)

if __name__=='__main__': unittest.main()
