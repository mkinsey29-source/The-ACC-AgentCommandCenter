"""Conversation integration tests using scripted adapters, real processes and SQLite."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from acc.bridge import dispatch
from acc.core import Conflict, Coordinator
from acc.server import Server
from test_acc import eventually
import test_workflow

PLANNER = '''
import json,pathlib,sys,time
p=json.loads(pathlib.Path(sys.argv[1]).read_text())
if 'conversation' not in p:
    exec(WORKER)
else:
    c=p['conversation'];text=' '.join(m['text'] for m in c['pending'])
    if 'slow' in text: time.sleep(.3)
    if 'mutate' in text:
        pathlib.Path(p['project'],'unexpected.txt').write_text('bad planner')
        sys.exit(7)
    if 'offline' in text and sys.argv[-1]=='online': sys.exit(9)
    discussion=text.startswith('idea')
    result={'token':c['result_contract']['token'],'reply':'Saved this idea.' if discussion else 'I will build this and have it independently reviewed.',
            'intent':'discussion' if discussion else 'request','actions':[]}
    if not discussion:
        result['actions']=[{'type':'create','title':'Requested change','instruction':'normal',
                            'source_ids':[m['id'] for m in c['pending']]}]
    pathlib.Path(p['result_file']).write_text(json.dumps(result))
    print('Conversation result ready',flush=True)
'''.replace('WORKER', repr(test_workflow.WORKER))


class ConversationTests(unittest.TestCase):
    setUp = test_workflow.WorkflowTests.setUp
    tearDown = test_workflow.WorkflowTests.tearDown

    def configure(self, **changes):
        self.worker.write_text(PLANNER)
        # An online planner and a local planner use the same executable fixture with different flags.
        self.c.agents['hermes-coordinator']['argv'][-1] = 'online'
        settings = {'preferred_agent': 'hermes-coordinator', 'local_agent': 'local-coordinator',
                    'workflow': {'implementer':'builder', 'reviewer':'reviewer', 'coordinator':'hermes-coordinator',
                                 'fallbacks': {'implementer':'local-builder','reviewer':'local-reviewer','coordinator':'local-coordinator'}}}
        settings.update(changes)
        return self.c.conversation.configure(settings)

    def append(self, text='Please build this', mid='message-1'):
        return self.c.conversation.append({'id':mid, 'text':text, 'source':'fixture'})

    def claim(self):
        return self.c.conversation.claim({'owner':'ChatGPT desktop fixture'})

    def result(self, claim, **changes):
        value={'token':claim['token'], 'reply':'I will build it.', 'intent':'request',
               'actions':[{'type':'create','title':'Requested change','instruction':'normal','source_ids':['message-1']}]}
        value.update(changes)
        return value

    def test_original_words_and_idempotent_capture(self):
        text='  My actual words\n$(literal) `example`  '
        first=self.append(text)
        self.assertEqual(first['text'],text)
        self.assertEqual(self.append(text)['seq'],first['seq'])
        with self.assertRaises(Conflict): self.append('Different text')
        self.assertEqual(len(self.c.conversation.messages()),1)

    def test_external_roundtrip_atomic_idempotence_and_review(self):
        self.configure(enabled=False)
        self.append()
        claim=self.claim();result=self.result(claim)
        first=self.c.conversation.complete(result)
        self.assertEqual(self.c.conversation.complete(result),first)
        with self.assertRaises(Conflict): self.c.conversation.complete({**result,'reply':'Different reply'})
        eventually(lambda:self.c.store.get(first['task_ids'][0])['status']=='accepted',timeout=12)
        task=self.c.store.get(first['task_ids'][0])
        self.assertEqual(task['source_ids'],['message-1'])
        self.assertEqual(len(task['runs']),4)
        self.assertEqual(self.c.conversation.state()['pending'],0)
        self.assertEqual(len([t for t in self.c.snapshot()['tasks'] if not t.get('parent_task_id')]),1)

    def test_failed_commit_rolls_back_messages_tasks_and_receipt(self):
        self.configure(enabled=False);self.append();claim=self.claim();result=self.result(claim)
        original=self.c.conversation.event
        def fail(db,kind,details):
            if kind=='conversation_replied': raise RuntimeError('fixture crash at commit boundary')
            return original(db,kind,details)
        with patch.object(self.c.conversation,'event',side_effect=fail):
            with self.assertRaises(RuntimeError): self.c.conversation.complete(result)
        self.assertEqual(self.c.store.tasks(),[])
        self.assertEqual(self.c.conversation.state()['pending'],1)
        response=self.c.conversation.complete(result)
        self.assertEqual(len(response['task_ids']),1)

    def test_discussion_never_launches_work_and_invalid_actions_rollback(self):
        self.configure(enabled=False);self.append('idea: perhaps blue?');claim=self.claim()
        with self.assertRaises(ValueError):
            self.c.conversation.complete(self.result(claim,intent='discussion'))
        self.c.conversation.complete(self.result(claim,intent='discussion',actions=[],reply='Saved your idea.'))
        self.assertEqual(self.c.store.tasks(),[])

    def test_late_external_result_and_old_revision_are_rejected(self):
        self.configure(enabled=False);self.append();claim=self.claim()
        lease=self.c.conversation.meta('conversation_lease');lease['expires']=0
        with self.c.store.connect() as db:self.c.conversation.put(db,'conversation_lease',lease)
        with self.assertRaises(Conflict):self.c.conversation.complete(self.result(claim))
        newer=self.claim()
        task=self.c.create({'title':'Existing','instruction':'Old'})
        action={'type':'revise','task_id':task['id'],'revision':999,'instruction':'New','source_ids':['message-1']}
        with self.assertRaises(Conflict):self.c.conversation.complete(self.result(newer,actions=[action]))
        self.assertEqual(self.c.store.get(task['id'])['revision'],1)
        action['revision']=1
        self.c.conversation.complete(self.result(newer,actions=[action]))
        self.assertEqual(self.c.store.get(task['id'])['revision'],2)

    def test_claim_prevents_background_and_manual_runner_start(self):
        self.configure();claim=self.claim();self.append()
        time.sleep(.6)
        self.assertIsNone(self.c.running_task)
        task=self.c.create({'title':'Explicit command','instruction':'noop','argv':[sys.executable,'-c','print(1)']})
        with self.assertRaises(Conflict):self.c.start(task['id'])
        renewed=self.c.conversation.renew({'token':claim['token']})
        self.assertEqual(renewed['context']['pending'][0]['id'],'message-1')
        self.c.conversation.complete(self.result(claim,intent='discussion',actions=[]))

    def test_real_online_failure_local_fallback_and_remote_resumption(self):
        self.configure();self.append('Please build this offline')
        eventually(lambda:len([t for t in self.c.snapshot()['tasks'] if not t.get('parent_task_id')])==1,timeout=8)
        task_id=next(t['id'] for t in self.c.snapshot()['tasks'] if not t.get('parent_task_id'))
        eventually(lambda:self.c.store.get(task_id)['status']=='accepted',timeout=12)
        task=self.c.store.get(task_id)
        self.assertTrue(all(r['agent'].startswith('local-') for r in task['runs']))
        children=[t for t in self.c.snapshot()['tasks'] if t.get('parent_task_id')==task_id]
        self.assertEqual([t['task_number'] for t in children], list(range(2, 2 + len(children))))
        self.assertEqual({t['task_kind'] for t in children}, {'workflow_step'})
        planner=[t for t in self.c.store.tasks() if t.get('internal')=='conversation'][0]
        self.assertEqual([r['agent'] for r in planner['runs']],['hermes-coordinator','local-coordinator'])
        self.assertEqual(planner['status'],'accepted')
        claim=self.claim()
        self.assertEqual(next(t for t in claim['context']['tasks'] if not t.get('parent_task_id'))['status'],'accepted')
        self.assertEqual(claim['context']['pending'],[])
        self.c.conversation.release({'token':claim['token']})
        self.append('Please make the next change','message-2')
        eventually(lambda:len([t for t in self.c.snapshot()['tasks'] if not t.get('parent_task_id')])==2,timeout=8)
        next_task=[t for t in self.c.snapshot()['tasks'] if not t.get('parent_task_id')][1]['id']
        eventually(lambda:self.c.store.get(next_task)['status']=='accepted',timeout=12)
        self.assertEqual(self.c.store.get(next_task)['runs'][0]['agent'],'builder')

    def test_new_message_waits_for_next_turn(self):
        self.configure();self.append('idea slow: keep this for later')
        eventually(lambda:self.c.running_task is not None)
        self.append('idea: another thought','message-2')
        eventually(lambda:self.c.conversation.state()['pending']==0,timeout=8)
        replies=[m for m in self.c.conversation.messages() if m['role']=='assistant']
        self.assertEqual(len(replies),2)
        self.assertEqual(replies[0]['data']['message_ids'],['message-1'])
        self.assertEqual(self.c.snapshot()['tasks'],[])

    def test_bad_planner_edit_holds_without_fallback(self):
        self.configure();self.append('Please mutate this')
        eventually(lambda:bool(self.c.conversation.state()['held']),timeout=8)
        self.assertIn('changed project files',self.c.conversation.state()['held'])
        self.assertEqual(self.c.conversation.state()['pending'],1)
        planner=[t for t in self.c.store.tasks() if t.get('internal')][0]
        self.assertEqual(len(planner['runs']),1)
        self.assertEqual(self.c.snapshot()['tasks'],[])

    def test_unconfigured_adapter_keeps_messages_across_restart(self):
        self.append('Keep this until I get home')
        self.c.close();self.c=Coordinator(self.project,self.state,self.config)
        self.assertEqual(self.c.conversation.state()['pending'],1)
        self.assertEqual(self.c.conversation.messages()[0]['text'],'Keep this until I get home')
        self.assertEqual(self.c.store.tasks(),[])

    def test_mcp_conversation_over_authenticated_http(self):
        self.configure(enabled=False)
        server=Server(('127.0.0.1',0),self.c,'fixture')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)
        def call(name,args):
            response=dispatch({'id':1,'method':'tools/call','params':{'name':name,'arguments':args}},url,'fixture')['result']
            self.assertFalse(response['isError'],response)
            return json.loads(response['content'][0]['text'])
        try:
            claim=call('acc_conversation_claim',{'owner':'ChatGPT remote fixture'})
            call('acc_conversation_send',{'id':'message-1','text':'idea: keep this exact wording'})
            call('acc_conversation_renew',{'token':claim['token']})
            call('acc_conversation_complete',self.result(claim,intent='discussion',actions=[],reply='Saved your idea.'))
            messages=call('acc_conversation_read',{})['messages']
            self.assertEqual(len(messages),2)
            self.assertEqual(messages[0]['status'],'handled')
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_local_recording_transcription_is_saved_once(self):
        from acc.voice import Voice
        transcriber=self.root/'transcribe.py'
        transcriber.write_text('import pathlib,sys\nassert pathlib.Path(sys.argv[1]).read_bytes()==b"fixture-audio"\npathlib.Path(sys.argv[2]).write_text("idea: spoken request")\n')
        config={'argv':[sys.executable,str(transcriber),'{audio_file}','{text_file}']}
        self.c.voice=Voice(self.c,config)
        payload={'id':'recording-one','mime':'audio/webm','audio':base64.b64encode(b'fixture-audio').decode()}
        first=self.c.voice.save(payload)
        self.assertEqual(self.c.voice.save(payload),first)
        eventually(lambda:self.c.store.get(first['task_id'])['status']=='accepted',timeout=8)
        messages=self.c.conversation.messages()
        self.assertEqual(len(messages),1)
        self.assertEqual(messages[0]['text'],'idea: spoken request')
        self.assertEqual(messages[0]['source'],'local voice')
        with self.assertRaises(Conflict):self.c.voice.save({**payload,'audio':base64.b64encode(b'other').decode()})

    def test_interrupted_internal_run_can_be_inspected_and_retried(self):
        self.configure(enabled=False);self.append()
        task=self.c.create({'title':'Interrupted conversation','instruction':'Read inbox'})
        task.update(internal='conversation',status='launching',pid=None)
        self.c.store.save(task,'fixture',{})
        with self.c.store.connect() as db:
            self.c.conversation.put(db,'conversation_lease',{'token':'old','kind':'local','owner':'local-coordinator','task_id':task['id']})
        self.c.close();self.c=Coordinator(self.project,self.state,self.config)
        self.assertTrue(self.c.recovery_required)
        self.assertEqual(self.c.conversation.state()['background_runs'][0]['status'],'interrupted')
        with self.assertRaises(Conflict):self.c.conversation.retry()
        self.c.recover(task['id'])
        self.assertIsNone(self.c.conversation.meta('conversation_lease'))
        self.c.conversation.retry()
        self.assertEqual(self.c.conversation.state()['pending'],1)

    def test_optional_transcriber_uses_downloaded_model_and_real_output_contract(self):
        model=self.root/'speech-model';model.mkdir()
        for filename in ('model.bin','config.json','tokenizer.json'):(model/filename).write_text('fixture')
        # Verify the adapter's integration contract without claiming speech recognition quality.
        module=self.root/'faster_whisper.py'
        module.write_text("import os\nclass WhisperModel:\n def __init__(self,path,**kwargs):\n  assert os.environ['HF_HUB_OFFLINE']=='1'\n def transcribe(self,audio,**kwargs):\n  return iter([type('Segment',(),{'text':' spoken words '})()]),None\n")
        import os
        output=self.root/'recognized.txt'
        script=Path(__file__).resolve().parents[1]/'acc'/'transcribe.py'
        run=subprocess.run([sys.executable,str(script),'--audio','fixture.wav','--output',str(output),'--model-dir',str(model)],
                           env={**os.environ,'PYTHONPATH':str(self.root)},capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(output.read_text(),'spoken words')
        (model/'tokenizer.json').unlink()
        run=subprocess.run([sys.executable,str(script),'--audio','fixture.wav','--output',str(output),'--model-dir',str(model)],capture_output=True,text=True)
        self.assertNotEqual(run.returncode,0)
        self.assertIn('downloaded',run.stderr)


if __name__=='__main__':unittest.main()
