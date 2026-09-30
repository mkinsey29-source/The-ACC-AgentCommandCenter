"""Framework-neutral hosted API application boundary for M09 core v1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..auth import AuthenticationError, AuthorizationError, AuthService
from ..auth.models import _bounded_id
from .events import EventCursorError, PlatformEventSource, event_cursor, validate_event_batch
from .repository import AccountProjectView, PlatformReadRepository, safe_public_record


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: Mapping[str, Any]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def response(self) -> ApiResponse:
        return ApiResponse(self.status, {'error': {'code': self.code, 'message': self.message}})


def bearer_token(value: object) -> str:
    if not isinstance(value, str):
        raise ApiError(401, 'authentication_required', 'Authentication required.')
    scheme, separator, token = value.partition(' ')
    if separator != ' ' or scheme.lower() != 'bearer' or not token or token != token.strip():
        raise ApiError(401, 'authentication_required', 'Authentication required.')
    return token


class PlatformApi:
    """Account-scoped read/event application service.

    The future HTTP/WebSocket adapter maps routes to this service. Keeping this layer framework
    neutral makes tenant/auth/event behavior testable without choosing the public server framework.
    """

    def __init__(self, auth: AuthService, reads: PlatformReadRepository, events: PlatformEventSource):
        self.auth = auth
        self.reads = reads
        self.events = events

    def _authorize(
        self,
        authorization: object,
        account_id: object,
        *,
        permissions: tuple[str, ...],
        entitlements: tuple[str, ...] = ('acc.web',),
    ):
        token = bearer_token(authorization)
        try:
            account_id = _bounded_id(account_id, 'account_id')
            context = self.auth.authorize(
                token,
                account_id=account_id,
                permissions=permissions,
                entitlements=entitlements,
            )
            return token, account_id, context
        except AuthenticationError as exc:
            raise ApiError(401, 'authentication_required', 'Authentication required.') from exc
        except AuthorizationError as exc:
            raise ApiError(403, 'access_denied', 'Access denied.') from exc
        except (ValueError, TypeError) as exc:
            raise ApiError(400, 'invalid_request', 'Invalid request.') from exc

    @staticmethod
    def _route_ids(**values: object) -> dict[str, str]:
        """Validate caller-supplied route IDs before any repository call (400, not 500)."""
        try:
            return {name: _bounded_id(value, name) for name, value in values.items()}
        except (ValueError, TypeError) as exc:
            raise ApiError(400, 'invalid_request', 'Invalid request.') from exc

    @staticmethod
    def _project_view(project: AccountProjectView, account_id: str) -> dict[str, Any]:
        if not isinstance(project, AccountProjectView) or project.account_id != account_id:
            raise ApiError(500, 'invalid_repository_state', 'Platform repository state is invalid.')
        return {
            'account_id': project.account_id,
            'project_id': project.project_id,
            'name': project.name,
            'mode': project.mode,
            'revision': project.revision,
        }

    def list_projects(self, authorization: object, account_id: object) -> ApiResponse:
        _, account_id, _ = self._authorize(
            authorization, account_id, permissions=('project.read',))
        try:
            projects = [self._project_view(item, account_id) for item in self.reads.projects(account_id)]
        except ApiError:
            raise
        except (ValueError, TypeError) as exc:
            raise ApiError(500, 'invalid_repository_state', 'Platform repository state is invalid.') from exc
        return ApiResponse(200, {'projects': projects})

    def project_state(
        self, authorization: object, account_id: object, project_id: object,
    ) -> ApiResponse:
        # The response embeds full task records, so it needs task.read as well as project.read;
        # otherwise project.read alone would bypass the task endpoint's permission.
        _, account_id, _ = self._authorize(
            authorization, account_id, permissions=('project.read', 'task.read'))
        project_id = self._route_ids(project_id=project_id)['project_id']
        try:
            project = self.reads.project(account_id, project_id)
            if project is None:
                raise ApiError(404, 'not_found', 'Project not found.')
            project_view = self._project_view(project, account_id)
            if project.project_id != project_id:
                raise ApiError(500, 'invalid_repository_state', 'Platform repository state is invalid.')
            tasks = [
                safe_public_record(item, account_id=account_id, project_id=project_id)
                for item in self.reads.tasks(account_id, project_id)
            ]
            workers = [
                safe_public_record(item, account_id=account_id, project_id=project_id)
                for item in self.reads.workers(account_id, project_id)
            ]
            attention = [
                safe_public_record(item, account_id=account_id, project_id=project_id)
                for item in self.reads.attention(account_id, project_id)
            ]
        except ApiError:
            raise
        except (ValueError, TypeError) as exc:
            raise ApiError(500, 'invalid_repository_state', 'Platform repository state is invalid.') from exc
        return ApiResponse(200, {
            'project': project_view,
            'tasks': tasks,
            'workers': workers,
            'attention': attention,
        })

    def task(
        self,
        authorization: object,
        account_id: object,
        project_id: object,
        task_id: object,
    ) -> ApiResponse:
        _, account_id, _ = self._authorize(
            authorization, account_id, permissions=('task.read',))
        ids = self._route_ids(project_id=project_id, task_id=task_id)
        project_id, task_id = ids['project_id'], ids['task_id']
        try:
            task = self.reads.task(account_id, project_id, task_id)
            if task is None:
                raise ApiError(404, 'not_found', 'Task not found.')
            value = safe_public_record(task, account_id=account_id, project_id=project_id)
            if value.get('id') != task_id:
                raise ApiError(500, 'invalid_repository_state', 'Platform repository state is invalid.')
        except ApiError:
            raise
        except (ValueError, TypeError) as exc:
            raise ApiError(500, 'invalid_repository_state', 'Platform repository state is invalid.') from exc
        return ApiResponse(200, {'task': value})

    def events_after(
        self,
        authorization: object,
        account_id: object,
        project_id: object,
        after: object = 0,
        *,
        limit: int = 200,
    ) -> ApiResponse:
        _, account_id, _ = self._authorize(
            authorization, account_id, permissions=('event.read',))
        try:
            project_id = _bounded_id(project_id, 'project_id')
            cursor = event_cursor(after)
            if type(limit) is not int or not 1 <= limit <= 500:
                raise EventCursorError('Event limit must be between 1 and 500.')
        except (EventCursorError, ValueError, TypeError) as exc:
            raise ApiError(400, 'invalid_request', 'Invalid event cursor.') from exc

        try:
            batch = self.events.read_events(
                account_id, project_id, after=cursor, limit=limit)
            batch = validate_event_batch(
                batch, account_id=account_id, project_id=project_id, after=cursor, limit=limit)
        except (ValueError, TypeError) as exc:
            raise ApiError(500, 'invalid_event_source', 'Platform event source is invalid.') from exc

        return ApiResponse(200, {
            'events': [
                {
                    'seq': event.seq,
                    'account_id': event.account_id,
                    'project_id': event.project_id,
                    'kind': event.kind,
                    'at': event.at,
                    'data': dict(event.data),
                }
                for event in batch.events
            ],
            'cursor': batch.cursor,
            'has_more': batch.has_more,
        })

    @staticmethod
    def handle(callable_, *args, **kwargs) -> ApiResponse:
        """Run one operation and always return an ``ApiResponse``.

        Any unexpected exception (a storage driver error, a bug) becomes an opaque 500 so that no
        exception text, which may contain row data, reaches the client. The adapter should log the
        chained exception server-side.
        """
        try:
            return callable_(*args, **kwargs)
        except ApiError as exc:
            return exc.response()
        except Exception:
            return ApiError(500, 'internal_error', 'Internal error.').response()
