from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import math
import random
import re
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal, NotRequired, Self, TypedDict, cast
from urllib.parse import quote, urlsplit

import httpx

__version__ = "0.2.1"
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_RETRY_DELAY = 30.0

OptionStyle = Literal["default", "primary", "destructive"]


class ActionOption(TypedDict):
    """An option used by a single-choice or multi-choice interaction."""

    id: str
    label: str
    style: NotRequired[OptionStyle]
    sort_order: NotRequired[int]


ActionOptionInput = ActionOption


class ActionControl(TypedDict, total=False):
    key: str
    label: str
    kind: Literal["callback", "link", "actionbox"]
    url: str
    destructive: bool
    expires_at: str


def control(
    key: str,
    label: str,
    *,
    destructive: bool = False,
    expires_at: str | None = None,
    kind: Literal["callback", "actionbox"] = "callback",
) -> ActionControl:
    result: ActionControl = {"key": key, "label": label, "kind": kind}
    if destructive:
        result["destructive"] = True
    if expires_at is not None:
        result["expires_at"] = expires_at
    return result


def link(key: str, label: str, url: str, *, expires_at: str | None = None) -> ActionControl:
    result: ActionControl = {"key": key, "label": label, "kind": "link", "url": url}
    if expires_at is not None:
        result["expires_at"] = expires_at
    return result


class BooleanInteraction(TypedDict):
    type: Literal["boolean"]
    label: str
    true_label: NotRequired[str]
    false_label: NotRequired[str]


class SingleChoiceInteraction(TypedDict):
    type: Literal["single_choice"]
    label: str
    options: list[ActionOption]


class MultiChoiceInteraction(TypedDict):
    type: Literal["multi_choice"]
    label: str
    options: list[ActionOption]
    min_selections: NotRequired[int]
    max_selections: NotRequired[int | None]


class TextInteraction(TypedDict):
    type: Literal["text"]
    label: str
    placeholder: NotRequired[str | None]
    multiline: NotRequired[bool]
    min_length: NotRequired[int]
    max_length: NotRequired[int]


class IntegerInteraction(TypedDict):
    type: Literal["integer"]
    label: str
    min: NotRequired[int | None]
    max: NotRequired[int | None]
    step: NotRequired[int]
    unit: NotRequired[str | None]


class NumberInteraction(TypedDict):
    type: Literal["number"]
    label: str
    min: NotRequired[int | float | None]
    max: NotRequired[int | float | None]
    step: NotRequired[int | float]
    unit: NotRequired[str | None]


class RatingInteraction(TypedDict):
    type: Literal["rating"]
    label: str
    min: NotRequired[int]
    max: NotRequired[int]
    low_label: NotRequired[str | None]
    high_label: NotRequired[str | None]


class FormBooleanField(BooleanInteraction):
    id: str
    required: NotRequired[bool]


class FormSingleChoiceField(SingleChoiceInteraction):
    id: str
    required: NotRequired[bool]


class FormMultiChoiceField(MultiChoiceInteraction):
    id: str
    required: NotRequired[bool]


class FormTextField(TextInteraction):
    id: str
    required: NotRequired[bool]


class FormIntegerField(IntegerInteraction):
    id: str
    required: NotRequired[bool]


class FormNumberField(NumberInteraction):
    id: str
    required: NotRequired[bool]


class FormRatingField(RatingInteraction):
    id: str
    required: NotRequired[bool]


FormField = (
    FormBooleanField
    | FormSingleChoiceField
    | FormMultiChoiceField
    | FormTextField
    | FormIntegerField
    | FormNumberField
    | FormRatingField
)


class FormInteraction(TypedDict):
    type: Literal["form"]
    label: str
    fields: list[FormField]


TypedInteraction = (
    BooleanInteraction
    | SingleChoiceInteraction
    | MultiChoiceInteraction
    | TextInteraction
    | IntegerInteraction
    | NumberInteraction
    | RatingInteraction
    | FormInteraction
)
Interaction = TypedInteraction


class BooleanResponse(TypedDict):
    type: Literal["boolean"]
    value: bool


class TextResponse(TypedDict):
    type: Literal["text"]
    value: str


class IntegerResponse(TypedDict):
    type: Literal["integer"]
    value: int


class NumberResponse(TypedDict):
    type: Literal["number"]
    value: int | float


class RatingResponse(TypedDict):
    type: Literal["rating"]
    value: int


class SingleChoiceResponse(TypedDict):
    type: Literal["single_choice"]
    value: str


class MultiChoiceResponse(TypedDict):
    type: Literal["multi_choice"]
    value: list[str]


class FormResponse(TypedDict):
    type: Literal["form"]
    values: dict[str, Any]


TypedResponse = (
    BooleanResponse
    | TextResponse
    | IntegerResponse
    | NumberResponse
    | RatingResponse
    | SingleChoiceResponse
    | MultiChoiceResponse
    | FormResponse
)
InteractionResponse = TypedResponse
Response = TypedResponse

DecisionRiskLevel = Literal["unknown", "low", "medium", "high", "critical"]
DecisionReversibility = Literal["unknown", "reversible", "partially_reversible", "irreversible"]


class DecisionContext(TypedDict):
    schema_version: NotRequired[Literal[1]]
    reason: str
    current_state: NotRequired[str | None]
    proposed_change: str
    expected_effect: NotRequired[str | None]
    risk_level: DecisionRiskLevel
    risk_summary: NotRequired[str | None]
    reversibility: DecisionReversibility
    rollback_plan: NotRequired[str | None]
    affected_scope: NotRequired[list[str]]


def decision_context(
    *,
    reason: str,
    proposed_change: str,
    risk_level: DecisionRiskLevel,
    reversibility: DecisionReversibility,
    current_state: str | None = None,
    expected_effect: str | None = None,
    risk_summary: str | None = None,
    rollback_plan: str | None = None,
    affected_scope: list[str] | None = None,
) -> DecisionContext:
    """Build the optional structured review context accepted by every Action."""
    return cast(
        DecisionContext,
        {
            "schema_version": 1,
            "reason": reason,
            "current_state": current_state,
            "proposed_change": proposed_change,
            "expected_effect": expected_effect,
            "risk_level": risk_level,
            "risk_summary": risk_summary,
            "reversibility": reversibility,
            "rollback_plan": rollback_plan,
            "affected_scope": affected_scope or [],
        },
    )


generic_decision_context = decision_context
deployment_decision_context = decision_context
refund_decision_context = decision_context
database_change_decision_context = decision_context
access_request_decision_context = decision_context


class ApprovalPolicy(TypedDict, total=False):
    schema_version: Literal[1]
    mode: Literal["any", "all", "quorum"]
    required_approvals: int
    approval_option_id: str
    rejection_option_id: str
    allow_source_override: bool


class ApprovalVote(TypedDict, total=False):
    id: str
    reviewer_user_id: str | None
    outcome: Literal["approve", "reject"]
    option_id: str | None
    response: TypedResponse | None
    reason: str | None
    action_version: int
    created_at: str


class ApprovalProgress(TypedDict):
    state: Literal["pending", "approved", "rejected", "overridden", "expired", "cancelled"]
    required_approvals: int
    approval_count: int
    rejection_count: int
    remaining_approvals: int
    eligible_reviewer_count: int
    votes: list[ApprovalVote]


class ActionCreateInput(TypedDict):
    """The JSON fields accepted by ``Actionbox.create``."""

    title: str
    chat_delivery: NotRequired[Literal["inherit", "disabled"]]
    description: NotRequired[str]
    priority: NotRequired[Literal["low", "normal", "high", "urgent"]]
    open_url: NotRequired[str]
    dedupe_key: NotRequired[str]
    callback_url: NotRequired[str]
    controls: NotRequired[list[ActionControl]]
    expires_at: NotRequired[str]
    on_expire: NotRequired[dict[str, Any]]
    context: NotRequired[list[dict[str, Any]]]
    decision_class: NotRequired[str]
    decision_context: NotRequired[DecisionContext]
    options: NotRequired[list[ActionOption]]
    interaction: NotRequired[TypedInteraction]
    metadata: NotRequired[dict[str, Any]]
    visibility: NotRequired[Literal["workspace", "restricted"]]
    reviewer_user_ids: NotRequired[list[str]]
    reviewer_emails: NotRequired[list[str]]
    approval_policy: NotRequired[ApprovalPolicy]
    assignee_user_id: NotRequired[str]
    assignee_email: NotRequired[str]
    run_id: NotRequired[str]


class ActionPatchInput(TypedDict, total=False):
    chat_delivery: Literal["inherit", "disabled"]
    title: str
    description: str
    priority: Literal["low", "normal", "high", "urgent"]
    open_url: str | None
    expires_at: str | None
    on_expire: dict[str, Any]
    context: list[dict[str, Any]]
    decision_class: str | None
    decision_context: DecisionContext | None
    controls: list[ActionControl]
    metadata: dict[str, Any]


class ActionContextRequest(TypedDict, total=False):
    id: str
    question: str
    requested_fields: list[str]
    requested_by_user_id: str | None
    requested_at: str
    status: Literal["pending", "provided", "unavailable"]
    reason: str
    reason_code: Literal["not_available", "cannot_access", "not_applicable", "sensitive", "unknown"]
    responded_at: str


_ACTION_CREATE_EXTRA_FIELDS = frozenset(
    {
        "chat_delivery",
        "priority",
        "open_url",
        "dedupe_key",
        "callback_url",
        "controls",
        "expires_at",
        "on_expire",
        "context",
        "decision_class",
        "decision_context",
        "metadata",
        "visibility",
        "reviewer_user_ids",
        "reviewer_emails",
        "approval_policy",
        "assignee_user_id",
        "assignee_email",
        "run_id",
    }
)
_ACTION_PATCH_FIELDS = frozenset(ActionPatchInput.__annotations__)
_RUN_CREATE_EXTRA_FIELDS = frozenset(
    {"task_id", "status", "stage", "checkpoint", "stall_after_seconds", "metadata"}
)
_RESOLVE_INPUT_FIELDS = frozenset(
    {"action_version", "fingerprint", "option_id", "response", "reason", "approval_override"}
)
_TYPED_RESPONSE_TYPES = frozenset(
    {
        "boolean",
        "text",
        "integer",
        "number",
        "rating",
        "single_choice",
        "multi_choice",
        "form",
    }
)


def _reject_unknown_fields(payload: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unsupported = sorted(set(payload) - allowed)
    if unsupported:
        raise TypeError(f"{label} contains unsupported field(s): {', '.join(unsupported)}")


class ResolveInput(TypedDict, total=False):
    """The JSON fields accepted by the generic resolve endpoint."""

    action_version: int
    fingerprint: str
    option_id: str
    response: TypedResponse
    reason: str
    approval_override: bool


class ActionOutcome(TypedDict):
    id: str
    action_id: str
    status: Literal["success", "failed"]
    duration_ms: int | None
    rollback: bool
    reason_code: str | None
    action_version: int
    fingerprint: str
    created_at: str


class ControlRequest(TypedDict, total=False):
    id: str
    action_id: str
    action_version: int
    action_fingerprint: str
    control_key: str
    control_label: str
    control_kind: Literal["callback", "actionbox"]
    status: Literal[
        "requested",
        "delivered",
        "running",
        "succeeded",
        "failed",
        "expired",
        "cancelled",
    ]
    result_message: str | None
    result_reason_code: str | None
    verified_at: str | None
    verification_source: Literal["watch", "agent_run"] | None
    verification_reference: str | None
    requested_at: str
    completed_at: str | None


class ActionboxError(RuntimeError):
    def __init__(self, message: str, code: str = "UNKNOWN", status: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


RunStatus = Literal["queued", "running", "waiting", "succeeded", "failed", "cancelled"]


@dataclass(slots=True)
class AgentRun:
    """One autonomous task; progress sequencing is handled for the caller."""

    client: Actionbox = field(repr=False, compare=False)
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def status(self) -> RunStatus:
        return cast(RunStatus, self.data["status"])

    @property
    def progress_sequence(self) -> int:
        return int(self.data.get("progress_sequence", 0))

    @property
    def stalled(self) -> bool:
        return bool(self.data.get("stalled", False))

    @property
    def stall_action_id(self) -> str | None:
        value = self.data.get("stall_action_id")
        return value if isinstance(value, str) else None

    def progress(
        self,
        *,
        status: Literal["running", "waiting"] | None = None,
        stage: str | None = None,
        checkpoint: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AgentRun:
        payload: dict[str, Any] = {"sequence": self.progress_sequence + 1}
        if status is not None:
            payload["status"] = status
        if stage is not None:
            payload["stage"] = stage
        if checkpoint is not None:
            payload["checkpoint"] = checkpoint
        if len(payload) == 1:
            raise ValueError("provide status, stage, or checkpoint")
        self.data = cast(
            dict[str, Any],
            self.client._request(
                "PATCH",
                f"/v1/source/runs/{quote(self.id, safe='')}/progress",
                json=payload,
                headers={"Idempotency-Key": f"run-progress-{self.id}-{payload['sequence']}"},
                cancel_event=cancel_event,
            ),
        )
        return self

    def complete(
        self,
        status: Literal["succeeded", "failed", "cancelled"] = "succeeded",
        *,
        reason_code: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AgentRun:
        self.data = cast(
            dict[str, Any],
            self.client._request(
                "POST",
                f"/v1/source/runs/{quote(self.id, safe='')}/complete",
                json={
                    "status": status,
                    "progress_sequence": self.progress_sequence,
                    "reason_code": reason_code,
                },
                headers={"Idempotency-Key": f"run-complete-{self.id}"},
                cancel_event=cancel_event,
            ),
        )
        return self


class RunsResource:
    def __init__(self, client: Actionbox) -> None:
        self.client = client

    def start(
        self,
        *,
        external_id: str,
        agent_name: str,
        title: str,
        cancel_event: threading.Event | None = None,
        **payload: Any,
    ) -> AgentRun:
        _reject_unknown_fields(payload, _RUN_CREATE_EXTRA_FIELDS, "Run create input")
        data = self.client._request(
            "POST",
            "/v1/runs",
            json={
                "external_id": external_id,
                "agent_name": agent_name,
                "title": title,
                **payload,
            },
            headers={"Idempotency-Key": f"run-start-{external_id}"},
            cancel_event=cancel_event,
        )
        return AgentRun(self.client, cast(dict[str, Any], data))

    def get(
        self,
        run_id: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> AgentRun:
        data = self.client._request(
            "GET",
            f"/v1/source/runs/{quote(run_id, safe='')}",
            cancel_event=cancel_event,
        )
        return AgentRun(self.client, cast(dict[str, Any], data))


WatchStatus = Literal["new", "healthy", "down", "paused"]
WatchScheduleType = Literal["interval", "cron"]
WatchSignalMethod = Literal["any", "post"]


class WatchCreateInput(TypedDict, total=False):
    source_id: str
    name: str
    schedule_type: WatchScheduleType
    interval_seconds: int
    cron_expression: str
    timezone: str
    grace_seconds: int
    max_runtime_seconds: int
    priority: Literal["low", "normal", "high", "urgent"]
    runbook_url: str
    signal_method: WatchSignalMethod


@dataclass(slots=True)
class Watch:
    """Safe Watch state. The raw heartbeat URL is only present on creation."""

    client: Actionbox = field(repr=False, compare=False)
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def status(self) -> str:
        return self.data["status"]

    @property
    def heartbeat_url(self) -> str | None:
        value = self.data.get("heartbeat_url")
        return value if isinstance(value, str) else None


class WatchesResource:
    def __init__(self, client: Actionbox) -> None:
        self.client = client

    def list(self, *, cancel_event: threading.Event | None = None) -> list[Watch]:
        data = self.client._request("GET", "/v1/source/watches", cancel_event=cancel_event)
        if not isinstance(data, list):
            raise ActionboxError(
                "Actionbox returned an invalid Watch list.", "INVALID_RESPONSE", 200
            )
        return [Watch(self.client, item) for item in data if isinstance(item, dict)]

    def create(
        self,
        *,
        idempotency_key: str | None = None,
        cancel_event: threading.Event | None = None,
        **payload: Any,
    ) -> Watch:
        _reject_unknown_fields(
            payload, frozenset(WatchCreateInput.__annotations__), "Watch create input"
        )
        data = self.client._request(
            "POST",
            "/v1/source/watches",
            json=payload,
            headers={"Idempotency-Key": idempotency_key or f"watch-{secrets.token_urlsafe(18)}"},
            cancel_event=cancel_event,
        )
        return Watch(self.client, data)

    def pause(self, watch_id: str, *, cancel_event: threading.Event | None = None) -> Watch:
        data = self.client._request(
            "POST",
            f"/v1/source/watches/{quote(watch_id, safe='')}/pause",
            cancel_event=cancel_event,
        )
        return Watch(self.client, data)

    def resume(self, watch_id: str, *, cancel_event: threading.Event | None = None) -> Watch:
        data = self.client._request(
            "POST",
            f"/v1/source/watches/{quote(watch_id, safe='')}/resume",
            cancel_event=cancel_event,
        )
        return Watch(self.client, data)

    def rotate_token(self, watch_id: str, *, cancel_event: threading.Event | None = None) -> Watch:
        data = self.client._request(
            "POST",
            f"/v1/source/watches/{quote(watch_id, safe='')}/token/rotate",
            cancel_event=cancel_event,
        )
        return Watch(self.client, data)

    def archive(self, watch_id: str, *, cancel_event: threading.Event | None = None) -> None:
        self.client._request(
            "DELETE",
            f"/v1/source/watches/{quote(watch_id, safe='')}",
            cancel_event=cancel_event,
        )


@dataclass(slots=True)
class Action:
    client: Actionbox = field(repr=False, compare=False)
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def status(self) -> str:
        return self.data["status"]

    @property
    def environment(self) -> Literal["live", "test"]:
        value = self.data.get("environment", "live")
        if value not in {"live", "test"}:
            raise ActionboxError(
                f"Actionbox returned an unsupported environment value: {value!r}.",
                "INVALID_RESPONSE",
                200,
            )
        return cast(Literal["live", "test"], value)

    @property
    def action_version(self) -> int:
        return int(self.data.get("action_version", 1))

    @property
    def fingerprint(self) -> str | None:
        value = self.data.get("fingerprint")
        return value if isinstance(value, str) else None

    @property
    def resolved_by(self) -> str | None:
        value = self.data.get("resolved_by_type")
        return value if isinstance(value, str) else None

    @property
    def controls(self) -> list[ActionControl]:
        return cast(list[ActionControl], self.data.get("controls") or [])

    @property
    def visibility(self) -> Literal["workspace", "restricted"]:
        return cast(Literal["workspace", "restricted"], self.data.get("visibility", "workspace"))

    @property
    def reviewers(self) -> list[dict[str, str]]:
        return cast(list[dict[str, str]], self.data.get("reviewers") or [])

    @property
    def approval_policy(self) -> ApprovalPolicy | None:
        return cast(ApprovalPolicy | None, self.data.get("approval_policy"))

    @property
    def approval_progress(self) -> ApprovalProgress | None:
        return cast(ApprovalProgress | None, self.data.get("approval_progress"))

    @property
    def control_requests(self) -> list[ControlRequest]:
        return cast(list[ControlRequest], self.data.get("control_requests") or [])

    @property
    def context_request(self) -> ActionContextRequest | None:
        return cast(ActionContextRequest | None, self.data.get("context_request"))

    @property
    def decision(self) -> str | None:
        return self.data.get("resolution_option_id")

    @property
    def interaction(self) -> TypedInteraction | None:
        return cast(TypedInteraction | None, self.data.get("interaction"))

    @property
    def response(self) -> TypedResponse | None:
        return cast(TypedResponse | None, self.data.get("response"))

    @property
    def receipt(self) -> str | None:
        value = self.data.get("receipt")
        return value if isinstance(value, str) else None

    @property
    def outcome(self) -> ActionOutcome | None:
        return cast(ActionOutcome | None, self.data.get("outcome"))

    def report_outcome(
        self,
        status: Literal["success", "failed"],
        *,
        duration_ms: int | None = None,
        rollback: bool = False,
        reason_code: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ActionOutcome:
        fingerprint = self.fingerprint
        if fingerprint is None:
            raise ValueError("the Action has no fingerprint to bind the outcome")
        return self.client.report_outcome(
            self.id,
            status,
            action_version=self.action_version,
            fingerprint=fingerprint,
            duration_ms=duration_ms,
            rollback=rollback,
            reason_code=reason_code,
            cancel_event=cancel_event,
        )

    def refresh(self, *, cancel_event: threading.Event | None = None) -> Action:
        self.data = self.client.get(self.id, cancel_event=cancel_event).data
        return self

    def wait(
        self,
        timeout: float = 3600,
        poll_interval: float = 2,
        *,
        cancel_event: threading.Event | None = None,
    ) -> str | TypedResponse | None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than zero")
        deadline = time.monotonic() + timeout
        while self.status == "open" and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if remaining < 1:
                if cancel_event is not None:
                    if cancel_event.wait(remaining):
                        raise ActionboxError("Actionbox wait was cancelled.", "ABORTED", 0)
                else:
                    time.sleep(remaining)
                break
            wait_sec = min(max(1, int(min(remaining, poll_interval * 5))), 30)
            if cancel_event is not None and cancel_event.is_set():
                raise ActionboxError("Actionbox wait was cancelled.", "ABORTED", 0)
            try:
                self.data = self.client.get(
                    self.id,
                    wait_seconds=wait_sec,
                    cancel_event=cancel_event,
                    _deadline=deadline,
                ).data
            except ActionboxError as exc:
                if exc.code == "TIMEOUT" and time.monotonic() >= deadline:
                    break
                raise
            if self.status != "open" or time.monotonic() >= deadline:
                break
        if self.status == "open":
            return None
        # Return a concise string for single-choice Actions and the typed
        # response for boolean, numeric, text, multi-choice, and form inputs.
        return self.decision if self.decision is not None else self.response


class ActionsResource:
    def __init__(self, client: Actionbox) -> None:
        self.client = client

    def create(self, **payload: Any) -> Action:
        return self.client.create(**payload)

    def update(self, action_id: str, **payload: Any) -> Action:
        return self.client.update(action_id, **payload)

    def mark_context_unavailable(
        self,
        action_id: str,
        reason: str,
        reason_code: Literal[
            "not_available", "cannot_access", "not_applicable", "sensitive", "unknown"
        ] = "not_available",
    ) -> Action:
        return self.client.mark_context_unavailable(action_id, reason, reason_code)

    def resolve(
        self,
        action_id: str,
        option_id: str | ResolveInput | TypedResponse | None = None,
        reason: str | None = None,
        *,
        response: TypedResponse | None = None,
        action_version: int | None = None,
        fingerprint: str | None = None,
        approval_override: bool = False,
    ) -> Action:
        return self.client.resolve(
            action_id,
            option_id,
            reason,
            response=response,
            action_version=action_version,
            fingerprint=fingerprint,
        )

    def report_outcome(
        self,
        action_id: str,
        status: Literal["success", "failed"],
        *,
        action_version: int,
        fingerprint: str,
        duration_ms: int | None = None,
        rollback: bool = False,
        reason_code: str | None = None,
    ) -> ActionOutcome:
        return self.client.report_outcome(
            action_id,
            status,
            action_version=action_version,
            fingerprint=fingerprint,
            duration_ms=duration_ms,
            rollback=rollback,
            reason_code=reason_code,
        )

    def report_control_result(
        self,
        control_request_id: str,
        status: Literal["running", "succeeded", "failed"],
        *,
        message: str | None = None,
        reason_code: str | None = None,
    ) -> ControlRequest:
        return self.client.report_control_result(
            control_request_id,
            status,
            message=message,
            reason_code=reason_code,
        )


class Actionbox:
    """Small synchronous client for source-authenticated Actionbox APIs."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.actionbox.cloud",
        timeout: float = 10,
        max_retries: int = 2,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key or any(character.isspace() for character in api_key):
            raise ValueError("api_key must be a non-empty token without whitespace")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if not isinstance(max_retries, int) or not 0 <= max_retries <= 5:
            raise ValueError("max_retries must be an integer from 0 through 5")
        self.base_url = _validate_base_url(base_url)
        self.http = http_client or httpx.Client(timeout=timeout)
        self._owns_http_client = http_client is None
        self.timeout = timeout
        self.max_retries = max_retries
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Actionbox-Client": f"python-sdk/{__version__}",
        }
        self.actions = ActionsResource(self)
        self.watches = WatchesResource(self)
        self.runs = RunsResource(self)

    def close(self) -> None:
        if self._owns_http_client:
            self.http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        allow_retries: bool = True,
    ) -> dict[str, Any] | list[Any]:
        request_headers = {**self.headers, **(headers or {})}
        has_idempotency_key = any(key.lower() == "idempotency-key" for key in request_headers)
        retryable = method.upper() in {"GET", "HEAD", "OPTIONS"} or has_idempotency_key
        attempts = self.max_retries + 1 if retryable and allow_retries else 1

        for attempt in range(attempts):
            _raise_if_cancelled(cancel_event)
            request = self.http.build_request(
                method,
                f"{self.base_url}{path}",
                json=json,
                headers=request_headers,
                timeout=timeout if timeout is not None else self.timeout,
            )
            try:
                response = self.http.send(request, stream=True)
            except httpx.TimeoutException as exc:
                if retryable and attempt + 1 < attempts:
                    _wait_before_retry(attempt, None, cancel_event)
                    continue
                raise ActionboxError("Actionbox request timed out.", "TIMEOUT", 0) from exc
            except httpx.TransportError as exc:
                if retryable and attempt + 1 < attempts:
                    _wait_before_retry(attempt, None, cancel_event)
                    continue
                raise ActionboxError(
                    "Actionbox network request failed.", "NETWORK_ERROR", 0
                ) from exc

            try:
                raw_body = _read_bounded_body(response, cancel_event)
            finally:
                response.close()

            if (
                retryable
                and attempt + 1 < attempts
                and (response.status_code == 429 or response.status_code >= 500)
            ):
                _wait_before_retry(attempt, response.headers.get("Retry-After"), cancel_event)
                continue

            body = _parse_response_body(raw_body, response.status_code)
            if not response.is_success:
                error = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
                detail = body.get("detail") if isinstance(body.get("detail"), str) else None
                raise ActionboxError(
                    error.get("message", detail or "Actionbox request failed."),
                    error.get("code", "UNKNOWN"),
                    response.status_code,
                )
            data = body.get("data", body)
            if not isinstance(data, (dict, list)):
                raise ActionboxError(
                    "Actionbox returned an invalid response envelope.",
                    "INVALID_RESPONSE",
                    response.status_code,
                )
            return data

        raise ActionboxError("Actionbox request failed after retries.", "RETRY_EXHAUSTED", 0)

    def create(
        self,
        *,
        title: str,
        description: str = "",
        options: list[ActionOption] | None = None,
        interaction: TypedInteraction | None = None,
        idempotency_key: str | None = None,
        **payload: Any,
    ) -> Action:
        if options is not None and interaction is not None:
            raise ValueError("provide options or interaction, not both")
        _reject_unknown_fields(payload, _ACTION_CREATE_EXTRA_FIELDS, "Action create input")
        request_headers = {"Idempotency-Key": idempotency_key or f"sdk-{secrets.token_urlsafe(18)}"}
        request_payload: dict[str, Any] = {
            "title": title,
            "description": description,
            **payload,
        }
        if interaction is not None:
            request_payload["interaction"] = interaction
        else:
            request_payload["options"] = options or []
        data = self._request(
            "POST",
            "/v1/actions",
            json=request_payload,
            headers=request_headers,
        )
        return Action(self, data)

    def get(
        self,
        action_id: str,
        *,
        wait_seconds: int = 0,
        cancel_event: threading.Event | None = None,
        _deadline: float | None = None,
    ) -> Action:
        encoded_id = quote(action_id, safe="")
        path = f"/v1/source/actions/{encoded_id}"
        if wait_seconds > 0:
            path = f"{path}?wait_seconds={min(int(wait_seconds), 30)}"
        request_timeout = self.timeout
        if wait_seconds > 0:
            request_timeout = max(request_timeout, min(wait_seconds, 30) + 5)
        if _deadline is not None:
            request_timeout = min(request_timeout, max(0.001, _deadline - time.monotonic()))
        return Action(
            self,
            self._request(
                "GET",
                path,
                cancel_event=cancel_event,
                timeout=request_timeout,
                allow_retries=_deadline is None,
            ),
        )

    def update(self, action_id: str, **payload: Any) -> Action:
        if not payload:
            raise ValueError("provide at least one field to update")
        _reject_unknown_fields(payload, _ACTION_PATCH_FIELDS, "Action update input")
        return Action(
            self,
            self._request(
                "PATCH",
                f"/v1/actions/{quote(action_id, safe='')}",
                json=payload,
            ),
        )

    def mark_context_unavailable(
        self,
        action_id: str,
        reason: str,
        reason_code: Literal[
            "not_available", "cannot_access", "not_applicable", "sensitive", "unknown"
        ] = "not_available",
    ) -> Action:
        return Action(
            self,
            self._request(
                "POST",
                f"/v1/source/actions/{quote(action_id, safe='')}/context-request/unavailable",
                json={"reason": reason, "reason_code": reason_code},
            ),
        )

    def resolve(
        self,
        action_id: str,
        option_id: str | ResolveInput | TypedResponse | None = None,
        reason: str | None = None,
        *,
        response: TypedResponse | None = None,
        action_version: int | None = None,
        fingerprint: str | None = None,
        approval_override: bool = False,
    ) -> Action:
        if isinstance(option_id, Mapping):
            generic_input = dict(option_id)
            if generic_input.get("type") in _TYPED_RESPONSE_TYPES:
                response_fields = (
                    frozenset({"type", "values"})
                    if generic_input["type"] == "form"
                    else frozenset({"type", "value"})
                )
                _reject_unknown_fields(generic_input, response_fields, "Typed response input")
                response = cast(TypedResponse, generic_input)
                option_id = None
            else:
                _reject_unknown_fields(generic_input, _RESOLVE_INPUT_FIELDS, "Resolve input")
                response = cast(TypedResponse | None, generic_input.get("response", response))
                reason = cast(str | None, generic_input.get("reason", reason))
                option_id = cast(str | None, generic_input.get("option_id"))
            action_version = cast(int | None, generic_input.get("action_version", action_version))
            fingerprint = cast(str | None, generic_input.get("fingerprint", fingerprint))
            approval_override = bool(generic_input.get("approval_override", approval_override))

        request_payload: dict[str, Any] = {"option_id": option_id, "reason": reason}
        if action_version is not None:
            request_payload["action_version"] = action_version
        if fingerprint is not None:
            request_payload["fingerprint"] = fingerprint
        if response is not None:
            request_payload["response"] = response
        if approval_override:
            request_payload["approval_override"] = True
        return Action(
            self,
            self._request(
                "POST",
                f"/v1/actions/{quote(action_id, safe='')}/resolve",
                json=request_payload,
            ),
        )

    def cancel(self, action_id: str, reason: str | None = None) -> Action:
        payload = {"reason": reason} if reason is not None else {}
        return Action(
            self,
            self._request("POST", f"/v1/actions/{quote(action_id, safe='')}/cancel", json=payload),
        )

    def report_outcome(
        self,
        action_id: str,
        status: Literal["success", "failed"],
        *,
        action_version: int,
        fingerprint: str,
        duration_ms: int | None = None,
        rollback: bool = False,
        reason_code: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ActionOutcome:
        data = self._request(
            "POST",
            f"/v1/actions/{quote(action_id, safe='')}/outcome",
            json={
                "status": status,
                "duration_ms": duration_ms,
                "rollback": rollback,
                "reason_code": reason_code,
                "action_version": action_version,
                "fingerprint": fingerprint,
            },
            # Exact outcome replays are idempotent server-side, so transport
            # retries are safe even if the first response was lost.
            headers={"Idempotency-Key": f"outcome-{action_id}"},
            cancel_event=cancel_event,
        )
        return cast(ActionOutcome, data)

    def report_control_result(
        self,
        control_request_id: str,
        status: Literal["running", "succeeded", "failed"],
        *,
        message: str | None = None,
        reason_code: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ControlRequest:
        data = self._request(
            "POST",
            f"/v1/source/control-requests/{quote(control_request_id, safe='')}/result",
            json={
                "status": status,
                "message": message,
                "reason_code": reason_code,
            },
            headers={"Idempotency-Key": f"control-result-{control_request_id}-{status}"},
            cancel_event=cancel_event,
        )
        return cast(ControlRequest, data)

    def verify_receipt(
        self,
        receipt: str,
        public_keys: Mapping[str, Any] | list[Mapping[str, Any]] | str | bytes,
        *,
        expected_action_id: str | None = None,
        expected_environment: str | None = "live",
        expected_fingerprint: str | None = None,
        max_age_seconds: float | None = None,
        max_future_skew_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """Verify an Actionbox Ed25519 decision receipt token offline."""
        return verify_decision_receipt(
            receipt,
            public_keys,
            expected_action_id=expected_action_id,
            expected_environment=expected_environment,
            expected_fingerprint=expected_fingerprint,
            max_age_seconds=max_age_seconds,
            max_future_skew_seconds=max_future_skew_seconds,
        )

    def ask(
        self,
        *,
        title: str,
        options: list[str] | None = None,
        interaction: TypedInteraction | None = None,
        wait: bool = True,
        timeout: float = 3600,
        poll_interval: float = 2,
        cancel_event: threading.Event | None = None,
        **payload: Any,
    ) -> str | TypedResponse | Action | None:
        if options is None and interaction is None:
            raise ValueError("provide options or interaction")
        if options is not None and interaction is not None:
            raise ValueError("provide options or interaction, not both")
        normalized = _normalize_option_labels(options) if options is not None else None
        action = self.create(title=title, options=normalized, interaction=interaction, **payload)
        if not wait:
            return action
        created_binding = (action.action_version, action.fingerprint)
        created_content = action.data.get("content_fingerprint")
        created_policy = action.approval_policy
        result = action.wait(
            timeout=timeout, poll_interval=poll_interval, cancel_event=cancel_event
        )
        same_binding = bool(created_binding[1]) and (
            action.action_version, action.fingerprint
        ) == created_binding
        same_content = bool(created_content) and action.data.get("content_fingerprint") == created_content
        policy_resolution = bool(created_policy and action.approval_policy) and (
            same_binding or same_content
        ) and (
            action.resolved_by == "user"
            or (
                action.resolved_by == "source"
                and created_policy.get("allow_source_override") is True
                and action.approval_policy.get("allow_source_override") is True
            )
        )
        if action.status == "resolved" and not policy_resolution and (
            not same_binding or action.resolved_by != "user"
        ):
            raise ActionboxError(
                "The Action was not resolved by a human against the originally created request.",
                "APPROVAL_BINDING_MISMATCH",
                409,
            )
        return result


def _normalize_option_labels(labels: list[str]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for label in labels:
        option_id = re.sub(r"[^a-z0-9_-]+", "-", label.strip().lower()).strip("-")
        if not option_id:
            raise ValueError("option labels must not be empty")
        if option_id in seen:
            raise ValueError(f"option labels must produce unique IDs; duplicate ID: {option_id}")
        seen.add(option_id)
        normalized.append({"id": option_id, "label": label})
    return normalized


def send_heartbeat(
    heartbeat_url: str,
    signal: Literal["ping", "start", "success", "fail"] = "ping",
    *,
    timeout: float = 10,
    cancel_event: threading.Event | None = None,
    http_client: httpx.Client | None = None,
) -> None:
    """Deliver one Watch heartbeat without placing its capability in errors."""
    _raise_if_cancelled(cancel_event)
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if signal not in {"ping", "start", "success", "fail"}:
        raise ValueError("signal must be ping, start, success, or fail")
    parsed = urlsplit(heartbeat_url.strip())
    segments = [part for part in parsed.path.split("/") if part]
    if (
        len(segments) != 2
        or segments[0] != "hb"
        or not segments[1].startswith("hb_")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
    ):
        raise ValueError("heartbeat_url must be an Actionbox heartbeat URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        loopback = address.is_loopback
    except ValueError:
        loopback = parsed.hostname.casefold() == "localhost"
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("heartbeat_url must use HTTPS unless it targets localhost")
    base_path = parsed.path.rstrip("/")
    path = base_path if signal == "ping" else f"{base_path}/{signal}"
    url = f"{parsed.scheme}://{parsed.netloc}{path}"
    client = http_client or httpx.Client(timeout=timeout)
    owns_client = http_client is None
    try:
        try:
            response = client.post(url, timeout=timeout)
        except httpx.HTTPError as exc:
            raise ActionboxError("Heartbeat delivery failed.", "HEARTBEAT_FAILED", 0) from exc
    finally:
        if owns_client:
            client.close()
    _raise_if_cancelled(cancel_event)
    if response.status_code != 204:
        raise ActionboxError(
            f"Heartbeat delivery failed with HTTP {response.status_code}.",
            "HEARTBEAT_FAILED",
            response.status_code,
        )


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("base_url must be a full HTTPS URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        loopback = address.is_loopback
    except ValueError:
        loopback = parsed.hostname.lower() == "localhost"
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("base_url must use HTTPS unless it targets localhost")
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "base_url must not contain credentials, a path, query parameters, or a fragment"
        )
    port = f":{parsed.port}" if parsed.port is not None else ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}{port}"


def _read_bounded_body(
    response: httpx.Response,
    cancel_event: threading.Event | None = None,
) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        _raise_if_cancelled(cancel_event)
        size += len(chunk)
        if size > _MAX_RESPONSE_BYTES:
            raise ActionboxError(
                f"Actionbox response exceeds {_MAX_RESPONSE_BYTES} bytes.",
                "RESPONSE_TOO_LARGE",
                response.status_code,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_response_body(raw: bytes, status: int) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ActionboxError(
            "Actionbox returned invalid JSON.", "INVALID_RESPONSE", status
        ) from exc
    if not isinstance(body, dict):
        raise ActionboxError(
            "Actionbox returned an invalid response envelope.",
            "INVALID_RESPONSE",
            status,
        )
    return cast(dict[str, Any], body)


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return min(max(seconds, 0.0), _MAX_RETRY_DELAY)
    except ValueError:
        pass
    try:
        timestamp = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return min(max((timestamp - datetime.now(UTC)).total_seconds(), 0.0), _MAX_RETRY_DELAY)


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ActionboxError("Actionbox request was cancelled.", "ABORTED", 0)


def _wait_before_retry(
    attempt: int,
    retry_after: str | None,
    cancel_event: threading.Event | None,
) -> None:
    server_delay = _retry_after_seconds(retry_after)
    base = 0.25 * (2**attempt)
    delay = server_delay if server_delay is not None else base + random.uniform(0, base / 2)
    if cancel_event is not None:
        if cancel_event.wait(delay):
            raise ActionboxError("Actionbox request was cancelled.", "ABORTED", 0)
    else:
        time.sleep(delay)


def _b64url_decode(segment: str) -> bytes:
    if not segment or re.fullmatch(r"[A-Za-z0-9_-]+", segment) is None:
        raise ValueError("invalid base64url value")
    if len(segment) % 4 == 1:
        raise ValueError("invalid base64url value")
    padding = "=" * ((4 - len(segment) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{segment}{padding}".encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid base64url value") from exc
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if canonical != segment:
        raise ValueError("non-canonical base64url value")
    return decoded


def _receipt_public_key_bytes(value: str) -> bytes:
    try:
        return _b64url_decode(value)
    except ValueError as exc:
        raise ReceiptVerificationError("Public key contains invalid base64url data.") from exc


class ReceiptVerificationError(ValueError):
    """Raised when an Actionbox decision receipt fails cryptographic or claims verification."""


def verify_decision_receipt(
    receipt: str,
    public_keys: Mapping[str, Any] | list[Mapping[str, Any]] | str | bytes,
    *,
    expected_action_id: str | None = None,
    expected_environment: str | None = "live",
    expected_fingerprint: str | None = None,
    max_age_seconds: float | None = None,
    max_future_skew_seconds: float = 60.0,
) -> dict[str, Any]:
    """Verify an Actionbox Ed25519 decision receipt token offline.

    Validates signature, compact JWT structure, algorithm headers, and claims.
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise ImportError(
            "Decision receipt verification requires the 'cryptography' library. "
            "Install it with: pip install 'actionbox-sdk[crypto]'"
        ) from exc

    if not isinstance(receipt, str) or receipt.count(".") != 2:
        raise ReceiptVerificationError("Receipt must contain exactly three compact segments.")

    encoded_header, encoded_payload, encoded_signature = receipt.split(".")
    try:
        header_raw = _b64url_decode(encoded_header)
        payload_raw = _b64url_decode(encoded_payload)
        signature = _b64url_decode(encoded_signature)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ReceiptVerificationError(f"Receipt has invalid base64url encoding: {exc}") from exc

    if len(signature) != 64:
        raise ReceiptVerificationError("Ed25519 signatures must be exactly 64 bytes.")

    try:
        header = json.loads(header_raw.decode("utf-8"))
        payload = json.loads(payload_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptVerificationError(f"Receipt contains invalid JSON: {exc}") from exc

    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise ReceiptVerificationError("Receipt header and payload must be JSON objects.")

    if (
        header.get("alg") != "EdDSA"
        or header.get("typ") != "actionbox-decision-receipt"
        or header.get("v") not in {1, 2}
    ):
        raise ReceiptVerificationError("Receipt has an unsupported or invalid header.")

    if payload.get("iss") != "actionbox" or payload.get("receipt_version") not in {1, 2}:
        raise ReceiptVerificationError("Receipt payload has an invalid issuer or version.")

    if payload.get("status") != "resolved":
        raise ReceiptVerificationError("Receipt must have status 'resolved'.")

    required_claims = {
        "action_id",
        "environment",
        "version",
        "action_version",
        "fingerprint",
        "resolved_at",
    }
    if not required_claims <= payload.keys():
        raise ReceiptVerificationError("Receipt payload is incomplete.")
    if header.get("v") != payload.get("receipt_version"):
        raise ReceiptVerificationError("Receipt header and payload versions do not match.")
    if payload.get("receipt_version") == 2 and not {
        "approval_policy", "approval_progress", "resolution_method"
    } <= payload.keys():
        raise ReceiptVerificationError("Approval receipt payload is incomplete.")

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise ReceiptVerificationError("Receipt key id is missing or invalid.")
    key_bytes: bytes | None = None
    key_set: Mapping[str, Any] | list[Mapping[str, Any]] | str | bytes = public_keys

    if isinstance(key_set, bytes):
        key_bytes = key_set
    elif isinstance(key_set, str):
        if key_set.strip().startswith("{"):
            try:
                parsed_keys = json.loads(key_set)
            except json.JSONDecodeError as exc:
                raise ReceiptVerificationError("Public key set contains invalid JSON.") from exc
            if not isinstance(parsed_keys, dict) or not isinstance(parsed_keys.get("keys"), list):
                raise ReceiptVerificationError("Public key set must be a JWKS object.")
            key_set = parsed_keys["keys"]
        else:
            key_bytes = _receipt_public_key_bytes(key_set)

    if isinstance(key_set, Mapping) and isinstance(key_set.get("keys"), list):
        key_set = key_set["keys"]

    if key_bytes is None and isinstance(key_set, list):
        for item in key_set:
            if isinstance(item, Mapping) and item.get("kid") == kid:
                if item.get("kty") != "OKP" or item.get("crv") != "Ed25519":
                    raise ReceiptVerificationError(f"Public key '{kid}' is not an Ed25519 JWK.")
                x_val = item.get("x")
                if isinstance(x_val, str):
                    key_bytes = _receipt_public_key_bytes(x_val)
                    break

    if key_bytes is None and isinstance(key_set, Mapping) and kid in key_set:
        val = key_set[kid]
        key_bytes = _receipt_public_key_bytes(val) if isinstance(val, str) else bytes(val)

    if not key_bytes or len(key_bytes) != 32:
        raise ReceiptVerificationError(
            f"No valid 32-byte Ed25519 public key found for kid '{kid}'."
        )

    try:
        verifier = Ed25519PublicKey.from_public_bytes(key_bytes)
        verifier.verify(signature, f"{encoded_header}.{encoded_payload}".encode("ascii"))
    except InvalidSignature as exc:
        raise ReceiptVerificationError("Receipt cryptographic signature is invalid.") from exc

    if expected_action_id is not None and payload.get("action_id") != expected_action_id:
        raise ReceiptVerificationError(
            f"Receipt action_id '{payload.get('action_id')}' does not match expected '{expected_action_id}'."
        )

    if expected_environment is not None and payload.get("environment") != expected_environment:
        raise ReceiptVerificationError(
            f"Receipt environment '{payload.get('environment')}' does not match expected '{expected_environment}'."
        )

    if expected_fingerprint is not None and payload.get("fingerprint") != expected_fingerprint:
        raise ReceiptVerificationError("Receipt fingerprint does not match expected.")

    if not math.isfinite(max_future_skew_seconds) or max_future_skew_seconds < 0:
        raise ValueError("max_future_skew_seconds must be a finite non-negative number")
    resolved_at_str = payload.get("resolved_at")
    if not isinstance(resolved_at_str, str) or not resolved_at_str:
        raise ReceiptVerificationError("Receipt is missing resolved_at timestamp.")
    try:
        resolved_at = datetime.fromisoformat(resolved_at_str.replace("Z", "+00:00"))
        if resolved_at.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ReceiptVerificationError(
            f"Invalid resolved_at timestamp '{resolved_at_str}': {exc}"
        ) from exc
    now = datetime.now(UTC)
    age = (now - resolved_at).total_seconds()
    if age < -max_future_skew_seconds:
        raise ReceiptVerificationError(
            f"Receipt resolved_at is too far in the future ({-age:.1f}s)."
        )
    if max_age_seconds is not None:
        if not math.isfinite(max_age_seconds) or max_age_seconds < 0:
            raise ValueError("max_age_seconds must be a finite non-negative number")
        if age > max_age_seconds:
            raise ReceiptVerificationError(
                f"Receipt has expired (age {age:.1f}s > max {max_age_seconds}s)."
            )

    return payload


__all__ = [
    "Action",
    "ActionContextRequest",
    "ActionControl",
    "ActionCreateInput",
    "ActionOption",
    "ActionOptionInput",
    "ActionOutcome",
    "ActionPatchInput",
    "ApprovalPolicy",
    "ApprovalProgress",
    "ApprovalVote",
    "Actionbox",
    "ActionboxError",
    "ActionsResource",
    "AgentRun",
    "BooleanInteraction",
    "BooleanResponse",
    "ControlRequest",
    "DecisionContext",
    "DecisionReversibility",
    "DecisionRiskLevel",
    "FormBooleanField",
    "FormField",
    "FormIntegerField",
    "FormInteraction",
    "FormMultiChoiceField",
    "FormNumberField",
    "FormRatingField",
    "FormResponse",
    "FormSingleChoiceField",
    "FormTextField",
    "IntegerInteraction",
    "IntegerResponse",
    "Interaction",
    "InteractionResponse",
    "MultiChoiceInteraction",
    "MultiChoiceResponse",
    "NumberInteraction",
    "NumberResponse",
    "OptionStyle",
    "RatingInteraction",
    "RatingResponse",
    "ReceiptVerificationError",
    "ResolveInput",
    "Response",
    "RunStatus",
    "RunsResource",
    "SingleChoiceInteraction",
    "SingleChoiceResponse",
    "TextInteraction",
    "TextResponse",
    "TypedInteraction",
    "TypedResponse",
    "Watch",
    "WatchCreateInput",
    "WatchScheduleType",
    "WatchSignalMethod",
    "WatchStatus",
    "WatchesResource",
    "__version__",
    "access_request_decision_context",
    "control",
    "database_change_decision_context",
    "decision_context",
    "deployment_decision_context",
    "generic_decision_context",
    "link",
    "refund_decision_context",
    "send_heartbeat",
    "verify_decision_receipt",
]
