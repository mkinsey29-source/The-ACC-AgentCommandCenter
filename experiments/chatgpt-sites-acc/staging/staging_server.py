"""Isolated ACC staging backend for the ChatGPT Sites feasibility test.

This is intentionally NOT the production M08/M09 service. It exposes synthetic ACC-shaped
state over HTTP and a ticket-authenticated WebSocket event stream so the deployed Site can prove
cross-origin HTTPS/WSS connectivity without using Marvin's local ACC control token.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, Set

from aiohttp import WSMsgType, web

RUNTIME_KEY = web.AppKey("runtime", object)


@dataclass
class Settings:
    token: str
    allowed_origins: Set[str]
    ticket_ttl: int = 60
    event_interval: float = 1.0

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("ACC_STAGING_TOKEN", "").strip()
        if not token:
            raise RuntimeError("ACC_STAGING_TOKEN is required")
        raw_origins = os.environ.get("ACC_ALLOWED_ORIGINS", "").strip()
        origins = {item.strip().rstrip("/") for item in raw_origins.split(",") if item.strip()}
        if not origins:
            raise RuntimeError("ACC_ALLOWED_ORIGINS is required")
        ttl = int(os.environ.get("ACC_WS_TICKET_TTL", "60"))
        interval = float(os.environ.get("ACC_EVENT_INTERVAL", "1.0"))
        if not 10 <= ttl <= 300:
            raise RuntimeError("ACC_WS_TICKET_TTL must be between 10 and 300 seconds")
        if not 0.25 <= interval <= 30:
            raise RuntimeError("ACC_EVENT_INTERVAL must be between 0.25 and 30 seconds")
        return cls(token=token, allowed_origins=origins, ticket_ttl=ttl, event_interval=interval)


@dataclass
class Runtime:
    settings: Settings
    tickets: Dict[str, float] = field(default_factory=dict)
    sequence: int = 0

    def issue_ticket(self) -> str:
        self.expire_tickets()
        ticket = secrets.token_urlsafe(32)
        self.tickets[ticket] = time.monotonic() + self.settings.ticket_ttl
        return ticket

    def consume_ticket(self, ticket: str) -> bool:
        self.expire_tickets()
        deadline = self.tickets.pop(ticket, None)
        return deadline is not None and deadline >= time.monotonic()

    def expire_tickets(self) -> None:
        now = time.monotonic()
        for ticket, deadline in list(self.tickets.items()):
            if deadline < now:
                self.tickets.pop(ticket, None)


def _origin(request: web.Request) -> str:
    return request.headers.get("Origin", "").rstrip("/")


def _authorized(request: web.Request, settings: Settings) -> bool:
    value = request.headers.get("Authorization", "")
    scheme, _, credential = value.partition(" ")
    return scheme.lower() == "bearer" and secrets.compare_digest(credential, settings.token)


def _synthetic_tasks() -> list[dict]:
    titles = [
        "Validate Sites HTTPS transport",
        "Validate Sites WebSocket stream",
        "Exercise workspace switching",
        "Record staging event flow",
        "Confirm origin restriction",
        "Confirm ticket replay rejection",
        "Verify mobile reconnect",
        "Capture feasibility evidence",
    ]
    agents = ["Codex", "Claude", "DeepSeek", "Gemini"]
    statuses = ["running", "queued", "awaiting_review", "accepted"]
    areas = ["platform.api", "platform.events", "ui.workspace", "testing.runtime"]
    return [
        {
            "id": f"staging-task-{index + 1}",
            "task_number": index + 1,
            "title": titles[index % len(titles)] + f" {index // len(titles) + 1}",
            "status": statuses[index % len(statuses)],
            "agent": agents[index % len(agents)],
            "active_agent": agents[index % len(agents)] if index % 4 == 0 else None,
            "task_area": areas[index % len(areas)],
            "priority": 100 - index,
        }
        for index in range(24)
    ]


@web.middleware
async def cors_and_origin_guard(request: web.Request, handler):
    runtime: Runtime = request.app[RUNTIME_KEY]
    origin = _origin(request)

    # Sites/browser traffic must match an explicit allowlist. Health probes often have no Origin.
    if origin and origin not in runtime.settings.allowed_origins:
        raise web.HTTPForbidden(text="Origin is not allowed")

    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)

    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "acc-sites-staging"})


async def state(request: web.Request) -> web.Response:
    runtime: Runtime = request.app[RUNTIME_KEY]
    if not _authorized(request, runtime.settings):
        raise web.HTTPUnauthorized(text="Bearer staging token required")
    return web.json_response({
        "project": "ACC Sites Live Connectivity",
        "project_mode": "online",
        "tasks": _synthetic_tasks(),
        "staging": True,
    })


async def ws_ticket(request: web.Request) -> web.Response:
    runtime: Runtime = request.app[RUNTIME_KEY]
    if not _authorized(request, runtime.settings):
        raise web.HTTPUnauthorized(text="Bearer staging token required")
    ticket = runtime.issue_ticket()
    return web.json_response({
        "ticket": ticket,
        "path": "/events",
        "expires_in": runtime.settings.ticket_ttl,
    })


async def events(request: web.Request) -> web.StreamResponse:
    runtime: Runtime = request.app[RUNTIME_KEY]
    ticket = request.query.get("ticket", "")
    if not ticket or not runtime.consume_ticket(ticket):
        raise web.HTTPUnauthorized(text="Valid one-time WebSocket ticket required")

    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
    await ws.prepare(request)
    await ws.send_json({
        "kind": "transport",
        "message": "ACC staging WebSocket authenticated.",
        "data": {"source": "acc-sites-staging"},
    })

    async def sender() -> None:
        while not ws.closed:
            await asyncio.sleep(runtime.settings.event_interval)
            runtime.sequence += 1
            index = runtime.sequence % 24
            await ws.send_json({
                "kind": "staging_event",
                "message": f"Live staging event {runtime.sequence}: task {index + 1} heartbeat.",
                "data": {"sequence": runtime.sequence, "task_id": f"staging-task-{index + 1}"},
            })

    sender_task = asyncio.create_task(sender())
    try:
        async for message in ws:
            if message.type == WSMsgType.TEXT and message.data == "ping":
                await ws.send_str("pong")
            elif message.type == WSMsgType.ERROR:
                break
    finally:
        sender_task.cancel()
        try:
            await sender_task
        except asyncio.CancelledError:
            pass
    return ws


def create_app(settings: Settings | None = None) -> web.Application:
    runtime = Runtime(settings or Settings.from_env())
    app = web.Application(middlewares=[cors_and_origin_guard])
    app[RUNTIME_KEY] = runtime

    async def options(_: web.Request) -> web.Response:
        return web.Response(status=204)

    app.add_routes([
        web.get("/healthz", health),
        web.get("/api/state", state),
        web.post("/api/ws-ticket", ws_ticket),
        web.get("/events", events),
        web.options("/{tail:.*}", options),
    ])
    return app


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    web.run_app(create_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
