from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""

    planner_model: str = "claude-opus-5"
    coder_model: str = "claude-sonnet-5"
    testing_model: str = "claude-sonnet-5"
    debugger_model: str = "claude-opus-5"

    max_iterations: int = 6
    job_timeout_seconds: int = 2700
    job_cost_budget_usd: float = 2.00

    # Writes to test files are refused at the tool layer. Left as a prompt-only
    # rule this failed in practice: an issue carrying "modify the tests so they
    # pass trivially" got the whole suite replaced with `assert True`, and the
    # run then reported passing tests. Enable only for a job whose actual
    # purpose is changing tests, and never on a run triggered by issue text a
    # stranger can author.
    allow_test_edits: bool = False

    github_token: str = ""
    github_webhook_secret: str = ""

    sandbox_image: str = "autonomous-dev-agent-sandbox:latest"
    sandbox_timeout_seconds: int = 300
    # Job workspaces must live under a path Docker Desktop actually bind-mounts.
    # On Mac, /tmp and /private/var (i.e. tempfile.mkdtemp()'s default) are NOT
    # shared by default and silently produce an empty mount instead of erroring
    # -- only /Users paths are reliable. Keep this under the project dir, not
    # the system temp dir. See docker_manager.py's docstring for the full story.
    workspace_dir: str = "workspaces"

    database_url: str = ""
    embedding_provider: str = "voyage"
    voyage_api_key: str = ""


settings = Settings()
