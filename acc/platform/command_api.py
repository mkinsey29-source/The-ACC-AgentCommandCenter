"""Application service for authenticated M09 remote mutations."""
from __future__ import annotations

from typing import Any, Mapping

from ..auth import AuthenticationError, AuthorizationError, AuthService
from ..auth.models import _bounded_id
from .api import ApiError, ApiResponse, bearer_token
from .commands import (
    COMMAND_KINDS, CommandConflict, CommandRequest, IdempotencyConflict,
    PlatformCommandRepository, QuotaExceeded, QuotaReservation,
)


class CommandApi:
    def __init__(self, auth: AuthService, commands: PlatformCommandRepository):
        self.auth = auth
        self.commands = commands

    def execute(
        self, authorization: object, account_id: object, project_id: object,
        body: object,
    ) -> ApiResponse:
        token = bearer_token(authorization)
        try:
            account_id = _bounded_id(account_id, 'account_id')
            project_id = _bounded_id(project_id, 'project_id')
        except (ValueError, TypeError) as exc:
            raise ApiError(400, 'invalid_request', 'Invalid request.') from exc
        if not isinstance(body, Mapping):
            raise ApiError(400, 'invalid_request', 'Invalid request.')
        kind = body.get("kind")
        permissions = COMMAND_KINDS.get(kind)
        if permissions is None:
            raise ApiError(400, 'invalid_request', 'Invalid command.')
        try:
            context = self.auth.authorize(
                token, account_id=account_id, permissions=permissions,
                entitlements=('acc.web',),
            )
        except AuthenticationError as exc:
            raise ApiError(401, 'authentication_required', 'Authentication required.') from exc
        except AuthorizationError as exc:
            raise ApiError(403, 'access_denied', 'Access denied.') from exc

        try:
            command = CommandRequest(
                operation_id=body.get('operation_id'),
                account_id=account_id,
                project_id=project_id,
                kind=kind,
                expected_revision=body.get('expected_revision'),
                payload=body.get('payload', {}),
                actor_user_id=context.session.user_id,
            )
            quotas = self._quotas(body.get("quotas", ()))
            for quota in quotas:
                current = self.commands.quota_used(account_id, quota.name)
                self.auth.require_limit(
                    token, quota.name, current + quota.amount, account_id=account_id)
            result = self.commands.execute(command, quotas=quotas)
        except AuthorizationError as exc:
            raise ApiError(403, 'access_denied', 'Access denied.') from exc
        except IdempotencyConflict as exc:
            raise ApiError(409, 'idempotency_conflict', 'Operation ID was already used.') from exc
        except CommandConflict as exc:
            return ApiResponse(409, {
                'error': {'code': 'revision_conflict', 'message': 'Project revision changed.'},
                'current_revision': exc.current_revision,
            })
        except QuotaExceeded as exc:
            raise ApiError(409, 'quota_exceeded', 'Quota is unavailable.') from exc
        except (ValueError, TypeError) as exc:
            raise ApiError(400, 'invalid_request', 'Invalid command.') from exc

        if result.operation_id != command.operation_id:
            raise ApiError(500, 'invalid_repository_state', 'Command repository state is invalid.')
        return ApiResponse(200 if result.replayed else 201, {
            'operation_id': result.operation_id,
            'status': result.status,
            'revision': result.revision,
            'result': dict(result.result),
            'replayed': result.replayed,
        })

    @staticmethod
    def _quotas(value: object) -> tuple[QuotaReservation, ...]:
        if value in (None, (), []):
            return ()
        if not isinstance(value, list) or len(value) > 20:
            raise ValueError('quotas must be an array of at most 20 entries.')
        result = []
        seen = set()
        for item in value:
            if not isinstance(item, Mapping):
                raise ValueError('quota entries must be objects.')
            quota = QuotaReservation(item.get('name'), item.get('amount'))
            if quota.name in seen:
                raise ValueError('duplicate quota reservation.')
            seen.add(quota.name)
            result.append(quota)
        return tuple(result)
