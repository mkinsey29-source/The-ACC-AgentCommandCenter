import unittest

from aiohttp.test_utils import AioHTTPTestCase

from staging_server import Settings, create_app


class StagingServerTests(AioHTTPTestCase):
    async def get_application(self):
        return create_app(Settings(
            token="test-token",
            allowed_origins={"https://acc-web-feasibility.example"},
            ticket_ttl=60,
            event_interval=0.01,
        ))

    def headers(self, token="test-token", origin="https://acc-web-feasibility.example"):
        return {"Authorization": f"Bearer {token}", "Origin": origin}

    async def test_health_is_public(self):
        response = await self.client.get("/healthz")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["status"], "ok")

    async def test_state_requires_bearer_token(self):
        response = await self.client.get(
            "/api/state", headers={"Origin": "https://acc-web-feasibility.example"})
        self.assertEqual(response.status, 401)

    async def test_state_returns_synthetic_acc_shape_and_cors(self):
        response = await self.client.get("/api/state", headers=self.headers())
        self.assertEqual(response.status, 200)
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "https://acc-web-feasibility.example")
        payload = await response.json()
        self.assertTrue(payload["staging"])
        self.assertEqual(len(payload["tasks"]), 24)
        self.assertIn("task_area", payload["tasks"][0])

    async def test_disallowed_origin_is_rejected(self):
        response = await self.client.get(
            "/api/state", headers=self.headers(origin="https://evil.example"))
        self.assertEqual(response.status, 403)

    async def test_ticket_is_required_and_single_use_for_websocket(self):
        response = await self.client.post("/api/ws-ticket", headers=self.headers())
        self.assertEqual(response.status, 200)
        ticket = (await response.json())["ticket"]

        ws = await self.client.ws_connect(
            "/events?ticket=" + ticket,
            headers={"Origin": "https://acc-web-feasibility.example"},
        )
        first = await ws.receive_json(timeout=1)
        self.assertEqual(first["kind"], "transport")
        second = await ws.receive_json(timeout=1)
        self.assertEqual(second["kind"], "staging_event")
        await ws.close()

        response = await self.client.get(
            "/events?ticket=" + ticket,
            headers={"Origin": "https://acc-web-feasibility.example"},
        )
        self.assertEqual(response.status, 401)

    async def test_preflight_is_scoped_to_allowed_origin(self):
        response = await self.client.options(
            "/api/state",
            headers={
                "Origin": "https://acc-web-feasibility.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        self.assertEqual(response.status, 204)
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "https://acc-web-feasibility.example")


if __name__ == "__main__":
    unittest.main()
