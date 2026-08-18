# Autonomous Software Development Agent

An AI agent that resolves GitHub issues end to end: it reads the issue, understands the
codebase, plans a fix, writes the code, runs the tests in an isolated sandbox, debugs
failures, and opens a pull request.

## Features

- **Full issue-to-PR loop** — Planner, Coding, Testing, and Debugging agents built on LangGraph
- **Isolated test execution** — code runs in a resource-limited, network-disabled Docker sandbox
- **Codebase-grounded** — retrieval-augmented generation (RAG) over the target repo so changes are based on real existing code, not guesses
- **Real GitHub integration** — branch/commit/push, idempotent PR creation, and a webhook server that triggers on a labeled issue
- **Loop-safety guards** — iteration cap, repeated-failure detection, cost-budget tracking, and wall-clock timeout, so a stuck run escalates to a human instead of looping forever
- **Job persistence** — every run's plan, diffs, test results, and cost are recorded in Postgres

## Tech stack

Python, LangGraph, FastAPI, Anthropic API, Docker, PostgreSQL + pgvector, SQLAlchemy/Alembic, PyGithub.

## Setup

```bash
uv sync --extra dev --extra github --extra db --extra sandbox --extra rag --extra api
cp .env.example .env
# add ANTHROPIC_API_KEY at minimum; GITHUB_TOKEN for GitHub-backed runs

docker-compose up -d postgres
uv run alembic upgrade head

docker build -f docker/Dockerfile.sandbox -t autonomous-dev-agent-sandbox:latest docker/
```

## Usage

Run against a local repo:

```bash
uv run python scripts/run_local_job.py --repo path/to/repo --issue "Description of the bug or feature"
```

Run against a real GitHub issue (opens a real PR — point this at a repo you control):

```bash
uv run python scripts/run_github_job.py --repo owner/repo --issue 42
```

Run the webhook server:

```bash
uv run uvicorn src.api.main:app --reload
```

## Tests

```bash
uv run pytest
```
