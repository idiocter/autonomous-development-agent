# Autonomous Software Development Agent

An AI agent that resolves GitHub issues end to end: it reads the issue, understands the
codebase, plans a fix, writes the code, runs the tests in an isolated sandbox, debugs
failures, and opens a pull request.

## Features

- **Full issue-to-PR loop** — Planner, Coding, Testing, and Debugging agents built on LangGraph
- **Isolated test execution** — code runs in a resource-limited, network-disabled Docker sandbox
- **Codebase-grounded** — retrieval-augmented generation (RAG) over the target repo so changes are based on real existing code, not guesses
- **Real GitHub integration** — branch/commit/push, idempotent PR creation, and a webhook server that triggers on a labeled issue
- **Loop-safety guards** — iteration cap, repeated-failure detection, cost budget, and wall-clock timeout, so a stuck run escalates to a human instead of looping forever
- **Won't touch your tests** — the agents are instructed never to edit test files, and any test change that slips through is flagged in the PR body for review
- **Cost visibility** — every run prints a per-model token and cost breakdown
- **Job persistence** — plans, diffs, test results, and cost are recorded in Postgres

## Tech stack

Python, LangGraph, FastAPI, Anthropic API, Docker, PostgreSQL + pgvector, SQLAlchemy/Alembic, PyGithub.

> An `openai` branch runs the same system on GPT models. Only the LLM layer differs.

## Setup

Requires Docker Desktop running.

```bash
uv sync --extra dev --extra github --extra db --extra sandbox --extra rag --extra api
cp .env.example .env
# add ANTHROPIC_API_KEY; GITHUB_TOKEN too if you want GitHub-backed runs

docker-compose up -d postgres          # host port 5433
uv run alembic upgrade head

docker build -f docker/Dockerfile.sandbox -t autonomous-dev-agent-sandbox:latest docker/
```

## Usage

### Fix a bug in a local repo

No GitHub required. Prints the plan, per-attempt test results, the resulting diff, and a
token/cost table.

```bash
uv run python scripts/run_local_job.py \
  --repo path/to/repo \
  --issue "Description of the bug or feature"
```

Optional: `--max-iterations N` to override the debug-loop cap for a single run.

### Resolve a real GitHub issue and open a PR

Needs `GITHUB_TOKEN`. Clones the repo, works on a dedicated `agent/issue-N-<jobid>` branch,
and opens a PR — it never pushes to your default branch and never auto-merges.

```bash
uv run python scripts/run_github_job.py --repo owner/repo --issue 42
```

Point this at a repo you control until you trust it.

### Run the webhook server

```bash
uv run uvicorn src.api.main:app --reload
```

| Endpoint | Purpose |
|---|---|
| `POST /webhooks/github` | HMAC-verified issue events; fires when an issue gets the `agent:work-on-it` label |
| `GET /jobs` | list recent jobs |
| `GET /jobs/{id}` | one job's status, PR URL, and cost |
| `GET /jobs/{id}/events` | live progress stream (SSE) |
| `GET /health` | liveness |

For local development, relay real GitHub deliveries with ngrok or smee.io pointed at
`localhost:8000/webhooks/github`, and set `GITHUB_WEBHOOK_SECRET` to match.

### Index a repo into the vector store

Normally automatic, but useful to pre-warm or inspect:

```bash
uv run python scripts/index_repo.py --repo path/to/repo --repo-url owner/repo
```

## Configuration

All via `.env` — no code changes needed.

| Variable | Default | Effect |
|---|---|---|
| `PLANNER_MODEL` / `CODER_MODEL` / `TESTING_MODEL` / `DEBUGGER_MODEL` | Opus / Sonnet | per-agent model choice |
| `MAX_ITERATIONS` | `6` | max debug→code→test cycles before escalating |
| `JOB_COST_BUDGET_USD` | `2.00` | hard spend cap; breaching it aborts and escalates |
| `JOB_TIMEOUT_SECONDS` | `2700` | wall-clock kill switch |
| `SANDBOX_TIMEOUT_SECONDS` | `300` | per-command timeout inside the sandbox |

Required GitHub token permissions: **Contents** (read/write), **Pull requests** (read/write),
**Issues** (read/write). Issues write is what lets the agent comment a PR link back and apply
the `agent:needs-human` label when it escalates — without it, escalation silently no-ops.

## Tests

```bash
uv run pytest
```

Needs the Postgres container running and the sandbox image built for the full suite;
Docker-dependent tests skip gracefully if Docker isn't available.
