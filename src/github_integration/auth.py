"""Auth provider abstraction: swapping a static PAT for a GitHub App's
short-lived installation tokens (Phase 6) should be a config change, not a
rewrite of every call site that needs a token. Every caller goes through
`get_auth_provider().get_token()`, never `settings.github_token` directly.
"""

import time
from typing import Protocol

import jwt
import requests

from src.config import settings


class AuthProvider(Protocol):
    def get_token(self) -> str: ...


class StaticTokenProvider:
    """Phase 2-5: a single fine-grained PAT, scoped to the target repo(s)."""

    def __init__(self, token: str):
        self._token = token

    def get_token(self) -> str:
        if not self._token:
            raise RuntimeError(
                "GITHUB_TOKEN is not set -- add a fine-grained PAT scoped to the "
                "target repo(s) to autonomous-dev-agent/.env"
            )
        return self._token


class GitHubAppTokenProvider:
    """Phase 6: JWT-based GitHub App auth, exchanged for a short-lived
    (~1hr) installation token and cached until near expiry. Not wired into
    settings/get_auth_provider() by default yet -- opt in once an App is
    registered; code-complete but not live-verified without a real App.
    """

    def __init__(self, app_id: str, private_key: str, installation_id: str):
        self._app_id = app_id
        self._private_key = private_key
        self._installation_id = installation_id
        self._cached_token: str | None = None
        self._cached_until: float = 0.0

    def _make_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": self._app_id}
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    def get_token(self) -> str:
        if self._cached_token and time.time() < self._cached_until:
            return self._cached_token

        app_jwt = self._make_jwt()
        response = requests.post(
            f"https://api.github.com/app/installations/{self._installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        self._cached_token = data["token"]
        # Refresh 5 minutes before actual expiry as a safety margin.
        self._cached_until = time.time() + 55 * 60
        return self._cached_token


def get_auth_provider() -> AuthProvider:
    return StaticTokenProvider(settings.github_token)
