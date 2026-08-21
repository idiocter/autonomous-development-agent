# Autonomous Software Development Agent

An AI agent that fixes bugs in your code by itself and opens a pull request for you to review.

You give it a GitHub issue. It reads your codebase, works out a fix, writes the code, runs
your tests, debugs if they fail, and opens a PR. If it can't solve the problem, it stops and
asks a human instead of trying forever.

## What it does, step by step

1. **Downloads your repo** and reads through the codebase
2. **Finds the relevant code** so it changes real functions instead of guessing
3. **Makes a plan** — which files to change and how
4. **Writes the code**
5. **Runs your tests** inside a sealed Docker container (no internet, limited memory and CPU)
6. **Fixes and retries** if tests fail, reading the error to work out what went wrong
7. **Opens a pull request** on its own branch — it never merges anything itself
8. **Gives up and tags a human** if it can't get the tests passing

It cannot edit your tests to make them pass — the write tools refuse test files outright, so
an issue that asks for it gets an error rather than a weakened test suite. It stops if it
spends too much money or takes too long. Issue text is passed to the model as data rather
than instructions, and anything in it that looks like an injection attempt is flagged in the
pull request for whoever reviews it.

## Before you start

You need three things:

| | Why |
|---|---|
| **Docker Desktop** — open and running | tests run inside a container; the database runs in one too |
| **uv** | installs Python packages (`curl -LsSf https://astral.sh/uv/install.sh \| sh`) |
| **An Anthropic API key** | the AI that does the thinking |

A GitHub token is optional — you only need it if you want the agent to work on real GitHub
issues rather than folders on your computer.

## Quick version

If you have `make`, these are all you need:

```bash
make install      # install packages
make up           # start the database, build the test container
make demo         # watch it fix a bug in the practice repo
```

Then `make` on its own lists every command. The longer form is below if you'd rather run the
steps yourself.

## Setup — do this once

**1. Install the Python packages**

```bash
uv sync --extra dev --extra github --extra db --extra sandbox --extra rag --extra api
```

**2. Add your keys**

```bash
cp .env.example .env
```

Open `.env` and fill in:

```
ANTHROPIC_API_KEY=sk-ant-...      # required
GITHUB_TOKEN=github_pat_...       # only needed for GitHub issues
```

**3. Start the database**

```bash
docker-compose up -d postgres
uv run alembic upgrade head
```

**4. Build the test container**

```bash
docker build -f docker/Dockerfile.sandbox -t autonomous-dev-agent-sandbox:latest docker/
```

Setup is done.

## How to run it

### Fix a bug in a folder on your computer

Good for trying it out. Nothing is sent to GitHub.

```bash
make demo
```

Or point it at any folder:

```bash
uv run python scripts/run_local_job.py \
  --repo path/to/your/project \
  --issue "Describe the bug here"
```

You'll see the plan it made, whether the tests passed, the exact code change, and what it cost.
Your original folder is never touched — it works on a copy inside `workspaces/`.

### Fix a real GitHub issue and open a PR

```bash
make fix REPO=idiocter/agent-sandbox ISSUE=4
```

Replace the repo with your own and `4` with your issue number.
**Use a test repo until you trust it.**

### Start the web server

```bash
make serve
```

Then open these in your browser:

| Address | What you get |
|---|---|
| http://localhost:8000/docs | clickable list of everything the server can do |
| http://localhost:8000/jobs | every job it has run |
| http://localhost:8000/health | check the server is alive |

There is no home page — `http://localhost:8000/` shows "404 Not Found". That's normal.

## Common problems

**`404 Not Found` when running on a GitHub issue**
You used `owner/repo` literally. Put in your real username and repo name, like
`idiocter/agent-sandbox`.

**`404 Not Found` at http://localhost:8000/**
Normal — there's no home page. Use `/docs` instead.

**`Connect call failed ... 5433`**
The database isn't running. Start it with `docker-compose up -d postgres`.
The agent still works without it — just with weaker code search and no saved history.

**`Cannot connect to the Docker daemon`**
Docker Desktop isn't open. Start it and wait for the whale icon to settle.

**The agent opened a PR but didn't comment on the issue**
Your GitHub token is missing the **Issues → Read and write** permission. PRs still work, but
the agent can't comment or tag issues when it needs human help.

**An error about `anthropic` or `openai` not being installed**
You switched git branches. Re-run the `uv sync` command from setup — the two branches use
different AI providers.

## Settings you can change

All in `.env`. No code changes needed.

| Setting | Default | What it does |
|---|---|---|
| `ALLOW_TEST_EDITS` | `false` | lets the agent write to test files. Leave off for anything triggered by an issue a stranger can author |
| `MAX_ITERATIONS` | `6` | how many times it retries a fix before giving up |
| `JOB_COST_BUDGET_USD` | `2.00` | stops the job if it spends more than this |
| `JOB_TIMEOUT_SECONDS` | `2700` | stops the job after 45 minutes |
| `PLANNER_MODEL` etc. | Opus / Sonnet | which AI model each part uses |

A typical run costs well under one cent.

## Running the tests

```bash
make test
```

Needs the database running and the test container built.

## Two versions

- **`anthropic`** (this branch) — uses Claude (Anthropic)
- **`openai`** — the same agent, using GPT instead

Only the AI layer differs. Switch with `git checkout openai`, then re-run `uv sync`.
The `main` branch holds no code — just an index pointing at these two.
