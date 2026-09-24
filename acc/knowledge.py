"""Project-scoped Obsidian knowledge vault with deterministic lifecycle rules.

The vault remains ordinary Markdown.  ACC owns the task lifecycle and exposes a
small, high-level interface; agents do not need to understand folder layout,
frontmatter mutation, status banners, or embedding-cache details.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

from .core import Conflict


STATUSES = {
    'hypothesis', 'supported', 'verified', 'disproven', 'superseded', 'obsolete',
    'active', 'pending-review',
}
INACTIVE_STATUSES = {'disproven', 'superseded', 'obsolete'}
NON_RECOMMENDED_STATUSES = INACTIVE_STATUSES | {'pending-review'}
REVIEW_VERDICTS = {'unreviewed', 'supports', 'challenges', 'mixed', 'needs-evidence'}
REVIEW_KINDS = {
    'correction', 'solved-issue', 'unresolved-issue', 'unfinished-work',
    'failed-loop', 'workaround',
}
NOTE_TYPES = {
    'task-checkout', 'task-checkin', 'handoff', 'summary', 'decision', 'procedure',
    'problem-solution', 'hypothesis', 'correction', 'finding', 'lesson', 'reference', 'review',
}
TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]+", re.IGNORECASE)
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
STATUS_BANNER = re.compile(
    r"\n?<!-- ACC:STATUS-BANNER:START -->.*?<!-- ACC:STATUS-BANNER:END -->\n?",
    re.DOTALL,
)
REVIEW_LINKS = re.compile(
    r'\n?<!-- ACC:REVIEWS:START -->.*?<!-- ACC:REVIEWS:END -->\n?', re.DOTALL,
)
REVIEW_ACKS = re.compile(
    r'<!-- ACC:REVIEW-ACKS:START -->(.*?)<!-- ACC:REVIEW-ACKS:END -->', re.DOTALL,
)
REVIEW_AGREEMENTS = re.compile(
    r'\n?<!-- ACC:REVIEW-AGREEMENTS:START -->.*?<!-- ACC:REVIEW-AGREEMENTS:END -->\n?',
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


def _list_property(text, name):
    match = FRONTMATTER.match(text)
    if not match:
        return []
    values = re.search(rf'(?m)^{re.escape(name)}:\n((?:  - .*\n?)*)', match.group(1))
    if not values:
        return []
    result = []
    for line in values.group(1).splitlines():
        raw = line.removeprefix('  - ').strip()
        try:
            result.append(json.loads(raw))
        except (TypeError, ValueError):
            result.append(raw.strip('"\''))
    return result


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
        self.lock = threading.RLock()
        self._transaction = threading.local()
        if not self.root.is_dir():
            raise ValueError('Knowledge vault must be an existing non-symlink directory.')
        for folder in ('_ACC/Checkouts', '_ACC/Checkins', '_ACC/Handoffs', '_ACC/Templates',
                       'Knowledge', 'Decisions', 'Procedures', 'Problems-and-Solutions',
                       'Hypotheses', 'Corrections', 'Evidence', 'Reviews'):
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
        with self.lock:
            path = self.resolve(relative)
            route = path.relative_to(self.root).as_posix()
            pending = getattr(self._transaction, 'pending', None)
            exists = route in pending if pending is not None else False
            if (exists or path.exists()) and not replace:
                raise Conflict('Knowledge note already exists: ' + relative)
            if pending is not None:
                pending[route] = content
                return route
            self._write_now(path, content)
            return route

    @staticmethod
    def _write_now(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)

    @contextmanager
    def transaction(self):
        """Stage vault writes and publish them as one rollback-capable batch."""
        with self.lock:
            if getattr(self._transaction, 'pending', None) is not None:
                raise RuntimeError('Nested vault transactions are not supported.')
            self._transaction.pending = {}
            try:
                yield
                pending = self._transaction.pending
                originals = {}
                for route in pending:
                    path = self.resolve(route)
                    originals[route] = path.read_text(encoding='utf-8') if path.is_file() else None
                try:
                    for route, content in pending.items():
                        self._write_now(self.resolve(route), content)
                except Exception:
                    for route, content in originals.items():
                        path = self.resolve(route)
                        if content is None:
                            if path.exists():
                                path.unlink()
                        else:
                            self._write_now(path, content)
                    raise
            finally:
                self._transaction.pending = None

    def read(self, relative):
        with self.lock:
            path = self.resolve(relative)
            route = path.relative_to(self.root).as_posix()
            pending = getattr(self._transaction, 'pending', None)
            if pending is not None and route in pending:
                content = pending[route]
                if len(content.encode('utf-8')) > 2_000_000:
                    raise ValueError('Knowledge note exceeds the 2 MB safety limit.')
                return content
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
                'scopes': _list_property(text, 'scopes'),
                'review_target': _property(text, 'review_target'),
                'review_thread': _property(text, 'review_thread'),
                'review_verdict': _property(text, 'review_verdict'),
                'review_kind': _property(text, 'review_kind'),
                'created': _property(text, 'created', ''),
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
        self.lock = threading.RLock()
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
        scopes = self._scopes(payload.get('scopes', []))
        documents = [item for item in self.adapter.documents()
                     if (include_inactive or item['status'] not in NON_RECOMMENDED_STATUSES)
                     and (payload.get('include_checkouts') or item['type'] != 'task-checkout')
                     and (payload.get('include_reviews') or item['type'] != 'review')
                     and self._in_scopes(item, scopes)]
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
                'results': results, 'count': len(results), 'scopes': scopes}

    def _scopes(self, value):
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > 20:
            raise ValueError('Knowledge scopes must be an array of at most 20 vault folders.')
        scopes = []
        for scope in value:
            if not isinstance(scope, str) or not scope.strip():
                raise ValueError('Knowledge scopes must contain nonempty folder names.')
            path = Path(scope.strip())
            if path.is_absolute() or '..' in path.parts or '.obsidian' in path.parts:
                raise ValueError('Knowledge scopes must be safe vault-relative folders.')
            normalized = path.as_posix().strip('/')
            if normalized in ('', '.'):
                raise ValueError('Knowledge scopes must name a folder, not the vault root.')
            if normalized not in scopes:
                scopes.append(normalized)
        return scopes

    @staticmethod
    def _in_scopes(document, scopes, include_metadata=False):
        if not scopes:
            return True
        route = document['path']
        return any(route.startswith(scope + '/')
                   or include_metadata and scope in document.get('scopes', []) for scope in scopes)

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
            'scopes': self._scopes(payload.get('scopes', [])),
            'review_target': payload.get('review_target'),
            'review_thread': payload.get('review_thread'),
            'review_verdict': payload.get('review_verdict'),
            'review_kind': payload.get('review_kind'),
        }
        relative = f"{folder.rstrip('/')}/{created[:10]}-{_slug(title)}-{note_id[-8:]}.md"
        content = _frontmatter(metadata) + '# ' + title.strip() + '\n\n' + body.strip() + '\n'
        return {'id': note_id, 'path': self.adapter.write(relative, content),
                'vault_id': self.adapter.id, 'status': status, 'type': note_type}

    @staticmethod
    def _short_list(value, name):
        if not isinstance(value, list) or len(value) > 100 or not all(
                isinstance(item, str) and item.strip() and len(item) <= 500 for item in value):
            raise ValueError(name + ' must be an array of at most 100 short strings.')
        return [item.strip() for item in value]

    @staticmethod
    def _folder(note_type):
        return {
            'task-checkout': '_ACC/Checkouts', 'task-checkin': '_ACC/Checkins',
            'handoff': '_ACC/Handoffs', 'decision': 'Decisions', 'procedure': 'Procedures',
            'problem-solution': 'Problems-and-Solutions', 'hypothesis': 'Hypotheses',
            'correction': 'Corrections', 'review': 'Reviews',
        }.get(note_type, 'Knowledge')

    def checkout(self, payload):
        self._require()
        title = str(payload.get('title', '')).strip()
        instruction = str(payload.get('instruction', '')).strip()
        worker = str(payload.get('worker', '')).strip()
        if not title or not instruction or not worker:
            raise ValueError('Knowledge checkout requires title, instruction, and worker.')
        scopes = self._scopes(payload.get('scopes', []))
        found = self.search({'query': title + '\n' + instruction, 'limit': payload.get('limit', 12),
                             'include_inactive': True, 'scopes': scopes})
        reviews = self._pending_reviews(scopes)
        # Review queue links live in the checkout body. Keep source_notes for retrieved domain
        # knowledge so a large review queue is not constrained by generic frontmatter list limits.
        sources = [item['path'] for item in found['results']]
        active = [item for item in found['results'] if item['status'] not in NON_RECOMMENDED_STATUSES]
        warnings = [item for item in found['results'] if item['status'] in NON_RECOMMENDED_STATUSES]
        body = [
            '## Assignment', '', instruction, '', '## Retrieved knowledge', '',
        ]
        body += [f"- [[{item['path'][:-3]}]] — {item['status']}: {item['excerpt']}" for item in active]
        if not active:
            body.append('- No active matching notes were found.')
        body += ['', '## Pending reviews, warnings, and corrections', '']
        body += [f"- [[{item['path'][:-3]}]] — {item['status']}: {item['excerpt']}" for item in warnings]
        if not warnings:
            body.append('- No matching inactive conclusions were found.')
        body += ['', '## Latest review queue', '']
        if reviews:
            body += [f"- [[{item['path'][:-3]}]] — {item['review_verdict'] or 'unreviewed'}: "
                     f"{item['excerpt']}" for item in reviews]
        else:
            body.append('- No pending review threads matched this task scope.')
        body += ['', '## Review acknowledgements before work', '',
                 '<!-- ACC:REVIEW-ACKS:START -->']
        if reviews:
            body += [f"- [ ] [[{item['path'][:-3]}]] — record `agree`, or state the conflict "
                     'and link the follow-up review' for item in reviews]
        else:
            body.append('- No pending reviews.')
        body += ['<!-- ACC:REVIEW-ACKS:END -->']
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
            'tags': ['acc/checkout', 'project/' + _slug(self.c.project.name)], 'scopes': scopes,
        })
        return {**note, 'absolute_path': str(self.adapter.resolve(note['path'])),
                'sources': found['results'], 'reviews': reviews, 'scopes': scopes,
                'worker': worker, 'task_id': payload.get('task_id'),
                'run_id': payload.get('run_id'), 'stage': payload.get('stage'),
                'search_mode': found['mode'],
                'instruction': ('Before changing project files, read the retrieved notes and replace the '
                                'checkout synthesis placeholders with what you learned, how it applies, '
                                'conflicts, and mistakes to avoid. Review every latest pending review and '
                                'complete its acknowledgement before work.')}

    def _pending_reviews(self, scopes):
        all_reviews = [item for item in self.adapter.documents()
                       if item['type'] == 'review'
                       and self._in_scopes(item, scopes, include_metadata=True)]
        targeted = {item['review_target'] for item in all_reviews if item.get('review_target')}
        latest = [item for item in all_reviews if item['status'] == 'pending-review'
                  and item['path'] not in targeted]
        latest.sort(key=lambda item: (item.get('created', ''), item['path']), reverse=True)
        return [{'path': item['path'], 'title': item['title'], 'status': item['status'],
                 'review_target': item.get('review_target'),
                 'review_thread': item.get('review_thread'),
                 'review_verdict': item.get('review_verdict'),
                 'excerpt': self._excerpt(item['text'], set())} for item in latest]

    def _validated_review_acknowledgements(self, checkout, knowledge):
        expected = [item['path'] for item in checkout.get('reviews', [])]
        if not expected:
            return expected, {}
        supplied = knowledge.get('review_acknowledgements')
        if supplied is None:
            raise ValueError('Worker did not return acknowledgements for every latest review.')
        if not isinstance(supplied, list) or len(supplied) != len(expected):
            raise ValueError('review_acknowledgements must cover every latest pending review exactly once.')
        by_path = {}
        for item in supplied:
            if not isinstance(item, dict) or item.get('disposition') not in ('agree', 'conflict'):
                raise ValueError('Each review acknowledgement needs a path and agree/conflict disposition.')
            path = item.get('path')
            note = item.get('note')
            if (path in by_path or path not in expected or not isinstance(note, str)
                    or not note.strip() or len(note) > 500 or '\n' in note
                    or 'ACC:REVIEW-' in note):
                raise ValueError('Review acknowledgement path or note is invalid.')
            by_path[path] = item
        if set(by_path) != set(expected):
            raise ValueError('Review acknowledgements do not match the latest review queue.')
        for path, item in by_path.items():
            follow_up = item.get('review_path')
            if follow_up:
                self.adapter.resolve(follow_up, must_exist=True)
                if _property(self.adapter.read(follow_up), 'review_target') != path:
                    raise ValueError('Follow-up review does not target the acknowledged review.')
        return expected, by_path

    def _complete_review_acknowledgements(self, checkout, knowledge, checkout_text):
        expected, by_path = self._validated_review_acknowledgements(checkout, knowledge)
        if not expected:
            return checkout_text
        lines = []
        for path in expected:
            item = by_path[path]
            if item['disposition'] == 'agree':
                self._append_review_agreement(
                    path, checkout['path'], checkout.get('worker', 'worker'), item['note'].strip())
                lines.append(f"- [x] [[{path[:-3]}]] — agree: {item['note'].strip()}")
                continue
            follow_up = item.get('review_path')
            if not follow_up:
                created = self.review({
                    'target_path': path, 'title': 'Checkout conflict — ' + Path(path).stem,
                    'summary': item['note'].strip(), 'worker': checkout.get('worker', 'worker'),
                    'verdict': 'challenges', 'findings': [item['note'].strip()], 'evidence': [],
                    'task_id': checkout.get('task_id'), 'run_id': checkout.get('run_id'),
                    'stage': checkout.get('stage'), 'scopes': checkout.get('scopes', []),
                })
                follow_up = created['path']
            lines.append(f"- [x] [[{path[:-3]}]] — conflict: {item['note'].strip()} "
                         f"See [[{follow_up[:-3]}]].")
        replacement = ('<!-- ACC:REVIEW-ACKS:START -->\n' + '\n'.join(lines) +
                       '\n<!-- ACC:REVIEW-ACKS:END -->')
        return REVIEW_ACKS.sub(replacement, checkout_text, count=1)

    def _append_review_agreement(self, review_path, checkout_path, worker, note):
        with self.lock:
            text = self.adapter.read(review_path)
            current = []
            match = REVIEW_AGREEMENTS.search(text)
            if match:
                current = [line for line in match.group(0).splitlines() if line.startswith('- ')]
                text = REVIEW_AGREEMENTS.sub('\n', text, count=1)
            entry = f"- {worker} via [[{checkout_path[:-3]}]]: {note}"
            if entry not in current:
                current.append(entry)
            block = ('<!-- ACC:REVIEW-AGREEMENTS:START -->\n## Agreements\n\n' +
                     '\n'.join(current) + '\n<!-- ACC:REVIEW-AGREEMENTS:END -->\n\n')
            header = FRONTMATTER.match(text)
            if header:
                text = text[:header.end()] + block + text[header.end():].lstrip('\n')
            else:
                text = block + text.lstrip('\n')
            self.adapter.write(review_path, text.rstrip() + '\n', replace=True)

    def _prepare_checkin(self, payload):
        title = str(payload.get('title', '')).strip()
        summary = str(payload.get('summary', '')).strip()
        worker = str(payload.get('worker', '')).strip()
        if not title or not summary or not worker:
            raise ValueError('Knowledge check-in requires title, summary, and worker.')
        scopes = self._scopes(payload.get('scopes', []))
        learnings = self._short_list(payload.get('learnings', []), 'learnings')
        decisions = self._short_list(payload.get('decisions', []), 'decisions')
        evidence = self._short_list(payload.get('evidence', []), 'evidence')
        if (learnings or decisions) and not evidence:
            raise ValueError('Evidence-backed learnings and validated decisions require evidence.')
        review_values = {
            key: self._short_list(payload.get(key, []), key)
            for key in ('issues', 'solutions', 'loops', 'corrections', 'unvalidated')
        }
        structured_reviews = self._review_items(payload.get('review_items', []))
        completed_items = self._completed_items(payload.get('completed_knowledge', []), scopes)
        checkout_path = payload.get('checkout_path')
        if checkout_path:
            self.adapter.resolve(checkout_path, must_exist=True)
        return {'title': title, 'summary': summary, 'worker': worker, 'scopes': scopes,
                'learnings': learnings, 'decisions': decisions, 'evidence': evidence,
                'review_values': review_values, 'structured_reviews': structured_reviews,
                'completed_items': completed_items, 'checkout_path': checkout_path}

    def checkin(self, payload):
        """Publish a complete public/MCP check-in atomically."""
        with self.lock, self.adapter.transaction():
            return self._checkin(payload)

    def _checkin(self, payload):
        self._require()
        prepared = self._prepare_checkin(payload)
        title, summary, worker = (prepared[key] for key in ('title', 'summary', 'worker'))
        scopes = prepared['scopes']
        learnings, decisions, evidence = (
            prepared[key] for key in ('learnings', 'decisions', 'evidence'))
        review_values = prepared['review_values']
        structured_reviews = prepared['structured_reviews']
        completed_items = prepared['completed_items']
        checkout_path = prepared['checkout_path']
        sections = [('Summary', [summary])]
        for heading, values in (
                ('Evidence-backed learnings', learnings),
                ('Validated decisions', decisions), ('Evidence and checks', evidence)):
            sections.append((heading, values or ['None recorded.']))
        body = []
        for heading, values in sections:
            body += ['## ' + heading, '']
            body += [values[0]] if heading == 'Summary' else ['- ' + value for value in values]
            body.append('')
        if checkout_path:
            body += ['## Checkout', '', f"- [[{checkout_path[:-3]}]]", '']
        note = self.create_note({
            'title': 'Check-in — ' + title, 'body': '\n'.join(body), 'type': 'task-checkin',
            'status': payload.get('status', 'active'), 'task_id': payload.get('task_id'),
            'run_id': payload.get('run_id'), 'worker': worker, 'stage': payload.get('stage'),
            'source_notes': [checkout_path] if checkout_path else [],
            'tags': ['acc/checkin', 'project/' + _slug(self.c.project.name)],
            'scopes': scopes,
        })
        reviews = []
        legacy_kinds = {
            'issues': 'unresolved-issue', 'solutions': 'workaround', 'loops': 'failed-loop',
            'corrections': 'correction', 'unvalidated': 'unresolved-issue',
        }
        for key, values in review_values.items():
            for number, value in enumerate(values, 1):
                structured_reviews.append({
                    'title': f"{key.replace('-', ' ').title()} {number} — {title}",
                    'kind': legacy_kinds[key], 'situation': value,
                    'handling': 'Legacy result did not supply structured handling details.',
                    'outcome': 'Requires review before reuse.',
                    'uncertainty': 'Situation, handling, and outcome need a structured follow-up.',
                    'evidence': evidence,
                })
        for item in structured_reviews:
            item_body = [
                '## Situation', '', item['situation'], '',
                '## How the worker handled it', '', item['handling'], '',
                '## Outcome', '', item['outcome'], '',
                '## Remaining uncertainty', '', item['uncertainty'], '',
                '## Evidence', '',
            ]
            item_body += ['- ' + value for value in item['evidence']] or ['- No evidence attached.']
            reviews.append(self._create_review({
                'title': 'Review — ' + item['title'],
                'summary': item['situation'], 'body': '\n'.join(item_body),
                'target_path': note['path'], 'verdict': 'unreviewed', 'worker': worker,
                'task_id': payload.get('task_id'), 'run_id': payload.get('run_id'),
                'stage': payload.get('stage'),
                'source_notes': ([checkout_path] if checkout_path else []),
                'scopes': scopes, 'review_kind': item['kind'],
            }))
        knowledge_notes = []
        for item in completed_items:
            item_body = item['body'].strip() + '\n\n## Evidence\n\n' + '\n'.join(
                '- ' + value for value in item['evidence'])
            knowledge_notes.append(self.create_note({
                'title': item['title'], 'body': item_body, 'type': item['type'],
                'status': item['status'], 'folder': item['scope'], 'worker': worker,
                'task_id': payload.get('task_id'), 'run_id': payload.get('run_id'),
                'stage': payload.get('stage'),
                'source_notes': [note['path']] + ([checkout_path] if checkout_path else []),
                'scopes': [item['scope']], 'tags': ['acc/completed-knowledge'],
            }))
        if reviews or knowledge_notes:
            text = self.adapter.read(note['path']).rstrip()
            if knowledge_notes:
                text += '\n\n## Durable knowledge created\n\n' + '\n'.join(
                    f"- [[{item['path'][:-3]}]]" for item in knowledge_notes)
            if reviews:
                text += '\n\n## Pending reviews\n\n' + '\n'.join(
                    f"- [[{item['path'][:-3]}]]" for item in reviews)
            self.adapter.write(note['path'], text + '\n', replace=True)
        return {**note, 'review': reviews[0] if reviews else None, 'reviews': reviews,
                'knowledge_notes': knowledge_notes}

    def _review_items(self, value):
        if not isinstance(value, list) or len(value) > 50:
            raise ValueError('review_items must be an array of at most 50 items.')
        result = []
        required = ('title', 'kind', 'situation', 'handling', 'outcome', 'uncertainty')
        for item in value:
            if not isinstance(item, dict) or any(
                    not isinstance(item.get(key), str) or not item[key].strip()
                    for key in required):
                raise ValueError('Each review item needs title, kind, situation, handling, outcome, and uncertainty.')
            if item['kind'] not in REVIEW_KINDS:
                raise ValueError('Unknown review item kind.')
            evidence = self._short_list(item.get('evidence', []), 'review item evidence')
            result.append({**{key: item[key].strip() for key in required}, 'evidence': evidence})
        return result

    def _completed_items(self, value, scopes):
        if not isinstance(value, list) or len(value) > 50:
            raise ValueError('completed_knowledge must be an array of at most 50 items.')
        result = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError('Each completed knowledge item must be an object.')
            title, body, scope = item.get('title'), item.get('body'), item.get('scope')
            note_type, status = item.get('type', 'finding'), item.get('status', 'supported')
            if (not isinstance(title, str) or not title.strip() or not isinstance(body, str)
                    or not body.strip() or not isinstance(scope, str) or scope not in scopes):
                raise ValueError('Completed knowledge needs title, body, and an assigned task scope.')
            if note_type not in NOTE_TYPES - {'task-checkout', 'task-checkin', 'review'}:
                raise ValueError('Completed knowledge note type is invalid.')
            if status not in ('supported', 'verified'):
                raise ValueError('Completed knowledge must be supported or verified.')
            evidence = self._short_list(item.get('evidence', []), 'completed knowledge evidence')
            if not evidence:
                raise ValueError('Completed knowledge requires evidence.')
            result.append({'title': title.strip(), 'body': body.strip(), 'scope': scope,
                           'type': note_type, 'status': status, 'evidence': evidence})
        return result

    def review(self, payload):
        """Create a pending, recursively reviewable assessment of any vault note."""
        self._require()
        target = payload.get('target_path')
        self.adapter.resolve(target, must_exist=True)
        title = str(payload.get('title', '')).strip()
        summary = str(payload.get('summary', '')).strip()
        worker = str(payload.get('worker', '')).strip()
        verdict = payload.get('verdict', 'needs-evidence')
        if not title or not summary or not worker:
            raise ValueError('Knowledge review requires title, summary, and worker.')
        if verdict not in REVIEW_VERDICTS - {'unreviewed'}:
            raise ValueError('Review verdict must be supports, challenges, mixed, or needs-evidence.')
        findings = self._short_list(payload.get('findings', []), 'findings')
        evidence = self._short_list(payload.get('evidence', []), 'evidence')
        body = ['## Findings', '']
        body += ['- ' + value for value in findings] or ['- None recorded.']
        body += ['', '## Evidence', '']
        body += ['- ' + value for value in evidence] or ['- No conclusive evidence supplied.']
        body.append('')
        return self._create_review({
            'title': title, 'summary': summary, 'body': '\n'.join(body),
            'target_path': target, 'verdict': verdict, 'worker': worker,
            'task_id': payload.get('task_id'), 'run_id': payload.get('run_id'),
            'stage': payload.get('stage'), 'source_notes': payload.get('source_notes', []),
            'scopes': payload.get('scopes'),
        })

    def _create_review(self, payload):
        target = payload['target_path']
        target_text = self.adapter.read(target)
        target_scopes = _list_property(target_text, 'scopes')
        target_header = FRONTMATTER.match(target_text)
        target_declares_scopes = bool(
            target_header and re.search(r'(?m)^scopes:', target_header.group(1)))
        target_thread = _property(target_text, 'review_thread')
        review_kind = payload.get('review_kind') or _property(target_text, 'review_kind')
        thread = target_thread or target
        requested_scopes = (self._scopes(payload.get('scopes'))
                            if payload.get('scopes') is not None else None)
        if target_declares_scopes and requested_scopes is not None and requested_scopes != target_scopes:
            raise ValueError('A review must inherit the target note scopes.')
        scopes = target_scopes if target_declares_scopes else (requested_scopes or [])
        sources = [target] + [item for item in payload.get('source_notes', []) if item != target]
        body = (f"This review targets [[{target[:-3]}]].\n\n"
                f"## Review state\n\n- Verdict: `{payload['verdict']}`\n"
                f"- Summary: {payload['summary']}\n\n" + payload['body'].strip())
        note = self.create_note({
            'title': payload['title'], 'body': body, 'type': 'review',
            'status': 'pending-review', 'worker': payload['worker'],
            'task_id': payload.get('task_id'), 'run_id': payload.get('run_id'),
            'stage': payload.get('stage'), 'source_notes': sources,
            'tags': ['acc/review', 'review/' + payload['verdict']],
            'scopes': scopes, 'review_target': target, 'review_thread': thread,
            'review_verdict': payload['verdict'], 'review_kind': review_kind,
        })
        self._append_review_link(target, note['path'])
        return {**note, 'target_path': target, 'verdict': payload['verdict']}

    def _append_review_link(self, target, review_path):
        with self.lock:
            text = self.adapter.read(target)
            current = []
            match = REVIEW_LINKS.search(text)
            if match:
                current = re.findall(r'\[\[([^\]]+)\]\]', match.group(0))
                text = REVIEW_LINKS.sub('\n', text, count=1)
            link = review_path[:-3]
            if link not in current:
                current.append(link)
            block = ('<!-- ACC:REVIEWS:START -->\n'
                     '## Reviews\n\n' + '\n'.join('- [[' + item + ']]' for item in current) +
                     '\n<!-- ACC:REVIEWS:END -->\n\n')
            header = FRONTMATTER.match(text)
            if header:
                text = text[:header.end()] + block + text[header.end():].lstrip('\n')
            else:
                text = block + text.lstrip('\n')
            self.adapter.write(target, text.rstrip() + '\n', replace=True)

    def capture_result(self, task, result, stage, worker):
        if not self.enabled:
            return None
        knowledge = result.get('knowledge')
        if not isinstance(knowledge, dict):
            raise ValueError('Knowledge-enabled workers must return the required knowledge check-in object.')
        checkout = (task.get('knowledge') or {}).get('last_checkout')
        checkin_payload = {
            'title': f"{stage.capitalize()} Task {task.get('task_number')}: {task['title']}",
            'summary': result.get('summary'), 'task_id': task['id'], 'run_id': result.get('run_id'),
            'worker': worker, 'stage': stage, 'checkout_path': checkout.get('path') if checkout else None,
            **{key: knowledge.get(key, []) for key in
               ('learnings', 'issues', 'solutions', 'loops', 'decisions', 'corrections',
                'unvalidated', 'evidence')},
            'scopes': task.get('knowledge_scopes', []),
            'review_items': knowledge.get('review_items', []),
            'completed_knowledge': knowledge.get('completed_knowledge', []),
        }
        # Validate the complete result before any checkout, review, or check-in file is mutated.
        self._prepare_checkin(checkin_payload)
        with self.lock, self.adapter.transaction():
            if checkout:
                checkout_text = self.adapter.read(checkout['path'])
                synthesis = re.search(
                    r'<!-- ACC:CHECKOUT-SYNTHESIS:START -->(.*?)<!-- ACC:CHECKOUT-SYNTHESIS:END -->',
                    checkout_text, re.DOTALL)
                if synthesis and '- What I learned:\n- How I will apply it:' in synthesis.group(1):
                    supplied = knowledge.get('checkout_synthesis')
                    required = ('learned', 'application', 'conflicts', 'mistakes_to_avoid')
                    if isinstance(supplied, dict) and all(
                            isinstance(supplied.get(key), str) and supplied[key].strip()
                            and len(supplied[key]) <= 2_000 and '<!-- ACC:' not in supplied[key]
                            for key in required):
                        replacement = ('<!-- ACC:CHECKOUT-SYNTHESIS:START -->\n'
                                       f"- What I learned: {supplied['learned'].strip()}\n"
                                       f"- How I will apply it: {supplied['application'].strip()}\n"
                                       f"- Conflicts or uncertainty: {supplied['conflicts'].strip()}\n"
                                       f"- Known mistakes I will avoid: {supplied['mistakes_to_avoid'].strip()}\n"
                                       '<!-- ACC:CHECKOUT-SYNTHESIS:END -->')
                        checkout_text = re.sub(
                            r'<!-- ACC:CHECKOUT-SYNTHESIS:START -->.*?<!-- ACC:CHECKOUT-SYNTHESIS:END -->',
                            replacement, checkout_text, count=1, flags=re.DOTALL)
                        synthesis = re.search(
                            r'<!-- ACC:CHECKOUT-SYNTHESIS:START -->(.*?)<!-- ACC:CHECKOUT-SYNTHESIS:END -->',
                            checkout_text, re.DOTALL)
                if not synthesis or '- What I learned:\n- How I will apply it:' in synthesis.group(1):
                    raise ValueError('Worker did not complete the required checkout synthesis before check-in.')
                self._validated_review_acknowledgements(checkout, knowledge)
                checkout_text = self._complete_review_acknowledgements(
                    checkout, knowledge, checkout_text)
                self.adapter.write(checkout['path'], checkout_text, replace=True)
            note = self._checkin(checkin_payload)
        task.setdefault('knowledge', {})['last_checkin'] = note
        if note.get('reviews'):
            task['knowledge']['last_reviews'] = note['reviews']
            task['knowledge']['last_review'] = note['reviews'][-1]
        if note.get('knowledge_notes'):
            task['knowledge']['last_knowledge_notes'] = note['knowledge_notes']
        return note

    def record_result_failure(self, task, worker, stage, error, evidence=None, *,
                              kind='failed-loop', situation=None, handling=None,
                              outcome=None, uncertainty=None):
        """Route a rejected worker result into the review cycle.

        A schema or check-in validation failure is itself an operational exception.  It cannot be
        trusted as a factual check-in, but silently dropping it would also erase a failed loop that
        later workers should know about.  Anchor the review to the immutable checkout and make the
        operation idempotent for repeated handling of the same failed run.
        """
        if not self.enabled:
            return None
        checkout = (task.get('knowledge') or {}).get('last_checkout')
        if not checkout:
            return None
        message = str(error).strip()[:2_000] or 'The worker result was rejected.'
        run_id = checkout.get('run_id') or task.get('run_id')
        signature = f"{checkout['path']}:{run_id}:{stage}:{worker}:{kind}:{message}"
        knowledge_state = task.setdefault('knowledge', {})
        recorded = knowledge_state.setdefault('result_failure_reviews', {})
        if signature in recorded:
            return recorded[signature]
        evidence_items = self._short_list(evidence or [], 'result failure evidence')
        situation = (situation or 'ACC rejected the worker result during check-in validation.').strip()
        handling = (handling or
                    'The worker handling cannot be trusted from the rejected payload. ACC stopped '
                    'the transition, preserved task files and checkout, and required follow-up.').strip()
        outcome = (outcome or f'The result was not accepted: {message}').strip()
        uncertainty = (uncertainty or
                       'Task changes may exist. Any partially written managed notes or links require '
                       'review before reuse.').strip()
        body = [
            '## Situation', '',
            situation, '',
            '## How the worker handled it', '',
            handling, '',
            '## Outcome', '',
            outcome, '',
            '## Remaining uncertainty', '',
            uncertainty, '',
            '## Evidence', '',
        ]
        body += ['- ' + item for item in evidence_items] or ['- ACC check-in validation error: ' + message]
        review = self._create_review({
            'title': 'Review — ' + ('unfinished worker run' if kind == 'unfinished-work'
                                    else 'failed worker loop'),
            'summary': message,
            'body': '\n'.join(body),
            'target_path': checkout['path'],
            'verdict': 'unreviewed',
            'worker': worker or 'acc',
            'task_id': task.get('id'),
            'run_id': run_id,
            'stage': stage,
            'source_notes': [checkout['path']],
            'scopes': task.get('knowledge_scopes', checkout.get('scopes', [])),
            'review_kind': kind,
        })
        recorded[signature] = review
        knowledge_state['last_result_failure_review'] = review
        knowledge_state['last_review'] = review
        return review

    def transition(self, payload):
        with self.lock:
            return self._transition(payload)

    def _transition(self, payload):
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
