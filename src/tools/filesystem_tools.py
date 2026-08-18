"""Filesystem tools scoped to a job's repo workspace.

Every path is resolved against `repo_root` and checked to still live under it
after resolution, so a `write_file("../../etc/passwd", ...)` from an LLM tool
call can't escape the workspace via `..` or a symlink.
"""

from pathlib import Path

_DENYLIST_PATTERNS = (".env", ".env.", "id_rsa", ".pem")


def _resolve_scoped(repo_root: str, path: str) -> Path:
    root = Path(repo_root).resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"path escapes repo workspace: {path}")
    if any(part.startswith(_DENYLIST_PATTERNS) for part in (candidate.name,)):
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
    return sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())


def list_repo_structure(repo_root: str) -> list[str]:
    root = Path(repo_root).resolve()
    skip_dirs = {".git", "__pycache__", ".venv", "node_modules"}
    paths = []
    for p in root.rglob("*"):
        if p.is_file() and not any(part in skip_dirs for part in p.parts):
            paths.append(str(p.relative_to(root)))
    return sorted(paths)
