from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""

    # Override in .env to whatever your account has access to -- nothing in
    # the code depends on these specific model names. Heavier models are
    # assigned to planning/debugging, cheaper ones to mechanical steps.
    planner_model: str = "gpt-4o"
    coder_model: str = "gpt-4o"
    testing_model: str = "gpt-4o-mini"
    debugger_model: str = "gpt-4o"

    max_iterations: int = 6
    job_timeout_seconds: int = 2700
    job_cost_budget_usd: float = 2.00

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
