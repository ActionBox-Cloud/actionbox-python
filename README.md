# ActionBox Python SDK

Python client for the hosted [ActionBox](https://actionbox.cloud) API.
ActionBox lets software ask a human for a decision and continue after the response.

## Install

Requires Python 3.11 or newer.

```sh
python -m pip install actionbox-sdk
```

The distribution name is `actionbox-sdk`; the import name is `actionbox`.
Create a Source in ActionBox and store its key in `ACTIONBOX_API_KEY`.

```python
import os
from actionbox import Actionbox

with Actionbox(os.environ["ACTIONBOX_API_KEY"]) as client:
    decision = client.ask(
        title="Proceed with the operation?",
        options=["Approve", "Reject"],
    )
    print(decision)
```

The client uses `https://api.actionbox.cloud`. Keep Source keys in trusted
server environments and out of source control.

## Development

Install [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync --frozen --extra crypto
uv run --frozen python -m unittest discover -s tests -v
uv build
```

## Documentation and contributions

- [ActionBox documentation](https://actionbox.cloud/docs)
- [Contributing](CONTRIBUTING.md)
- [Report a vulnerability](SECURITY.md)

## License

This client is MIT licensed; see [LICENSE](LICENSE).
ActionBox is proprietary hosted software. This repository contains its client.
