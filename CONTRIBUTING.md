# Contributing

Issues and pull requests are welcome. Include a small reproduction and tests
for behavior changes. Never include API keys, Watch URLs, or customer data.

This repository receives maintained source snapshots. Maintainers incorporate
accepted patches into the canonical source and synchronize the resulting
changes here, preserving contributor credit. A pull request may therefore be
closed with a reference to the synchronized commit instead of merged directly.

Run the checks below for the client you are changing. Tests use fixtures or
local mock servers and do not require an ActionBox account.

## Python SDK

Install [uv](https://docs.astral.sh/uv/), then run from the repository root:

```sh
uv sync --frozen --extra crypto
uv run --frozen python -m unittest discover -s tests -v
uv build
```

## JavaScript / TypeScript SDK

Use a currently supported Node.js LTS release:

```sh
npm ci --ignore-scripts
npm test
npm run typecheck
npm pack
```

## CLI

Use the Go version declared in `go.mod` or newer:

```sh
go test ./...
go test -race ./...
go vet ./...
go build -trimpath -o actionbox ./cmd/actionbox
./actionbox --help
```

The `actionbox_pkg` directory contains the Python launcher used to distribute
the Go executable. Local Go development does not require that launcher.
