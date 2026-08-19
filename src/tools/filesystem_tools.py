"""Filesystem tools scoped to a job's repo workspace.

Every path is resolved against `repo_root` and checked to still live under it
after resolution, so a `write_file("../../etc/passwd", ...)` from an LLM tool
call can't escape the workspace via `..` or a symlink.
"""

from pathlib import Path

# Independent of .gitignore -- a repo may well not ignore its own secrets, and
# the point is to stop the agent reading them even when told to.
#
# Split by match type on purpose: the previous version tested every pattern
# with `startswith`, so suffix entries like ".pem" silently never matched and
# server.pem / key.p12 / credentials.json were all readable.
_DENY_PREFIXES = (".env", "id_rsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc")
_DENY_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
_DENY_EXACT = ("credentials.json", "service_account.json", "secrets.yaml", "secrets.yml")


def is_denylisted(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered.startswith(_DENY_PREFIXES)
        or lowered.endswith(_DENY_SUFFIXES)
        or lowered in _DENY_EXACT
    )


def _resolve_scoped(repo_root: str, path: str) -> Path:
    root = Path(repo_root).resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"path escapes repo workspace: {path}")
    # Check every path component, not just the filename -- otherwise
    # `secrets/prod.json` slips past a filename-only test.
    if any(is_denylisted(part) for part in candidate.relative_to(root).parts):
        raise ValueError(f"refusing to touch denylisted path: {path}")
    return candidate


def read_file(repo_root: str, path: str) -> str:
    target = _resolve_scoped(repo_root, path)
    return target.read_text()


def write_file(repo_root: str, path: str, content: str) -> None:
    target = _resolve_scoped(repo_root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def str_replace(repo_root: str, path: str, old_str: str, new_str: str) -> None:
    target = _resolve_scoped(repo_root, path)
    text = target.read_text()
    count = text.count(old_str)
    if count == 0:
        raise ValueError(f"old_str not found in {path}")
    if count > 1:
        raise ValueError(f"old_str matches {count} times in {path}; must be unique")
    target.write_text(text.replace(old_str, new_str, 1))


def list_dir(repo_root: str, path: str = ".") -> list[str]:
    target = _resolve_scoped(repo_root, path)
    # Denylisted entries are hidden, not just unreadable -- surfacing
    # "there is a .env here" invites the model to go after it.
    return sorted(
        p.name + ("/" if p.is_dir() else "")
        for p in target.iterdir()
        if not is_denylisted(p.name)
    )


def list_repo_structure(repo_root: str) -> list[str]:
    root = Path(repo_root).resolve()
    skip_dirs = {".git", "__pycache__", ".venv", "node_modules"}
    paths = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in skip_dirs for part in p.parts):
            continue
        if any(is_denylisted(part) for part in rel.parts):
            continue
        paths.append(str(rel))
    return sorted(paths)
