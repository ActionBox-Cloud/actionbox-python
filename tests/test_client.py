from __future__ import annotations

import json
import threading
import unittest

import httpx
from actionbox import (
    Action,
    Actionbox,
    ActionboxError,
    __version__,
    control,
    deployment_decision_context,
    link,
    send_heartbeat,
)


class ActionboxClientTests(unittest.TestCase):
    def client(self, handler: httpx.MockTransport) -> Actionbox:
        return Actionbox(
            "axb_test",
            http_client=httpx.Client(transport=handler),
            max_retries=1,
        )

    def test_decision_context_helpers_emit_the_generic_versioned_wire_contract(
        self,
    ) -> None:
        self.assertEqual(
            deployment_decision_context(
                reason="Staging passed.",
                proposed_change="Deploy 2.18.0.",
                risk_level="high",
                reversibility="reversible",
            ),
            {
                "schema_version": 1,
                "affected_scope": [],
                "reason": "Staging passed.",
                "current_state": None,
                "proposed_change": "Deploy 2.18.0.",
                "expected_effect": None,
                "risk_level": "high",
                "risk_summary": None,
                "reversibility": "reversible",
                "rollback_plan": None,
            },
        )

    def test_chat_delivery_policy_is_forwarded_on_create_and_update(self) -> None:
        payloads = []
        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"id": "act_chat", "title": "Private review", "status": "open"}})
        client = self.client(httpx.MockTransport(handler))
        client.create(title="Private review", chat_delivery="disabled")
        client.update("act_chat", chat_delivery="inherit")
        self.assertEqual(payloads[0]["chat_delivery"], "disabled")
        self.assertEqual(payloads[1], {"chat_delivery": "inherit"})

    def test_control_helpers_emit_the_bounded_wire_schema(self) -> None:
        self.assertEqual(
            control("rollback", "Rollback", destructive=True),
            {
                "key": "rollback",
                "label": "Rollback",
                "kind": "callback",
                "destructive": True,
            },
        )
        self.assertEqual(
            link("logs", "Open logs", "https://example.test/logs"),
            {
                "key": "logs",
                "label": "Open logs",
                "kind": "link",
                "url": "https://example.test/logs",
            },
        )

    def test_source_get_uses_strict_source_route_and_auth(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"data": {"id": "act/1", "status": "open"}})

        client = self.client(httpx.MockTransport(handler))
        action = client.get("act/1")
        self.assertEqual(action.id, "act/1")
        self.assertEqual(captured[0].url.path, "/v1/source/actions/act/1")
        self.assertEqual(captured[0].headers["Authorization"], "Bearer axb_test")
        self.assertEqual(captured[0].headers["X-Actionbox-Client"], f"python-sdk/{__version__}")

    def test_additive_action_fields_and_legacy_context_remain_readable(self) -> None:
        payload = {
            "id": "act_future",
            "status": "open",
            "action_version": 3,
            "fingerprint": "sha256:bound",
            "context": [
                {
                    "type": "logs",
                    "title": "Decision summary",
                    "content": "Proposed change: Deploy 2.18.0.",
                    "_actionbox_projection": "decision_context_v1",
                }
            ],
            "decision_context": {"schema_version": 1, "risk_level": "high"},
            "future_server_field": {"safe": True},
        }
        client = self.client(
            httpx.MockTransport(lambda _request: httpx.Response(200, json={"data": payload}))
        )

        action = client.get("act_future")

        self.assertEqual(action.action_version, 3)
        self.assertEqual(action.fingerprint, "sha256:bound")
        self.assertEqual(action.data["context"][0]["content"], "Proposed change: Deploy 2.18.0.")
        self.assertEqual(action.data["future_server_field"], {"safe": True})

    def test_source_sees_context_request_and_updates_the_same_action(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "act_1",
                        "status": "open",
                        "context_request": {
                            "id": "evt_1",
                            "question": "What can fail?",
                            "requested_fields": ["risk"],
                            "requested_by_user_id": "usr_1",
                            "requested_at": "2026-09-04T00:00:00Z",
                            "status": "pending",
                        },
                    }
                },
            )

        client = self.client(httpx.MockTransport(handler))
        action = client.get("act_1")
        self.assertEqual(action.context_request["question"], "What can fail?")
        client.update(action.id, context=[{"type": "logs", "content": "canary passed"}])
        self.assertEqual(captured[1].method, "PATCH")
        self.assertEqual(
            json.loads(captured[1].content),
            {"context": [{"type": "logs", "content": "canary passed"}]},
        )

    def test_source_can_close_context_request_as_unavailable(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"data": {"id": "act_1", "status": "open"}})

        client = self.client(httpx.MockTransport(handler))
        client.mark_context_unavailable(
            "act_1", "Production data is inaccessible.", "cannot_access"
        )
        self.assertEqual(
            captured[0].url.path,
            "/v1/source/actions/act_1/context-request/unavailable",
        )
        self.assertEqual(
            json.loads(captured[0].content),
            {
                "reason": "Production data is inaccessible.",
                "reason_code": "cannot_access",
            },
        )

    def test_create_has_idempotency_key_and_retries(self) -> None:
        calls = 0
        keys: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            keys.append(request.headers["Idempotency-Key"])
            if calls == 1:
                return httpx.Response(
                    503,
                    headers={"Retry-After": "0"},
                    json={"error": {"code": "TEMPORARY", "message": "retry"}},
                )
            return httpx.Response(201, json={"data": {"id": "act_1", "status": "open"}})

        client = self.client(httpx.MockTransport(handler))
        self.assertEqual(client.create(title="Test").id, "act_1")
        self.assertEqual(calls, 2)
        self.assertEqual(keys[0], keys[1])
        self.assertTrue(keys[0].startswith("sdk-"))

    def test_create_accepts_reviewer_email(self) -> None:
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(201, json={"data": {"id": "act_1", "status": "open"}})

        client = self.client(httpx.MockTransport(handler))
        client.create(title="Review", assignee_email="reviewer@example.com")
        self.assertEqual(captured[0]["assignee_email"], "reviewer@example.com")

    def test_create_accepts_restricted_reviewer_list(self) -> None:
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(201, json={"data": {"id": "act_1", "status": "open"}})

        client = self.client(httpx.MockTransport(handler))
        client.create(
            title="Private",
            visibility="restricted",
            reviewer_user_ids=["usr_one", "usr_two"],
        )
        self.assertEqual(captured[0]["visibility"], "restricted")
        self.assertEqual(captured[0]["reviewer_user_ids"], ["usr_one", "usr_two"])

    def test_create_accepts_typed_approval_policy(self) -> None:
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(201, json={"data": {"id": "act_1", "status": "open"}})

        client = self.client(httpx.MockTransport(handler))
        client.create(
            title="Approve",
            visibility="restricted",
            reviewer_user_ids=["usr_one", "usr_two"],
            approval_policy={"schema_version": 1, "mode": "all", "allow_source_override": False},
        )
        self.assertEqual(captured[0]["approval_policy"]["mode"], "all")

    def test_source_reports_a_durable_control_result(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "ctrlreq_1",
                        "status": "succeeded",
                        "control_key": "retry",
                    }
                },
            )

        client = self.client(httpx.MockTransport(handler))
        result = client.report_control_result(
            "ctrlreq/1",
            "succeeded",
            message="Recovered",
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(
            captured[0].url.raw_path,
            b"/v1/source/control-requests/ctrlreq%2F1/result",
        )
        self.assertEqual(
            json.loads(captured[0].content),
            {
                "status": "succeeded",
                "message": "Recovered",
                "reason_code": None,
            },
        )

    def test_non_idempotent_resolve_is_not_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503, json={"error": {"code": "TEMPORARY", "message": "retry"}})

        client = self.client(httpx.MockTransport(handler))
        with self.assertRaises(ActionboxError):
            client.resolve("act_1", "approve")
        self.assertEqual(calls, 1)

    def test_lowercase_idempotency_header_enables_safe_retry(self) -> None:
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503, json={"error": {"code": "TEMPORARY"}})
            return httpx.Response(200, json={"data": {"ok": True}})

        client = self.client(httpx.MockTransport(handler))
        result = client._request(
            "POST",
            "/v1/example",
            headers={"idempotency-key": "stable-operation"},
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls, 2)

    def test_typed_resolve_sends_one_response_envelope_and_escapes_action_id(
        self,
    ) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"data": {"id": "act/1", "status": "resolved"}})

        client = self.client(httpx.MockTransport(handler))
        client.resolve("act/1", response={"type": "boolean", "value": True})
        self.assertEqual(captured[0].url.raw_path, b"/v1/actions/act%2F1/resolve")
        self.assertEqual(
            json.loads(captured[0].content),
            {
                "option_id": None,
                "reason": None,
                "response": {"type": "boolean", "value": True},
            },
        )

    def test_typed_resolve_rejects_mixed_snapshot_fields(self) -> None:
        client = self.client(
            httpx.MockTransport(
                lambda _request: self.fail("mixed resolve input must fail before transport")
            )
        )
        with self.assertRaisesRegex(TypeError, "action_version"):
            client.resolve(
                "act_1",
                {"type": "boolean", "value": True, "action_version": 3},  # type: ignore[typeddict-unknown-key]
            )

    def test_resolve_can_bind_to_the_reviewed_action_snapshot(self) -> None:
        captured: list[httpx.Request] = []
        fingerprint = "sha256:" + "a" * 64

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "act_1",
                        "status": "resolved",
                        "action_version": 3,
                        "fingerprint": fingerprint,
                        "receipt": "compact-receipt",
                    }
                },
            )

        client = self.client(httpx.MockTransport(handler))
        action = client.resolve(
            "act_1",
            "approve",
            action_version=3,
            fingerprint=fingerprint,
        )
        self.assertEqual(action.action_version, 3)
        self.assertEqual(action.fingerprint, fingerprint)
        self.assertEqual(action.receipt, "compact-receipt")
        self.assertEqual(
            json.loads(captured[0].content),
            {
                "option_id": "approve",
                "reason": None,
                "action_version": 3,
                "fingerprint": fingerprint,
            },
        )

    def test_action_reports_an_exact_retryable_execution_outcome(self) -> None:
        captured: list[httpx.Request] = []
        fingerprint = "sha256:" + "a" * 64

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": "out_1",
                        "action_id": "act/1",
                        "status": "failed",
                        "duration_ms": 1200,
                        "rollback": True,
                        "reason_code": "LOCK_TIMEOUT",
                        "action_version": 3,
                        "fingerprint": fingerprint,
                        "created_at": "2026-08-23T00:00:00Z",
                    }
                },
            )

        client = self.client(httpx.MockTransport(handler))
        action = Action(
            client,
            {
                "id": "act/1",
                "status": "resolved",
                "action_version": 3,
                "fingerprint": fingerprint,
            },
        )
        outcome = action.report_outcome(
            "failed", duration_ms=1200, rollback=True, reason_code="LOCK_TIMEOUT"
        )
        self.assertEqual(outcome["id"], "out_1")
        self.assertEqual(captured[0].url.raw_path, b"/v1/actions/act%2F1/outcome")
        self.assertEqual(captured[0].headers["Idempotency-Key"], "outcome-act/1")
        self.assertEqual(
            json.loads(captured[0].content),
            {
                "status": "failed",
                "duration_ms": 1200,
                "rollback": True,
                "reason_code": "LOCK_TIMEOUT",
                "action_version": 3,
                "fingerprint": fingerprint,
            },
        )

    def test_get_rejects_an_oversized_response(self) -> None:
        payload = json.dumps({"data": {"value": "x" * 1_048_576}}).encode()
        client = self.client(
            httpx.MockTransport(lambda _request: httpx.Response(200, content=payload))
        )
        with self.assertRaises(ActionboxError) as caught:
            client.get("act_1")
        self.assertEqual(caught.exception.code, "RESPONSE_TOO_LARGE")

    def test_rejects_remote_plaintext_and_allows_loopback(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            Actionbox("axb_test", "http://example.com")
        client = Actionbox("axb_test", "http://127.0.0.1:8000")
        client.close()

    def test_wait_can_be_cancelled(self) -> None:
        client = self.client(httpx.MockTransport(lambda _request: httpx.Response(500)))
        action = Action(client, {"id": "act_1", "status": "open"})
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(ActionboxError) as caught:
            action.wait(timeout=1, poll_interval=0.01, cancel_event=cancelled)
        self.assertEqual(caught.exception.code, "ABORTED")

    def test_response_size_is_bounded(self) -> None:
        payload = json.dumps({"data": {"value": "x" * 1_048_576}}).encode()
        client = self.client(
            httpx.MockTransport(lambda _request: httpx.Response(200, content=payload))
        )
        with self.assertRaises(ActionboxError) as caught:
            client.get("act_1")
        self.assertEqual(caught.exception.code, "RESPONSE_TOO_LARGE")

    def test_ask_rejects_option_labels_with_colliding_derived_ids(self) -> None:
        client = self.client(
            httpx.MockTransport(
                lambda _request: self.fail("colliding options must fail before transport")
            )
        )
        with self.assertRaisesRegex(ValueError, "unique IDs"):
            client.ask(title="Choose", options=["A B", "A-B"], wait=False)

    def test_ask_rejects_nonhuman_or_changed_resolution(self) -> None:
        created = {
            "id": "act_bound",
            "title": "Deploy?",
            "description": "",
            "status": "open",
            "action_version": 1,
            "fingerprint": "sha256:original",
            "resolution_option_id": None,
        }

        for resolved_by, version, fingerprint in (
            ("source", 1, "sha256:original"),
            ("user", 2, "sha256:changed"),
        ):

            def handler(request: httpx.Request) -> httpx.Response:
                if request.method == "POST":
                    return httpx.Response(201, json={"data": created})
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            **created,
                            "status": "resolved",
                            "action_version": version,
                            "fingerprint": fingerprint,
                            "resolved_by_type": resolved_by,
                            "resolution_option_id": "approve",
                        }
                    },
                )

            with self.subTest(resolved_by=resolved_by, version=version):
                client = self.client(httpx.MockTransport(handler))
                with self.assertRaisesRegex(ActionboxError, "originally created"):
                    client.ask(title="Deploy?", options=["Approve"])

    def test_policy_ask_rejects_content_changes_and_unapproved_source(self) -> None:
        created = {"id": "act_binding", "status": "open", "action_version": 1,
                   "fingerprint": "sha256:original", "content_fingerprint": "sha256:content",
                   "approval_policy": {"schema_version": 1, "mode": "all", "allow_source_override": False}}
        for change in [
            {"action_version": 2, "fingerprint": "sha256:changed", "content_fingerprint": "sha256:changed"},
            {"resolved_by_type": "system"},
            {"resolved_by_type": "source", "approval_policy": {"schema_version": 1, "mode": "all", "allow_source_override": True}},
        ]:
            def handler(request):
                data = created if request.method == "POST" else {
                    **created, "status": "resolved", "resolved_by_type": "user",
                    "resolution_option_id": "approve", **change,
                }
                return httpx.Response(200, json={"data": data})
            with self.subTest(change=change):
                client = self.client(httpx.MockTransport(handler))
                with self.assertRaises(ActionboxError) as exc:
                    client.ask(title="Deploy?", options=["Approve"])
                self.assertEqual(exc.exception.code, "APPROVAL_BINDING_MISMATCH")

    def test_ask_accepts_valid_policy_resolution_after_roster_change_or_override(self) -> None:
        created = {
            "id": "act_policy",
            "title": "Deploy?",
            "description": "",
            "status": "open",
            "action_version": 1,
            "fingerprint": "sha256:original",
            "content_fingerprint": "sha256:same-content",
            "resolution_option_id": None,
            "approval_policy": {
                "schema_version": 1,
                "mode": "all",
                "allow_source_override": True,
            },
        }

        for resolved_by, version, fingerprint in (
            ("user", 2, "sha256:changed"),
            ("source", 1, "sha256:original"),
        ):

            def handler(request: httpx.Request) -> httpx.Response:
                if request.method == "POST":
                    return httpx.Response(201, json={"data": created})
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            **created,
                            "status": "resolved",
                            "action_version": version,
                            "fingerprint": fingerprint,
                            "resolved_by_type": resolved_by,
                            "resolution_option_id": "approve",
                        }
                    },
                )

            with self.subTest(resolved_by=resolved_by, version=version):
                client = self.client(httpx.MockTransport(handler))
                self.assertEqual(client.ask(title="Deploy?", options=["Approve"]), "approve")

    def test_ask_normalizes_option_whitespace_consistently(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201, json={"data": {"id": "act_1", "status": "open"}})

        client = self.client(httpx.MockTransport(handler))
        client.ask(title="Choose", options=["  Ship   now  "], wait=False)
        self.assertEqual(
            json.loads(captured[0].content)["options"],
            [{"id": "ship-now", "label": "  Ship   now  "}],
        )

    def test_ask_normalizes_common_symbols_into_valid_option_ids(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201, json={"data": {"id": "act_1", "status": "open"}})

        client = self.client(httpx.MockTransport(handler))
        client.ask(
            title="Choose",
            options=["Deploy to /var/www", "Cancel (abort)!", "Review & ship?"],
            wait=False,
        )
        self.assertEqual(
            json.loads(captured[0].content)["options"],
            [
                {"id": "deploy-to-var-www", "label": "Deploy to /var/www"},
                {"id": "cancel-abort", "label": "Cancel (abort)!"},
                {"id": "review-ship", "label": "Review & ship?"},
            ],
        )

    def test_ask_rejects_collisions_after_symbol_normalization(self) -> None:
        client = self.client(
            httpx.MockTransport(
                lambda _request: self.fail("colliding options must fail before transport")
            )
        )
        with self.assertRaisesRegex(ValueError, "unique IDs"):
            client.ask(title="Choose", options=["A/B", "A & B"], wait=False)

    def test_watch_source_methods_and_heartbeat(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={"data": [{"id": "wat_1", "name": "Backup", "status": "healthy"}]},
                )
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": "wat_1",
                        "name": "Backup",
                        "status": "new",
                        "heartbeat_url": "https://api.example/hb/hb_secret",
                    }
                },
            )

        client = self.client(httpx.MockTransport(handler))
        self.assertEqual(client.watches.list()[0].id, "wat_1")
        created = client.watches.create(
            name="Backup", schedule_type="interval", interval_seconds=300
        )
        self.assertEqual(created.heartbeat_url, "https://api.example/hb/hb_secret")
        self.assertEqual(captured[0].url.path, "/v1/source/watches")
        self.assertNotIn("source_id", json.loads(captured[1].content))
        self.assertTrue(captured[1].headers["Idempotency-Key"].startswith("watch-"))

        heartbeat_request: list[httpx.Request] = []
        transport = httpx.MockTransport(
            lambda request: heartbeat_request.append(request) or httpx.Response(204)
        )
        with httpx.Client(transport=transport) as heartbeat_client:
            send_heartbeat(
                "http://localhost/hb/hb_test",
                "success",
                http_client=heartbeat_client,
            )
        self.assertEqual(heartbeat_request[0].method, "POST")
        self.assertEqual(heartbeat_request[0].url.path, "/hb/hb_test/success")

        heartbeat_request.clear()
        with httpx.Client(transport=transport) as heartbeat_client:
            send_heartbeat(
                "http://localhost/hb/hb_test/",
                http_client=heartbeat_client,
            )
        self.assertEqual(heartbeat_request[0].url.path, "/hb/hb_test")

    def test_create_update_and_resource_helpers_reject_unknown_fields(self) -> None:
        client = self.client(
            httpx.MockTransport(
                lambda _request: self.fail("invalid payload must fail before transport")
            )
        )
        with self.assertRaisesRegex(TypeError, "session_token"):
            client.create(title="Test", session_token="secret")
        with self.assertRaisesRegex(TypeError, "db_password"):
            client.update("act_1", db_password="secret")
        with self.assertRaisesRegex(TypeError, "private_key"):
            client.runs.start(
                external_id="run-1",
                agent_name="agent",
                title="Test",
                private_key="secret",
            )
        with self.assertRaisesRegex(TypeError, "session_token"):
            client.watches.create(
                name="Backup",
                schedule_type="interval",
                interval_seconds=300,
                session_token="secret",
            )

    def test_watch_create_reuses_caller_idempotency_key_across_retries(self) -> None:
        keys: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            keys.append(request.headers["Idempotency-Key"])
            if len(keys) == 1:
                return httpx.Response(
                    503,
                    headers={"Retry-After": "0"},
                    json={"error": {"code": "TEMPORARY"}},
                )
            return httpx.Response(
                201, json={"data": {"id": "wat_1", "name": "Backup", "status": "new"}}
            )

        client = self.client(httpx.MockTransport(handler))
        client.watches.create(
            name="Backup",
            schedule_type="interval",
            interval_seconds=300,
            idempotency_key="watch-caller-key",
        )
        self.assertEqual(keys, ["watch-caller-key", "watch-caller-key"])

    def test_unexpected_environment_fails_closed(self) -> None:
        client = self.client(httpx.MockTransport(lambda _request: httpx.Response(500)))
        action = Action(client, {"id": "act_1", "status": "open", "environment": "preview"})
        with self.assertRaisesRegex(ActionboxError, "unsupported environment"):
            _ = action.environment

    def test_watch_source_lifecycle_helpers_are_scoped(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if request.method == "DELETE":
                return httpx.Response(204)
            body: dict[str, object] = {
                "id": "wat_1",
                "name": "Backup",
                "status": "healthy",
            }
            if request.url.path.endswith("/token/rotate"):
                body["heartbeat_url"] = "https://api.example/hb/hb_rotated"
            return httpx.Response(200, json={"data": body})

        client = self.client(httpx.MockTransport(handler))
        client.watches.pause("wat/1")
        client.watches.resume("wat/1")
        rotated = client.watches.rotate_token("wat/1")
        client.watches.archive("wat/1")
        self.assertEqual(rotated.heartbeat_url, "https://api.example/hb/hb_rotated")
        self.assertEqual(
            [request.url.raw_path.decode("ascii") for request in captured],
            [
                "/v1/source/watches/wat%2F1/pause",
                "/v1/source/watches/wat%2F1/resume",
                "/v1/source/watches/wat%2F1/token/rotate",
                "/v1/source/watches/wat%2F1",
            ],
        )

    def test_run_helper_hides_progress_sequence_bookkeeping(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            body = json.loads(request.content) if request.content else {}
            if request.url.path == "/v1/runs":
                sequence, status = 0, "running"
            elif request.url.path.endswith("/progress"):
                sequence, status = body["sequence"], body.get("status", "running")
            else:
                sequence, status = body["progress_sequence"], body["status"]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "run/1",
                        "status": status,
                        "progress_sequence": sequence,
                    }
                },
            )

        client = self.client(httpx.MockTransport(handler))
        run = client.runs.start(
            external_id="task-42",
            agent_name="codex",
            title="Fix checkout",
        )
        run.progress(stage="tests", checkpoint="test-184")
        run.complete()

        self.assertEqual(run.status, "succeeded")
        self.assertEqual(
            [request.url.raw_path.decode("ascii") for request in captured],
            [
                "/v1/runs",
                "/v1/source/runs/run%2F1/progress",
                "/v1/source/runs/run%2F1/complete",
            ],
        )
        self.assertEqual(json.loads(captured[1].content)["sequence"], 1)
        self.assertEqual(json.loads(captured[2].content)["progress_sequence"], 1)


if __name__ == "__main__":
    unittest.main()
