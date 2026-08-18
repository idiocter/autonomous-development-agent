"""Chunking strategy: AST-aware for Python (chunk at function/class
boundaries -- each function/class becomes one chunk), line-window fallback
(~200 lines, 20-line overlap, for non-Python code), heading-based for
Markdown/docs. Multi-language AST chunking via tree-sitter is a documented
future enhancement (see plan.md), not required for this project's own
Python-heavy codebase.
"""

import ast
import hashlib
import re
from dataclasses import dataclass


@dataclass
class Chunk:
    file_path: str
    start_line: int
    end_line: int
    chunk_type: str  # "code" | "doc"
    language: str | None
    content: str
    content_hash: str


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def chunk_python_file(file_path: str, content: str) -> list[Chunk]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return chunk_generic_file(file_path, content, language="python")

    lines = content.splitlines()
    top_level_nodes = [
        n
        for n in ast.iter_child_nodes(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    if not top_level_nodes:
        return chunk_generic_file(file_path, content, language="python")

    chunks: list[Chunk] = []
    for node in top_level_nodes:
        start = node.lineno
        end = getattr(node, "end_lineno", start)
        segment = "\n".join(lines[start - 1 : end])
        if not segment.strip():
            continue
        chunks.append(
            Chunk(
                file_path=file_path,
                start_line=start,
                end_line=end,
                chunk_type="code",
                language="python",
                content=segment,
                content_hash=_hash(segment),
            )
        )
    return chunks


def chunk_generic_file(
    file_path: str, content: str, *, language: str | None, window: int = 200, overlap: int = 20
) -> list[Chunk]:
    lines = content.splitlines()
    if not lines:
        return []

    chunks: list[Chunk] = []
    start = 0
    while start < len(lines):
        end = min(start + window, len(lines))
        segment = "\n".join(lines[start:end])
        if segment.strip():
            chunks.append(
                Chunk(
                    file_path=file_path,
                    start_line=start + 1,
                    end_line=end,
                    chunk_type="code",
                    language=language,
                    content=segment,
                    content_hash=_hash(segment),
                )
            )
        if end == len(lines):
            break
        start = end - overlap
    return chunks


def chunk_markdown_file(file_path: str, content: str) -> list[Chunk]:
    lines = content.splitlines()
    sections: list[tuple[int, list[str]]] = []
    current_start = 1
    current_lines: list[str] = []
    for i, line in enumerate(lines, start=1):
        if re.match(r"^#{1,6}\s", line) and current_lines:
            sections.append((current_start, current_lines))
            current_start = i
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_start, current_lines))

    chunks = []
    for start, seg_lines in sections:
        segment = "\n".join(seg_lines)
        if not segment.strip():
            continue
        chunks.append(
            Chunk(
                file_path=file_path,
                start_line=start,
                end_line=start + len(seg_lines) - 1,
                chunk_type="doc",
                language=None,
                content=segment,
                content_hash=_hash(segment),
            )
        )
    return chunks or chunk_generic_file(file_path, content, language=None)


def chunk_file(file_path: str, content: str) -> list[Chunk]:
    if file_path.endswith(".py"):
        return chunk_python_file(file_path, content)
    if file_path.endswith((".md", ".rst")):
        return chunk_markdown_file(file_path, content)
    return chunk_generic_file(file_path, content, language=None)
