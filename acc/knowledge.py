"""Project-scoped Obsidian knowledge vault with deterministic lifecycle rules.

The vault remains ordinary Markdown.  ACC owns the task lifecycle and exposes a
small, high-level interface; agents do not need to understand folder layout,
frontmatter mutation, status banners, or embedding-cache details.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

from .core import Conflict


STATUSES = {'hypothesis', 'supported', 'verified', 'disproven', 'superseded', 'obsolete', 'active'}
INACTIVE_STATUSES = {'disproven', 'superseded', 'obsolete'}
NOTE_TYPES = {
    'task-checkout', 'task-checkin', 'handoff', 'summary', 'decision', 'procedure',
    'problem-solution', 'hypothesis', 'correction', 'finding', 'lesson', 'reference',
}
TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]+", re.IGNORECASE)
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
STATUS_BANNER = re.compile(
    r"\n?<!-- ACC:STATUS-BANNER:START -->.*?<!-- ACC:STATUS-BANNER:END -->\n?",
    re.DOTALL,
)


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _slug(value):
    result = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')[:80]
    return result or uuid.uuid4().hex[:12]


def _yaml_value(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _frontmatter(metadata):
    lines = ['---']
    for key, value in metadata.items():
        if isinstance(value, list):
            lines.append(f'{key}:')
            lines.extend('  - ' + _yaml_value(item) for item in value)
        else:
            lines.append(f'{key}: {_yaml_value(value)}')
    lines += ['---', '']
    return '\n'.join(lines)


def _property(text, name, default=None):
    match = FRONTMATTER.match(text)
    if not match:
        return default
    scalar = re.search(rf'(?m)^{re.escape(name)}:\s*(.*?)\s*$', match.group(1))
    if not scalar:
        return default
    value = scalar.group(1)
    if value in ('null', ''):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value.strip('"\'')


def _terms(text):
    return {term.lower() for term in TOKEN.findall(text) if len(term) > 1}


class OllamaEmbedder:
    """Small standard-library adapter for Ollama's local /api/embed endpoint."""

    def __init__(self, settings):
        self.host = str(settings.get('host', 'http://127.0.0.1:11434')).rstrip('/')
        target = urlsplit(self.host)
        if target.scheme != 'http' or target.hostname not in ('127.0.0.1', 'localhost') or target.username:
            raise ValueError('Knowledge embeddings must use a loopback Ollama HTTP server.')
        self.model = str(settings.get('model', 'embeddinggemma')).strip()
        if not self.model or len(self.model) > 200:
            raise ValueError('Knowledge embedding model is invalid.')
        self.timeout = settings.get('timeout_seconds', 30)
        if type(self.timeout) is not int or not 1 <= self.timeout <= 300:
            raise ValueError('Embedding timeout must be 1–300 seconds.')

    def embed(self, texts):
        request = urllib.request.Request(
            self.host + '/api/embed', method='POST',
            data=json.dumps({'model': self.model, 'input': texts}).encode(),
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ValueError('Local Ollama embeddings are unavailable: ' + str(exc)) from exc
        vectors = payload.get('embeddings')
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ValueError('Ollama returned an invalid embedding response.')
        if not all(isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v) for v in vectors):
            raise ValueError('Ollama returned invalid embedding vectors.')
        return vectors


class EmbeddingCache:
    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS note_embeddings (
                vault_id TEXT NOT NULL, path TEXT NOT NULL, model TEXT NOT NULL,
                content_hash TEXT NOT NULL, vector TEXT NOT NULL, updated TEXT NOT NULL,
                PRIMARY KEY(vault_id,path,model))''')

    def vectors(self, vault_id, model, documents, embedder):
        found, missing = {}, []
        with sqlite3.connect(self.path) as db:
            for document in documents:
                row = db.execute('''SELECT content_hash,vector FROM note_embeddings
                                  WHERE vault_id=? AND path=? AND model=?''',
                                 (vault_id, document['path'], model)).fetchone()
                if row and row[0] == document['hash']:
                    found[document['path']] = json.loads(row[1])
                else:
                    missing.append(document)
            if missing:
                fresh = embedder.embed([item['embedding_text'] for item in missing])
                for document, vector in zip(missing, fresh):
                    found[document['path']] = vector
                    db.execute('''INSERT OR REPLACE INTO note_embeddings
                        (vault_id,path,model,content_hash,vector,updated) VALUES (?,?,?,?,?,?)''',
                        (vault_id, document['path'], model, document['hash'],
                         json.dumps(vector), _stamp()))
        return found


class MarkdownVaultAdapter:
    """Safe direct-file adapter; Obsidian may be open or closed."""

    def __init__(self, vault_id, root, create=False):
        self.id = vault_id
        if not isinstance(root, (str, Path)) or not str(root).strip():
            raise ValueError('Knowledge vault path is required.')
        raw_root = Path(root).expanduser()
        if not raw_root.exists() and create:
            raw_root.mkdir(parents=True)
        if raw_root.is_symlink():
            raise ValueError('Knowledge vault must not be a symlink.')
        self.root = raw_root.resolve()
        if not self.root.is_dir():
            raise ValueError('Knowledge vault must be an existing non-symlink directory.')
        for folder in ('_ACC/Checkouts', '_ACC/Checkins', '_ACC/Handoffs', '_ACC/Templates',
                       'Knowledge', 'Decisions', 'Procedures', 'Problems-and-Solutions',
                       'Hypotheses', 'Corrections', 'Evidence'):
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    def resolve(self, relative, must_exist=False):
        if not isinstance(relative, str) or not relative.strip() or '\0' in relative:
            raise ValueError('Vault note path is invalid.')
        candidate = (self.root / relative).resolve()
        if candidate == self.root or not candidate.is_relative_to(self.root) or candidate.suffix.lower() != '.md':
            raise ValueError('Vault note path must be a Markdown file inside the configured vault.')
        if candidate.exists() and candidate.is_symlink():
            raise ValueError('Symlinked vault notes are not writable.')
        if must_exist and not candidate.is_file():
            raise KeyError(relative)
        return candidate

    def write(self, relative, content, replace=False):
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not replace:
            raise Conflict('Knowledge note already exists: ' + relative)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
        return path.relative_to(self.root).as_posix()

    def read(self, relative):
        path = self.resolve(relative, must_exist=True)
        if path.stat().st_size > 2_000_000:
            raise ValueError('Knowledge note exceeds the 2 MB safety limit.')
        return path.read_text(encoding='utf-8')

    def documents(self):
        documents = []
        for path in sorted(self.root.rglob('*.md')):
            relative = path.relative_to(self.root)
            if '.obsidian' in relative.parts or path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding='utf-8')
            route = relative.as_posix()
            documents.append({
                'path': route, 'title': _property(text, 'title', path.stem),
                'status': _property(text, 'status', 'active'),
                'type': _property(text, 'type', 'reference'),
                'text': text, 'hash': hashlib.sha256(text.encode()).hexdigest(),
                'embedding_text': (path.stem + '\n' + text)[:20_000],
            })
        return documents


class KnowledgeVaults:
    """Deep module used by task lifecycle code and by the ACC MCP bridge."""

    def __init__(self, coordinator, settings=None, embedder=None):
        self.c = coordinator
        self.settings = settings or {}
        self.enabled = bool(self.settings.get('enabled', False))
        self.adapter = None
        self.embedder = embedder
        self.cache = None
        self.embedding_error = None
        if not self.enabled:
            return
        vault = self.settings.get('vault')
        if not isinstance(vault, dict):
            raise ValueError('Enabled knowledge requires a vault configuration.')
        vault_id = str(vault.get('id', '')).strip()
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', vault_id):
            raise ValueError('Knowledge vault id must be lowercase letters, numbers, and hyphens.')
        self.adapter = MarkdownVaultAdapter(vault_id, vault.get('path'), bool(vault.get('create')))
        embeddings = self.settings.get('embeddings', {})
        provider = embeddings.get('provider', 'none')
        if self.embedder is None and provider == 'ollama':
            self.embedder = OllamaEmbedder(embeddings)
        elif self.embedder is None and provider != 'none':
            raise ValueError('Knowledge embedding provider must be ollama or none.')
        if self.embedder:
            self.cache = EmbeddingCache(self.c.state / 'knowledge-embeddings.sqlite3')

    def state(self):
        if not self.enabled:
            return {'enabled': False}
        return {'enabled': True, 'vault_id': self.adapter.id,
                'adapter': 'markdown-files',
                'embedding_provider': 'ollama' if isinstance(self.embedder, OllamaEmbedder) else (
                    'configured' if self.embedder else 'none'),
                'embedding_model': getattr(self.embedder, 'model', None),
                'embedding_error': self.embedding_error}

    def _require(self):
        if not self.enabled:
            raise Conflict('No project knowledge vault is configured.')

    def search(self, payload=None):
        self._require()
        payload = payload or {}
        query = str(payload.get('query', '')).strip()
        if not query or len(query) > 20_000:
            raise ValueError('Knowledge search query must contain 1–20,000 characters.')
        limit = payload.get('limit', 12)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Knowledge search limit must be 1–50.')
        include_inactive = bool(payload.get('include_inactive', False))
        documents = [item for item in self.adapter.documents()
                     if (include_inactive or item['status'] not in INACTIVE_STATUSES)
                     and (payload.get('include_checkouts') or item['type'] != 'task-checkout')]
        query_terms = _terms(query)
        for item in documents:
            title_terms = _terms(item['title'] + ' ' + item['path'])
            body_terms = _terms(item['text'])
            item['lexical_score'] = (4 * len(query_terms & title_terms) + len(query_terms & body_terms)) / max(1, len(query_terms))
            item['semantic_score'] = 0.0
        mode = 'lexical'
        warning = None
        if self.embedder and documents:
            try:
                model = getattr(self.embedder, 'model', self.embedder.__class__.__name__)
                vectors = self.cache.vectors(self.adapter.id, model, documents, self.embedder)
                query_vector = self.embedder.embed([query])[0]
                for item in documents:
                    item['semantic_score'] = self._cosine(query_vector, vectors[item['path']])
                mode = 'hybrid'
                self.embedding_error = None
            except (ValueError, OSError, KeyError) as exc:
                warning = str(exc)
                self.embedding_error = warning
                mode = 'lexical_fallback'
        documents.sort(key=lambda item: (
            -(0.65 * item['semantic_score'] + 0.35 * min(item['lexical_score'], 1.0)
              if mode == 'hybrid' else item['lexical_score']),
            item['path']))
        results = []
        for item in documents:
            score = (0.65 * item['semantic_score'] + 0.35 * min(item['lexical_score'], 1.0)
                     if mode == 'hybrid' else item['lexical_score'])
            if score <= 0 and item['lexical_score'] <= 0:
                continue
            results.append({'path': item['path'], 'title': item['title'], 'status': item['status'],
                            'type': item['type'], 'score': round(score, 6),
                            'excerpt': self._excerpt(item['text'], query_terms)})
            if len(results) == limit:
                break
        return {'vault_id': self.adapter.id, 'mode': mode, 'warning': warning,
                'results': results, 'count': len(results)}

    @staticmethod
    def _cosine(left, right):
        if len(left) != len(right):
            raise ValueError('Embedding vector dimensions changed; clear the knowledge index.')
        denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(x * x for x in right))
        return sum(x * y for x, y in zip(left, right)) / denominator if denominator else 0.0

    @staticmethod
    def _excerpt(text, query_terms):
        body = FRONTMATTER.sub('', text, count=1)
        lines = [line.strip() for line in body.splitlines() if line.strip() and not line.startswith('<!--')]
        matching = next((line for line in lines if _terms(line) & query_terms), None)
        return (matching or (lines[0] if lines else ''))[:500]

    def create_note(self, payload):
        self._require()
        title = payload.get('title')
        body = payload.get('body')
        note_type = payload.get('type', 'finding')
        status = payload.get('status', 'hypothesis')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            raise ValueError('Knowledge note title must contain 1–200 characters.')
        if not isinstance(body, str) or not 1 <= len(body.strip()) <= 100_000:
            raise ValueError('Knowledge note body must contain 1–100,000 characters.')
        if note_type not in NOTE_TYPES or status not in STATUSES:
            raise ValueError('Unknown knowledge note type or status.')
        folder = payload.get('folder') or self._folder(note_type)
        if (not isinstance(folder, str) or folder.startswith(('/', '\\'))
                or '..' in Path(folder).parts or '.obsidian' in Path(folder).parts):
            raise ValueError('Knowledge folder must be relative to the vault.')
        note_id = payload.get('id') or ('kn-' + uuid.uuid4().hex)
        created = _stamp()
        metadata = {
            'acc_id': note_id, 'title': title.strip(), 'type': note_type, 'status': status,
            'project': self.c.project.name, 'task_id': payload.get('task_id'),
            'run_id': payload.get('run_id'), 'worker': payload.get('worker', 'operator'),
            'stage': payload.get('stage'), 'created': created, 'updated': created,
            'source_notes': [item if item.startswith('[[') else '[[' + item.removesuffix('.md') + ']]'
                             for item in self._short_list(payload.get('source_notes', []), 'source_notes')],
            'tags': self._short_list(payload.get('tags', []), 'tags'),
        }
        relative = f"{folder.rstrip('/')}/{created[:10]}-{_slug(title)}-{note_id[-8:]}.md"
        content = _frontmatter(metadata) + '# ' + title.strip() + '\n\n' + body.strip() + '\n'
        return {'id': note_id, 'path': self.adapter.write(relative, content),
                'vault_id': self.adapter.id, 'status': status, 'type': note_type}

    @staticmethod
    def _short_list(value, name):
        if not isinstance(value, list) or len(value) > 100 or not all(
                isinstance(item, str) and 1 <= len(item) <= 500 for item in value):
            raise ValueError(name + ' must be an array of at most 100 short strings.')
        return value

    @staticmethod
    def _folder(note_type):
        return {
            'task-checkout': '_ACC/Checkouts', 'task-checkin': '_ACC/Checkins',
            'handoff': '_ACC/Handoffs', 'decision': 'Decisions', 'procedure': 'Procedures',
            'problem-solution': 'Problems-and-Solutions', 'hypothesis': 'Hypotheses',
            'correction': 'Corrections',
        }.get(note_type, 'Knowledge')

    def checkout(self, payload):
        self._require()
        title = str(payload.get('title', '')).strip()
        instruction = str(payload.get('instruction', '')).strip()
        worker = str(payload.get('worker', '')).strip()
        if not title or not instruction or not worker:
            raise ValueError('Knowledge checkout requires title, instruction, and worker.')
        found = self.search({'query': title + '\n' + instruction, 'limit': payload.get('limit', 12),
                             'include_inactive': True})
        sources = [item['path'] for item in found['results']]
        active = [item for item in found['results'] if item['status'] not in INACTIVE_STATUSES]
        warnings = [item for item in found['results'] if item['status'] in INACTIVE_STATUSES]
        body = [
            '## Assignment', '', instruction, '', '## Retrieved knowledge', '',
        ]
        body += [f"- [[{item['path'][:-3]}]] — {item['status']}: {item['excerpt']}" for item in active]
        if not active:
            body.append('- No active matching notes were found.')
        body += ['', '## Historical warnings and corrections', '']
        body += [f"- [[{item['path'][:-3]}]] — {item['status']}: {item['excerpt']}" for item in warnings]
        if not warnings:
            body.append('- No matching inactive conclusions were found.')
        body += [
            '', '## Agent synthesis before work', '',
            '<!-- ACC:CHECKOUT-SYNTHESIS:START -->',
            '- What I learned:',
            '- How I will apply it:',
            '- Conflicts or uncertainty:',
            '- Known mistakes I will avoid:',
            '<!-- ACC:CHECKOUT-SYNTHESIS:END -->', '',
            '## Retrieval details', '',
            f"- Mode: `{found['mode']}`",
            f"- Warning: {found['warning'] or 'None'}",
        ]
        note = self.create_note({
            'title': 'Checkout — ' + title, 'body': '\n'.join(body), 'type': 'task-checkout',
            'status': 'active', 'task_id': payload.get('task_id'), 'run_id': payload.get('run_id'),
            'worker': worker, 'stage': payload.get('stage'), 'source_notes': sources,
            'tags': ['acc/checkout', 'project/' + _slug(self.c.project.name)],
        })
        return {**note, 'absolute_path': str(self.adapter.resolve(note['path'])),
                'sources': found['results'], 'search_mode': found['mode'],
                'instruction': ('Before changing project files, read the retrieved notes and replace the '
                                'checkout synthesis placeholders with what you learned, how it applies, '
                                'conflicts, and mistakes to avoid.')}

    def checkin(self, payload):
        self._require()
        title = str(payload.get('title', '')).strip()
        summary = str(payload.get('summary', '')).strip()
        worker = str(payload.get('worker', '')).strip()
        if not title or not summary or not worker:
            raise ValueError('Knowledge check-in requires title, summary, and worker.')
        sections = [('Summary', [summary])]
        for heading, key in (
                ('New learnings', 'learnings'), ('Problems encountered', 'issues'),
                ('Solutions and workarounds', 'solutions'), ('Loops and abandoned approaches', 'loops'),
                ('Decisions', 'decisions'), ('Corrections to prior knowledge', 'corrections'),
                ('Evidence and checks', 'evidence')):
            values = self._short_list(payload.get(key, []), key)
            sections.append((heading, values or ['None recorded.']))
        body = []
        for heading, values in sections:
            body += ['## ' + heading, '']
            body += [values[0]] if heading == 'Summary' else ['- ' + value for value in values]
            body.append('')
        checkout_path = payload.get('checkout_path')
        if checkout_path:
            self.adapter.resolve(checkout_path, must_exist=True)
            body += ['## Checkout', '', f"- [[{checkout_path[:-3]}]]", '']
        return self.create_note({
            'title': 'Check-in — ' + title, 'body': '\n'.join(body), 'type': 'task-checkin',
            'status': payload.get('status', 'active'), 'task_id': payload.get('task_id'),
            'run_id': payload.get('run_id'), 'worker': worker, 'stage': payload.get('stage'),
            'source_notes': [checkout_path] if checkout_path else [],
            'tags': ['acc/checkin', 'project/' + _slug(self.c.project.name)],
        })

    def capture_result(self, task, result, stage, worker):
        if not self.enabled:
            return None
        knowledge = result.get('knowledge')
        if not isinstance(knowledge, dict):
            raise ValueError('Knowledge-enabled workers must return the required knowledge check-in object.')
        checkout = (task.get('knowledge') or {}).get('last_checkout')
        if checkout:
            checkout_text = self.adapter.read(checkout['path'])
            synthesis = re.search(
                r'<!-- ACC:CHECKOUT-SYNTHESIS:START -->(.*?)<!-- ACC:CHECKOUT-SYNTHESIS:END -->',
                checkout_text, re.DOTALL)
            if synthesis and '- What I learned:\n- How I will apply it:' in synthesis.group(1):
                supplied = knowledge.get('checkout_synthesis')
                required = ('learned', 'application', 'conflicts', 'mistakes_to_avoid')
                if isinstance(supplied, dict) and all(
                        isinstance(supplied.get(key), str) and supplied[key].strip() for key in required):
                    replacement = ('<!-- ACC:CHECKOUT-SYNTHESIS:START -->\n'
                                   f"- What I learned: {supplied['learned'].strip()}\n"
                                   f"- How I will apply it: {supplied['application'].strip()}\n"
                                   f"- Conflicts or uncertainty: {supplied['conflicts'].strip()}\n"
                                   f"- Known mistakes I will avoid: {supplied['mistakes_to_avoid'].strip()}\n"
                                   '<!-- ACC:CHECKOUT-SYNTHESIS:END -->')
                    checkout_text = re.sub(
                        r'<!-- ACC:CHECKOUT-SYNTHESIS:START -->.*?<!-- ACC:CHECKOUT-SYNTHESIS:END -->',
                        replacement, checkout_text, count=1, flags=re.DOTALL)
                    self.adapter.write(checkout['path'], checkout_text, replace=True)
                    synthesis = re.search(
                        r'<!-- ACC:CHECKOUT-SYNTHESIS:START -->(.*?)<!-- ACC:CHECKOUT-SYNTHESIS:END -->',
                        checkout_text, re.DOTALL)
            if not synthesis or '- What I learned:\n- How I will apply it:' in synthesis.group(1):
                raise ValueError('Worker did not complete the required checkout synthesis before check-in.')
        note = self.checkin({
            'title': f"{stage.capitalize()} Task {task.get('task_number')}: {task['title']}",
            'summary': result['summary'], 'task_id': task['id'], 'run_id': result['run_id'],
            'worker': worker, 'stage': stage, 'checkout_path': checkout.get('path') if checkout else None,
            **{key: knowledge.get(key, []) for key in
               ('learnings', 'issues', 'solutions', 'loops', 'decisions', 'corrections', 'evidence')},
        })
        task.setdefault('knowledge', {})['last_checkin'] = note
        return note

    def transition(self, payload):
        self._require()
        relative = payload.get('path')
        status = payload.get('status')
        if status not in STATUSES:
            raise ValueError('Unknown knowledge status.')
        evidence = self._short_list(payload.get('evidence', []), 'evidence')
        corrected_by = payload.get('corrected_by')
        if corrected_by is not None:
            self.adapter.resolve(corrected_by, must_exist=True)
        text = self.adapter.read(relative)
        match = FRONTMATTER.match(text)
        if not match:
            raise ValueError('Only notes with YAML frontmatter can change knowledge status.')
        header = match.group(1)
        if re.search(r'(?m)^status:', header):
            header = re.sub(r'(?m)^status:.*$', 'status: ' + _yaml_value(status), header)
        else:
            header += '\nstatus: ' + _yaml_value(status)
        if re.search(r'(?m)^updated:', header):
            header = re.sub(r'(?m)^updated:.*$', 'updated: ' + _yaml_value(_stamp()), header)
        else:
            header += '\nupdated: ' + _yaml_value(_stamp())
        if evidence:
            header = re.sub(r'(?m)^status_evidence:\n(?:  - [^\n]*\n?)*', '', header).rstrip()
            header += '\nstatus_evidence:\n' + '\n'.join('  - ' + _yaml_value(item) for item in evidence)
        if corrected_by:
            header = re.sub(r'(?m)^corrected_by:\n(?:  - [^\n]*\n?)*', '', header).rstrip()
            header += '\ncorrected_by:\n  - ' + _yaml_value('[[' + corrected_by[:-3] + ']]')
        body = text[match.end():]
        body = STATUS_BANNER.sub('\n', body).lstrip('\n')
        if status in INACTIVE_STATUSES:
            link = f" See [[{corrected_by[:-3]}]]." if corrected_by else ''
            body = ('<!-- ACC:STATUS-BANNER:START -->\n'
                    f'> [!danger] {status.capitalize()} knowledge\n'
                    f'> Do not use this note as active guidance.{link}\n'
                    '<!-- ACC:STATUS-BANNER:END -->\n\n' + body)
        self.adapter.write(relative, '---\n' + header + '\n---\n\n' + body.rstrip() + '\n', replace=True)
        return {'path': relative, 'status': status, 'corrected_by': corrected_by, 'evidence': evidence}

    def rebuttal(self, payload):
        self._require()
        original = payload.get('original_path')
        self.adapter.resolve(original, must_exist=True)
        title = str(payload.get('title', '')).strip()
        explanation = str(payload.get('explanation', '')).strip()
        if not title or not explanation:
            raise ValueError('Rebuttal requires a title and explanation.')
        body = (f"This note corrects [[{original[:-3]}]].\n\n"
                '## Why the prior conclusion failed\n\n' + explanation)
        note = self.create_note({
            'title': title, 'body': body, 'type': 'correction', 'status': 'verified',
            'task_id': payload.get('task_id'), 'run_id': payload.get('run_id'),
            'worker': payload.get('worker', 'operator'), 'source_notes': [original],
            'tags': ['acc/correction'],
        })
        self.transition({'path': original, 'status': payload.get('original_status', 'disproven'),
                         'corrected_by': note['path'], 'evidence': payload.get('evidence', [])})
        return {**note, 'corrects': original}
