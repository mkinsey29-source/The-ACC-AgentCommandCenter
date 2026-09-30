"""Application service for authenticated M09 remote mutations."""
from __future__ import annotations

from typing import Mapping

from ..auth import AuthenticationError, AuthorizationError, AuthService
from ..auth.models import _bounded_id
from .api import ApiError, ApiResponse, bearer_token
from .commands import (
    COMMAND_KINDS, COMMAND_QUOTAS, CommandConflict, CommandRequest, CommandStateConflict,
    CommandTargetNotFound, IdempotencyConflict, PlatformCommandRepository, QuotaExceeded,
)

# Exact request body schema. ``quotas`` is deliberately absent: quota use is server-derived.
_BODY_FIELDS = frozenset(('operation_id', 'kind', 'expected_revision', 'payload'))


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
        if not isinstance(body, Mapping) or set(body) != _BODY_FIELDS:
            raise ApiError(400, 'invalid_request', 'Invalid command.')
        kind = body['kind']
        permissions = COMMAND_KINDS.get(kind) if isinstance(kind, str) else None
        if permissions is None:
            raise ApiError(400, 'invalid_request', 'Invalid command.')

        # Live M08 check before every mutation: session, user, account, membership, the exact
        # command permission, and the acc.web entitlement.
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
                operation_id=body['operation_id'],
                account_id=account_id,
                project_id=project_id,
                kind=kind,
                expected_revision=body['expected_revision'],
                payload=body['payload'],
                actor_user_id=context.session.user_id,
            )
        except (ValueError, TypeError) as exc:
            raise ApiError(400, 'invalid_request', 'Invalid command.') from exc

        # The ceiling for every server-derived reservation is the live M08 entitlement limit; the
        # repository compares it with recorded usage inside its transaction.
        limits = {name: context.entitlements.limits.get(name) for name, _ in COMMAND_QUOTAS[kind]}
        try:
            result = self.commands.execute(command, limits=limits)
        except IdempotencyConflict as exc:
            raise ApiError(409, 'idempotency_conflict', 'Operation ID was already used.') from exc
        except CommandConflict as exc:
            return ApiResponse(409, {
                'error': {'code': 'revision_conflict', 'message': 'Project revision changed.'},
                'current_revision': exc.current_revision,
            })
        except QuotaExceeded as exc:
            raise ApiError(409, 'quota_exceeded', 'Quota is unavailable.') from exc
        except CommandTargetNotFound as exc:
            raise ApiError(404, 'not_found', 'Not found.') from exc
        except CommandStateConflict as exc:
            raise ApiError(409, 'state_conflict', 'Command is not valid in the current state.') from exc
        # Any other repository exception is a fault: PlatformApi.handle makes it an opaque 500.

        if result.operation_id != command.operation_id:
            raise ApiError(500, 'invalid_repository_state', 'Command repository state is invalid.')
        return ApiResponse(200 if result.replayed else 201, {
            'operation_id': result.operation_id,
            'status': result.status,
            'revision': result.revision,
            'result': dict(result.result),
            'replayed': result.replayed,
        })
