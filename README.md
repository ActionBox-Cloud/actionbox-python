<img src="https://actionbox.cloud/appbox.svg" width="64" alt="Actionbox logo">

# Actionbox Python SDK

Actionbox gives backend services a durable, server-authoritative way to ask a
human for a decision and continue when that decision is available. This package
is the typed Python client for creating, resolving, and waiting on Actions,
plus managing source-scoped heartbeat Watches.

## Documentation

- [Actionbox documentation](https://actionbox.cloud/docs)

## Requirements

- Python 3.11 or newer
- An Actionbox Source API key, supplied through `ACTIONBOX_API_KEY`

Keep API keys and Watch capability URLs on trusted servers, workers, or CI
jobs. Do not put this SDK or its credentials in browser code.

## Install

```bash
pip install actionbox-sdk
```

The distribution is named `actionbox-sdk` so it does not conflict with the
Actionbox CLI distribution on PyPI. The Python import remains `actionbox`.

```python
import os
from actionbox import Actionbox

with Actionbox(os.environ["ACTIONBOX_API_KEY"]) as client:
    decision = client.ask(
        title="Deploy to production?",
        options=["Approve", "Reject"],
        assignee_email="reviewer@example.com",
        callback_url="https://ci.example.com/actionbox",
    )
    print(decision)
```

Use `assignee_email` for human-managed configuration. Actionbox resolves it
only among active reviewers in the Source workspace. Stable integrations may
use `assignee_user_id` instead; the ID is copyable from the Team settings
page. Do not send both fields.

Team integrations can restrict discovery to several reviewers:

```python
client.create(
    title="Approve emergency access",
    visibility="restricted",
    reviewer_emails=["incident@example.com", "security@example.com"],
)
```

Use either `reviewer_emails` or `reviewer_user_ids`, with at most 15 members.

The SDK uses the hosted production API at `https://api.actionbox.cloud` by
default. Customer integrations should use that default and only pass
`base_url` in maintainer-controlled test environments.

`ask(..., wait=False)` returns an `Action`; `Action.wait()` polls the server and leaves the Action open when the local timeout expires. The concise single-choice API returns the selected option ID as a string.

## Typed interactions and responses

The SDK exports typed interaction and response contracts that match the REST API. Use an explicit typed interaction with `create` or `ask` when the human response is more than a single choice:

```python
from actionbox import Actionbox, BooleanInteraction

with Actionbox(os.environ["ACTIONBOX_API_KEY"]) as client:
    action = client.create(
        title="Deploy configuration",
        interaction=BooleanInteraction(
            type="boolean",
            label="Deploy now?",
            true_label="Deploy",
            false_label="Hold",
        ),
    )
    resolved = client.resolve(
        action.id,
        response={"type": "boolean", "value": True},
        reason="Approved by release manager",
    )
    print(resolved.response)  # {"type": "boolean", "value": True}
```

The available interaction types are `boolean`, `single_choice`, `multi_choice`, `text`, `integer`, `number`, `rating`, and `form`. Form fields use the same typed field shapes and are returned as `{"type": "form", "values": {...}}`. Create inputs also accept bounded developer `context` blocks and an explicit typed `on_expire` fallback; omitting it returns `expired` without inventing a response.

`resolve` supports concise single-choice syntax and generic typed input:

```python
client.resolve(action.id, "approve")  # single-choice shorthand
client.resolve(action.id, {"response": {"type": "text", "value": "ship"}})
client.actions.resolve(action.id, response={"type": "number", "value": 4.5})
```

`Action.interaction` and `Action.response` expose the canonical typed wire values. `options`, `option_id`, `ask(..., options=[...])`, and string decision results are first-class single-choice conveniences.

## Optional decision context

Keep simple Actions unchanged. For higher-impact reviews, use a helper that
builds the same generic structured context accepted by the REST API:

```python
from actionbox import deployment_decision_context

action = client.create(
    title="Deploy 2.18.0?",
    decision_class="production_deployment",
    decision_context=deployment_decision_context(
        reason="Release passed staging.",
        proposed_change="Deploy 2.18.0 to production.",
        risk_level="high",
        reversibility="reversible",
        rollback_plan="Restore the previous image.",
    ),
)
```

The generic, refund, database-change, and access-request helpers emit this same
wire shape; they do not create server-side template types.

If `action.context_request` is present, a reviewer has asked the Source for
more detail. Update the same Action rather than creating another one:

```python
client.update(
    action.id,
    decision_context=deployment_decision_context(
        reason="The reviewer requested the operational risk.",
        proposed_change="Deploy 2.18.0 to production.",
        risk_level="high",
        reversibility="reversible",
        rollback_plan="Restore the previous image.",
    ),
)
```

If the Source cannot truthfully supply it, close the request explicitly:

```python
client.mark_context_unavailable(
    action.id,
    "Production customer data is not accessible to this worker.",
    "cannot_access",
)
```

## Agent framework integrations

The OpenAI Agents SDK and LangGraph keep their own paused run state; Actionbox
supplies the durable human request and typed response. The documentation
covers both patterns:

- OpenAI Agents: map `result.interruptions` to Actions, apply each decision to
  `result.to_state()`, then resume the original agent.
- LangGraph: create Actions after interrupts surface to the graph driver, then
  resume the same checkpoint and `thread_id` with `Command(resume=...)`.

See the [OpenAI Agents SDK guide](https://actionbox.cloud/docs/openai-agents)
and [LangGraph guide](https://actionbox.cloud/docs/langgraph). No additional
Actionbox endpoint or framework-owned state migration is required.

## Execution outcomes

After carrying out an approved operation, report its real result from the
resolved Action snapshot:

```python
outcome = resolved.report_outcome(
    "success",
    duration_ms=48_312,
    rollback=False,
)
```

The SDK sends the Action's exact version and fingerprint. Exact retries are
safe; Actionbox rejects a conflicting second outcome.

## Action Controls

Paid plans can attach a few secondary operations to an Action. A control does
not answer or close the Action. It asks the Source to do something, such as
retrying a job, while the reviewer keeps the original decision open.

```python
from actionbox import control, link

action = client.create(
    title="Deployment failed",
    callback_url="https://ci.example.com/actionbox",
    controls=[
        control("retry", "Retry deployment"),
        control("rollback", "Roll back", destructive=True),
        link("logs", "Open logs", "https://ci.example.com/runs/4821"),
    ],
)
```

The signed `action.control_requested` webhook includes a
`control_request_id`. Report the operation state with the Source client:

```python
client.report_control_result(
    control_request_id,
    "running",
    message="Retry started",
)
client.report_control_result(
    control_request_id,
    "succeeded",
    message="Deployment recovered",
)
```

Use `failed` when the operation does not complete. ActionBox records the result
without resolving the parent Action. During staged rollout, the API may return
`CONTROLS_DISABLED` until the feature is enabled for the paid workspace.
ActionBox never runs an infrastructure command itself. Your callback handler
maps each control key to an operation and reports the result. A later Watch
heartbeat or meaningful Agent Run update can add independent recovery evidence.
That evidence does not replace the Source's reported result.

## Agent Runs

Group an agent task and its Actions without managing another framework:

```python
run = client.runs.start(
    external_id="checkout-fix-42",
    agent_name="codex",
    title="Fix checkout deadlock",
    stall_after_seconds=900,
)
run.progress(stage="tests", checkpoint="test-184")
action = client.create(
    title="Approve staging migration",
    run_id=run.id,
    callback_url="https://agent.example.com/actionbox",
    controls=[control("resume", "Resume run")],
)
run.complete()
```

The SDK handles progress sequence numbers. Runs are optional; standalone
Actions continue to work exactly as before. When `stall_after_seconds` is set,
unchanged status/stage/checkpoint updates do not reset the timer. Actionbox
creates one ordinary Action if progress stalls and resolves it when progress
changes or the Run completes; `waiting` pauses the timer. Meaningful progress
or completion also verifies the latest delivered control on an Action linked
to that Run.

## Heartbeat Watches

Source credentials can create and list Watches scoped to that Source. The raw heartbeat URL is returned only by creation:

```python
from actionbox import Actionbox, send_heartbeat

with Actionbox(os.environ["ACTIONBOX_API_KEY"]) as client:
    watch = client.watches.create(
        name="Nightly backup",
        schedule_type="interval",
        interval_seconds=3600,
        grace_seconds=60,
        signal_method="post",
    )
    send_heartbeat(watch.heartbeat_url, "start")
```

Watch creation automatically uses a secure idempotency key. If your application
retries the whole call, reuse a caller-owned key with
`client.watches.create(idempotency_key="watch-nightly-backup-v1", ...)`.
For a recurring heartbeat loop, pass a long-lived `httpx.Client` through the
`http_client` argument so connections can be reused.

Use `send_heartbeat` for `ping`, `start`, `success`, or `fail`; it always sends
POST. `signal_method="post"` prevents link previewers and security scanners
from accidentally recording a heartbeat with GET. The
source-scoped resource also exposes `client.watches.pause(id)`,
`resume(id)`, `rotate_token(id)`, and `archive(id)`; only create/rotate return
a raw URL. Store capability URLs in a secret manager; Watch details and
exports never return them.
When Action Controls are enabled for a paid workspace, a Watch incident also
offers `Skip this occurrence` and `Pause monitoring`. Skipping closes only the
current incident and advances the Watch schedule. A later healthy signal can
independently verify a delivered control request.

## Verify decision receipts

Resolved Actions include an Ed25519-signed receipt. Install the optional crypto
extra, fetch ActionBox's public key set, and bind verification to the Action you
expected:

```bash
python -m pip install "actionbox-sdk[crypto]"
```

```python
import httpx

keys = httpx.get(
    "https://api.actionbox.cloud/.well-known/actionbox-receipt-keys.json"
).json()
claims = client.verify_receipt(
    action.receipt,
    keys,
    expected_action_id=action.id,
    expected_environment="live",
    expected_fingerprint=action.fingerprint,
    max_age_seconds=300,
)
```

Cache the public key set according to its response headers and refresh it when
verification encounters a new key ID.
The verifier always validates the timezone-aware `resolved_at` timestamp and
rejects receipts beyond the future-clock-skew tolerance. `max_age_seconds` is
optional and adds an upper age limit when your workflow needs one.

## License

MIT. See [LICENSE](./LICENSE).
