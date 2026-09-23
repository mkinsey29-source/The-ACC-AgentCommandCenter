"""Evidence-backed autonomous agent selection with an optional TypeSafe Jev signal."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from .core import now


AREA = re.compile(r'[a-z][a-z0-9-]{0,39}(?:\.[a-z][a-z0-9-]{0,39}){0,3}\Z')
ROLES = ('implementer', 'reviewer', 'coordinator')


def _bounded_number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError(f'{label} must be between {low} and {high}.')
    return float(value)


class TypeSafeEvaluator:
    """Small standard-library client for POST /v1/systemone.

    Credentials are resolved only when a request is made and never enter persisted state.
    """

    def __init__(self, settings=None):
        settings = settings or {}
        if not isinstance(settings, dict):
            raise ValueError('TypeSafe routing settings must be an object.')
        self.endpoint = settings.get('endpoint', 'https://api.typesafe.ai/v1/systemone')
        self.model = settings.get('model', 'jev-latest')
        self.api_key_env = settings.get('api_key_env', 'TYPESAFE_API_KEY')
        self.api_key_file = settings.get('api_key_file')
        self.timeout = settings.get('timeout_seconds', 20)
        if not isinstance(self.endpoint, str) or not self.endpoint.startswith(('https://', 'http://127.0.0.1:', 'http://localhost:')):
            raise ValueError('TypeSafe endpoint must use HTTPS or a loopback HTTP address.')
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError('TypeSafe model must be a nonempty string.')
        if not isinstance(self.api_key_env, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{1,79}', self.api_key_env):
            raise ValueError('TypeSafe api_key_env must be an uppercase environment variable name.')
        if self.api_key_file is not None and not isinstance(self.api_key_file, str):
            raise ValueError('TypeSafe api_key_file must be a path string.')
        if type(self.timeout) is not int or not 1 <= self.timeout <= 120:
            raise ValueError('TypeSafe timeout_seconds must be 1–120.')

    def _key(self):
        if self.api_key_file:
            try:
                key = Path(self.api_key_file).expanduser().read_text(encoding='utf-8').strip()
            except OSError:
                key = ''
        else:
            key = os.environ.get(self.api_key_env, '').strip()
        return key

    def configured(self):
        return bool(self._key())

    def evaluate(self, state, questions):
        key = self._key()
        if not key:
            raise RuntimeError('TypeSafe API credential is not configured.')
        body = json.dumps({'state': state, 'model': self.model, 'questions': questions},
                          separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            self.endpoint, data=body, method='POST',
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise RuntimeError('TypeSafe returned HTTP ' + str(response.status) + '.')
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise RuntimeError('TypeSafe response exceeded 1 MB.')
                result = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError('TypeSafe evaluation failed: ' + str(exc)) from exc
        if not isinstance(result, dict) or not isinstance(result.get('answers'), dict):
            raise RuntimeError('TypeSafe response did not contain typed answers.')
        return result


class AgentRouter:
    """Choose workflow roles and retain objective outcomes by task area.

    Hard eligibility and numeric policy remain deterministic. Jev supplies only the
    semantic-fit probability distribution for the already eligible candidates.
    """

    DEFAULT_WEIGHTS = {'semantic_fit': .35, 'quality': .20, 'reliability': .25,
                       'cost_efficiency': .15, 'continuity': .05}

    def __init__(self, coordinator, settings=None):
        self.c = coordinator
        settings = settings or {}
        if not isinstance(settings, dict):
            raise ValueError('Routing settings must be an object.')
        allowed = {'enabled', 'typesafe', 'weights', 'confidence_threshold', 'max_failures'}
        if set(settings) - allowed:
            raise ValueError('Unknown routing setting: ' + sorted(set(settings) - allowed)[0])
        self.enabled = settings.get('enabled') is True
        self.confidence_threshold = _bounded_number(
            settings.get('confidence_threshold', .55), 'Routing confidence threshold', 0, 1)
        max_failures = settings.get('max_failures', 3)
        if type(max_failures) is not int or not 1 <= max_failures <= 10:
            raise ValueError('Routing max_failures must be 1–10.')
        self.max_failures = max_failures
        configured_weights = settings.get('weights', {})
        if not isinstance(configured_weights, dict):
            raise ValueError('Routing weights must be an object.')
        weights = {**self.DEFAULT_WEIGHTS, **configured_weights}
        if set(weights) != set(self.DEFAULT_WEIGHTS):
            raise ValueError('Routing weights must use the documented dimensions.')
        self.weights = {key: _bounded_number(value, key + ' weight', 0, 1)
                        for key, value in weights.items()}
        total = sum(self.weights.values())
        if not total:
            raise ValueError('At least one routing weight must be positive.')
        self.weights = {key: value / total for key, value in self.weights.items()}
        self.evaluator = TypeSafeEvaluator(settings.get('typesafe'))
        with self.c.store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS agent_outcomes (
                run_id TEXT PRIMARY KEY, at REAL NOT NULL, agent TEXT NOT NULL,
                role TEXT NOT NULL, task_area TEXT NOT NULL, success INTEGER NOT NULL,
                accepted INTEGER, switched INTEGER NOT NULL, duration REAL,
                cost_usd REAL, input_tokens INTEGER, output_tokens INTEGER,
                data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS agent_outcomes_profile
                ON agent_outcomes(agent, role, task_area, at);
            ''')

    @staticmethod
    def _agent_profile(agent):
        route = agent.get('routing') or {}
        if not isinstance(route, dict):
            raise ValueError('Agent routing metadata must be an object.')
        roles = route.get('roles', ROLES)
        capabilities = route.get('capabilities', [])
        if not isinstance(roles, list) and not isinstance(roles, tuple):
            raise ValueError('Agent routing roles must be an array.')
        if any(role not in ROLES for role in roles):
            raise ValueError('Agent routing roles must be implementer, reviewer, or coordinator.')
        if not isinstance(capabilities, list) or not all(isinstance(x, str) and AREA.fullmatch(x)
                                                        for x in capabilities):
            raise ValueError('Agent routing capabilities must be dotted lowercase names.')
        quality = _bounded_number(route.get('quality_tier', 3), 'Agent quality_tier', 1, 5) / 5
        cost = _bounded_number(route.get('cost_tier', 3), 'Agent cost_tier', 1, 5)
        return {'roles': tuple(roles), 'capabilities': tuple(capabilities), 'quality': quality,
                'cost_efficiency': (6 - cost) / 5,
                'description': str(route.get('description') or agent.get('description') or '')[:1000]}

    def _mode(self):
        with self.c.store.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='project_mode'").fetchone()
        return row[0] if row and row[0] in ('online', 'offline') else 'online'

    def _eligible(self, role, required=(), exclude=(), force_offline=False):
        candidates = []
        offline = force_offline or self._mode() == 'offline'
        for agent in self.c.agents.values():
            if agent['id'] == 'local-command' or not agent.get('available') or agent['id'] in exclude:
                continue
            if role != 'implementer' and agent.get('kind') == 'job':
                continue
            if offline and agent.get('local') is not True:
                continue
            profile = self._agent_profile(agent)
            if role not in profile['roles']:
                continue
            declared = set(profile['capabilities'])
            if declared and any(item not in declared for item in required):
                continue
            candidates.append((agent, profile))
        return candidates

    def profiles(self, task_area=None):
        with self.c.store.connect() as db:
            rows = db.execute('SELECT * FROM agent_outcomes ORDER BY at').fetchall()
        grouped = {}
        for row in rows:
            if task_area and row['task_area'] not in (task_area, 'general'):
                continue
            key = (row['agent'], row['role'])
            item = grouped.setdefault(key, {'attempts': 0, 'successes': 0, 'accepted': 0,
                                             'acceptance_samples': 0, 'switches': 0,
                                             'cost_total': 0., 'cost_samples': 0,
                                             'duration_total': 0., 'duration_samples': 0})
            item['attempts'] += 1
            item['successes'] += row['success']
            item['switches'] += row['switched']
            if row['accepted'] is not None:
                item['acceptance_samples'] += 1
                item['accepted'] += row['accepted']
            if row['cost_usd'] is not None:
                item['cost_total'] += row['cost_usd']; item['cost_samples'] += 1
            if row['duration'] is not None:
                item['duration_total'] += row['duration']; item['duration_samples'] += 1
        result = {}
        for key, item in grouped.items():
            attempts = item['attempts']
            result[key] = {
                'samples': attempts,
                'reliability': (item['successes'] + 1) / (attempts + 2),
                'acceptance_rate': ((item['accepted'] + 1) / (item['acceptance_samples'] + 2)
                                    if item['acceptance_samples'] else None),
                'switch_rate': (item['switches'] + 1) / (attempts + 2),
                'mean_cost_usd': (item['cost_total'] / item['cost_samples']
                                  if item['cost_samples'] else None),
                'mean_duration_seconds': (item['duration_total'] / item['duration_samples']
                                          if item['duration_samples'] else None),
            }
        return result

    @staticmethod
    def _task_fields(task):
        area = task.get('task_area', 'general')
        required = task.get('required_capabilities', [])
        if not isinstance(area, str) or not AREA.fullmatch(area):
            raise ValueError('task_area must be a dotted lowercase name.')
        if not isinstance(required, list) or not all(isinstance(x, str) and AREA.fullmatch(x)
                                                     for x in required):
            raise ValueError('required_capabilities must contain dotted lowercase names.')
        risk = task.get('risk', 'medium')
        if risk not in ('low', 'medium', 'high'):
            raise ValueError('risk must be low, medium, or high.')
        return area, tuple(dict.fromkeys(required)), risk

    @staticmethod
    def _lexical_fit(task, profile, required):
        capabilities = set(profile['capabilities'])
        if required and capabilities:
            return len(set(required) & capabilities) / len(required)
        words = set(re.findall(r'[a-z0-9]+', (task.get('title', '') + ' ' + task.get('instruction', '')).lower()))
        terms = set(re.findall(r'[a-z0-9]+', (profile['description'] + ' ' + ' '.join(capabilities)).lower()))
        return min(1., .45 + len(words & terms) / max(8, len(words)))

    def _questions(self, task, pools, evidence):
        questions = {}
        for role, candidates in pools.items():
            criteria = {}
            for agent, profile in candidates:
                history = evidence.get((agent['id'], role), {})
                criteria[agent['id']] = {
                    'description': profile['description'] or agent.get('name', agent['id']),
                    'declared_capabilities': list(profile['capabilities']),
                    'observed_history': history or 'No completed samples yet.'}
            questions[role] = {
                'type': 'choice',
                'instructions': {
                    'decision': f'Which eligible agent is the best {role} for this task?',
                    'guidance': ('Judge semantic task fit and likely execution quality. The application '
                                 'separately enforces availability, policy, exact cost, and observed metrics.')},
                'criteria': criteria}
        state = {'task': {key: task.get(key) for key in ('title', 'instruction', 'task_area',
                                                         'required_capabilities', 'risk')},
                 'note': 'All candidates shown already passed deterministic eligibility checks.'}
        return state, questions

    def _semantic_answers(self, task, pools, evidence):
        if not self.evaluator.configured():
            return {}, None, 'deterministic_no_typesafe_credential', None
        state, questions = self._questions(task, pools, evidence)
        try:
            response = self.evaluator.evaluate(state, questions)
            return response['answers'], response.get('usage'), 'typesafe_jev', None
        except RuntimeError as exc:
            return {}, None, 'deterministic_typesafe_error', str(exc)

    def route(self, task, defaults=None, exclude=None):
        """Return a complete workflow specification and an auditable routing record."""
        area, required, risk = self._task_fields(task)
        defaults = defaults or {}
        exclude = exclude or {}
        force_offline = defaults.get('mode') == 'offline'
        pools = {role: self._eligible(role, required if role != 'coordinator' else (),
                                      exclude.get(role, ()), force_offline) for role in ROLES}
        for role, candidates in pools.items():
            if not candidates:
                raise ValueError('No eligible available agent can serve as ' + role + '.')
        evidence = self.profiles(area)
        answers, usage, source, error = self._semantic_answers(task, pools, evidence)
        rankings, selected, confidences = {}, {}, {}
        for role in ROLES:
            answer = answers.get(role, {})
            probabilities = answer.get('probabilities', {}) if answer.get('type') == 'choice' else {}
            confidence = answer.get('confidence') if isinstance(answer.get('confidence'), (int, float)) else None
            scored = []
            for agent, profile in pools[role]:
                history = evidence.get((agent['id'], role), {})
                semantic = probabilities.get(agent['id'])
                lexical = self._lexical_fit(task, profile, required)
                if not isinstance(semantic, (int, float)) or isinstance(semantic, bool) or not 0 <= semantic <= 1:
                    semantic = lexical
                elif confidence is not None:
                    # A diffuse Jev distribution should not overpower declared capabilities
                    # and measured outcomes. It still contributes without becoming an approval gate.
                    semantic = confidence * semantic + (1 - confidence) * lexical
                reliability = history.get('reliability', .5)
                acceptance = history.get('acceptance_rate')
                if acceptance is not None:
                    reliability = (reliability + acceptance) / 2
                continuity = 1. if defaults.get(role) == agent['id'] else .5
                dimensions = {'semantic_fit': semantic, 'quality': profile['quality'],
                              'reliability': reliability,
                              'cost_efficiency': profile['cost_efficiency'], 'continuity': continuity}
                score = sum(self.weights[key] * value for key, value in dimensions.items())
                scored.append({'agent': agent['id'], 'score': round(score, 6),
                               'dimensions': {k: round(v, 6) for k, v in dimensions.items()},
                               'samples': history.get('samples', 0)})
            scored.sort(key=lambda item: (-item['score'], item['agent']))
            if role == 'reviewer' and selected.get('implementer'):
                scored = [item for item in scored if item['agent'] != selected['implementer']]
                if not scored:
                    raise ValueError('No independent reviewer is eligible for the selected implementer.')
            rankings[role] = scored
            selected[role] = scored[0]['agent']
            confidences[role] = confidence
        low_confidence = any(value is not None and value < self.confidence_threshold
                             for value in confidences.values())
        spec = {**selected, 'max_rounds': defaults.get('max_rounds', 3),
                'mode': defaults.get('mode', 'online'), 'fallbacks': defaults.get('fallbacks', {})}
        decision = {'at': now(), 'task_area': area, 'required_capabilities': list(required),
                    'risk': risk, 'source': source, 'confidence': confidences,
                    'low_confidence': low_confidence, 'rankings': rankings, 'selected': selected,
                    'usage': usage, 'error': error,
                    'policy': 'Automatic assignment; confidence changes evidence weighting, not user approval.'}
        return spec, decision

    def select_orchestrator(self, text, preferred=None, force_offline=False):
        """Choose one coordinator-capable adapter for an ACC-owned conversation turn."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Automatic orchestrator selection needs pending conversation text.')
        task = {'title': 'Orchestrate conversation', 'instruction': text,
                'task_area': 'general', 'required_capabilities': [], 'risk': 'medium'}
        candidates = self._eligible('coordinator', (), (), force_offline)
        if not candidates:
            raise ValueError('No eligible available agent can orchestrate this conversation.')
        evidence = self.profiles('general')
        answers, usage, source, error = self._semantic_answers(
            task, {'coordinator': candidates}, evidence)
        answer = answers.get('coordinator', {})
        probabilities = answer.get('probabilities', {}) if answer.get('type') == 'choice' else {}
        confidence = answer.get('confidence') if isinstance(answer.get('confidence'), (int, float)) else None
        ranked = []
        for agent, profile in candidates:
            history = evidence.get((agent['id'], 'coordinator'), {})
            semantic = probabilities.get(agent['id'])
            lexical = self._lexical_fit(task, profile, ())
            if not isinstance(semantic, (int, float)) or isinstance(semantic, bool) or not 0 <= semantic <= 1:
                semantic = lexical
            elif confidence is not None:
                semantic = confidence * semantic + (1 - confidence) * lexical
            reliability = history.get('reliability', .5)
            acceptance = history.get('acceptance_rate')
            if acceptance is not None:
                reliability = (reliability + acceptance) / 2
            dimensions = {
                'semantic_fit': semantic,
                'quality': profile['quality'],
                'reliability': reliability,
                'cost_efficiency': profile['cost_efficiency'],
                'continuity': 1. if preferred == agent['id'] else .5,
            }
            score = sum(self.weights[key] * value for key, value in dimensions.items())
            ranked.append({'agent': agent['id'], 'score': round(score, 6),
                           'dimensions': {key: round(value, 6) for key, value in dimensions.items()},
                           'samples': history.get('samples', 0)})
        ranked.sort(key=lambda item: (-item['score'], item['agent']))
        selected = ranked[0]['agent']
        return selected, {
            'at': now(), 'selected': selected, 'source': source, 'confidence': confidence,
            'low_confidence': confidence is not None and confidence < self.confidence_threshold,
            'rankings': ranked, 'usage': usage, 'error': error,
            'policy': 'Automatic orchestrator selection; explicit session choices bypass this judgment.',
        }

    def reroute(self, task, role, reason, failed_agent=None):
        workflow = task['workflow']
        failures = workflow.setdefault('route_failures', [])
        if len(failures) >= self.max_failures:
            return None
        failed_agent = failed_agent or task['runs'][-1]['agent']
        failures.append({'at': now(), 'role': role, 'agent': failed_agent, 'reason': reason[:1000]})
        excluded = {item['agent'] for item in failures if item['role'] == role}
        defaults = {key: workflow.get(key) for key in ROLES}
        defaults.update(max_rounds=workflow['max_rounds'], mode=workflow['mode'],
                        fallbacks=workflow.get('fallbacks', {}))
        try:
            spec, decision = self.route(task, defaults, {role: excluded})
        except ValueError:
            return None
        replacement = spec[role]
        if role == 'reviewer' and replacement == workflow.get('implementation_agent'):
            return None
        workflow[role] = replacement
        if role == 'implementer':
            task['agent'] = replacement
        workflow.setdefault('routing_history', []).append(decision)
        failed_stage = {'implementer': 'implement', 'reviewer': 'review',
                        'coordinator': 'coordinate'}[role]
        failed_run = next((run for run in reversed(task.get('runs', []))
                           if run.get('agent') == failed_agent and run.get('stage') == failed_stage), None)
        if failed_run:
            with self.c.store.connect() as db:
                db.execute('UPDATE agent_outcomes SET switched=1 WHERE run_id=?',
                           (failed_run['id'],))
        return replacement

    def record_outcome(self, task, role, success, accepted=None, switched=False,
                       cost=None, usage=None, error=None):
        if not task.get('runs'):
            return
        run = task['runs'][-1]
        ended = run.get('ended', now())
        duration = max(0., ended - run['started']) if run.get('started') else None
        cost = cost if isinstance(cost, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        cost_usd = cost.get('usd', cost.get('cost_usd'))
        data = {'task_id': task['id'], 'task_number': task.get('task_number'),
                'revision': task['revision'], 'error': error, 'cost': cost, 'usage': usage}
        values = (run['id'], now(), run['agent'], role, task.get('task_area', 'general'),
                  int(bool(success)), None if accepted is None else int(bool(accepted)),
                  int(bool(switched)), duration,
                  float(cost_usd) if isinstance(cost_usd, (int, float)) else None,
                  usage.get('input_tokens') if type(usage.get('input_tokens')) is int else None,
                  usage.get('output_tokens') if type(usage.get('output_tokens')) is int else None,
                  json.dumps(data))
        with self.c.store.connect() as db:
            db.execute('''INSERT INTO agent_outcomes
                (run_id,at,agent,role,task_area,success,accepted,switched,duration,cost_usd,
                 input_tokens,output_tokens,data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET accepted=COALESCE(excluded.accepted,accepted),
                success=excluded.success, switched=MAX(switched,excluded.switched),
                duration=excluded.duration, cost_usd=COALESCE(excluded.cost_usd,cost_usd),
                input_tokens=COALESCE(excluded.input_tokens,input_tokens),
                output_tokens=COALESCE(excluded.output_tokens,output_tokens), data=excluded.data''', values)

    def mark_acceptance(self, run_id, accepted):
        if not run_id:
            return
        with self.c.store.connect() as db:
            db.execute('UPDATE agent_outcomes SET accepted=? WHERE run_id=?',
                       (int(bool(accepted)), run_id))

    def snapshot(self):
        profiles = self.profiles()
        return {'enabled': self.enabled, 'typesafe_configured': self.evaluator.configured(),
                'confidence_threshold': self.confidence_threshold,
                'profiles': [{'agent': key[0], 'role': key[1], **value}
                             for key, value in sorted(profiles.items())]}
