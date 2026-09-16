"""Saved microphone recordings and a configurable local transcription process."""
import base64
import hashlib
import json
from pathlib import Path
import re
from .core import Conflict


class Voice:
    def __init__(self, coordinator, config):
        self.c, self.config = coordinator, config
        if config:
            argv = config.get('argv')
            if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and '\0' not in a for a in argv):
                raise ValueError('Transcription argv must be a nonempty string array.')
            if not any('{audio_file}' in a for a in argv) or not any('{text_file}' in a for a in argv):
                raise ValueError('Transcription argv needs {audio_file} and {text_file}.')

    def save(self, payload):
        if not self.config:
            raise ValueError('Configure a local transcription command on this host first.')
        mid, mime = payload.get('id'), payload.get('mime', '').split(';')[0]
        if not isinstance(mid, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', mid):
            raise ValueError('Invalid recording id.')
        extensions = {'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'audio/wav': 'wav'}
        if mime not in extensions:
            raise ValueError('Recording must be WebM, Ogg, MP4, or WAV audio.')
        try:
            raw = base64.b64decode(payload.get('audio', ''), validate=True)
        except (ValueError, TypeError):
            raise ValueError('Invalid audio encoding.')
        if not 1 <= len(raw) <= 10 * 1024 * 1024:
            raise ValueError('Recording must be between 1 byte and 10 MiB.')
        digest = hashlib.sha256(raw).hexdigest()
        with self.c.lock:
            task_id = 'voice' + hashlib.sha256(mid.encode()).hexdigest()
            try:
                existing = self.c.store.get(task_id)
            except KeyError:
                existing = None
            if existing:
                if existing.get('audio_hash') != digest:
                    raise Conflict('Recording id already belongs to different audio.')
                return {'saved': True, 'id': mid, 'task_id': task_id}
            folder = self.c.state / 'voice' / mid
            folder.mkdir(parents=True, exist_ok=True)
            audio = folder / ('audio.' + extensions[mime])
            temporary = audio.with_suffix('.tmp'); temporary.write_bytes(raw); temporary.replace(audio)
            output = folder / 'transcript.txt'
            argv = [a.replace('{audio_file}', str(audio)).replace('{text_file}', str(output)) for a in self.config['argv']]
            task = self.c.build_task({'title': 'Transcribe recording', 'instruction': 'Transcribe locally without sending audio to a cloud service.',
                                     'argv': argv, 'timeout_seconds': 300})
            task.update(id=task_id, internal='transcription', recording_id=mid, audio_hash=digest,
                        transcript_file=str(output), audio_file=str(audio))
            self.c.store.save(task, 'recording_saved', {'message': 'Recording saved locally; waiting for transcription.'})
            return {'saved': True, 'id': mid, 'task_id': task_id}

    def finish(self, task, folder, code, stopped):
        if code or stopped or task['timed_out']:
            task.update(status='paused', activity='Recording retained. Inspect transcription setup, then retry.')
        else:
            output = Path(task['transcript_file'])
            if output.is_symlink() or not output.is_file() or output.stat().st_size > 200000:
                raise ValueError('Transcriber must write a UTF-8 transcript file of at most 200 KB.')
            text = output.read_text(encoding='utf-8')
            self.c.conversation.append({'id': 'recording-' + task['recording_id'], 'text': text, 'source': 'local voice'})
            task.update(status='accepted', activity='Transcribed and saved in conversation.')
        self.c.store.save(task, 'transcription_finished', {'message': task['activity']})

    def retry(self, payload):
        with self.c.lock:
            task = self.c.store.get(payload.get('task_id'))
            if task.get('internal') != 'transcription' or task['status'] != 'paused':
                raise Conflict('Only a paused transcription may be retried.')
            Path(task['transcript_file']).unlink(missing_ok=True)
            task.update(status='queued', activity='Ready to retry transcription.')
            self.c.store.save(task, 'transcription_retry', {'message': task['activity']})
            return {'queued': True}
