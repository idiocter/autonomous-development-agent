from src.rag.chunking import chunk_file, chunk_markdown_file, chunk_python_file


def test_chunk_python_file_splits_on_function_boundaries():
    content = (
        "def foo():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def bar():\n"
        "    return 2\n"
    )
    chunks = chunk_python_file("mod.py", content)

    assert len(chunks) == 2
    assert "def foo" in chunks[0].content
    assert "def bar" in chunks[1].content
    assert chunks[0].language == "python"
    assert chunks[0].chunk_type == "code"


def test_chunk_python_file_falls_back_on_syntax_error():
    chunks = chunk_python_file("broken.py", "def foo(:\n    pass\n")
    assert len(chunks) == 1  # falls back to line-window chunking


def test_chunk_python_file_falls_back_when_no_top_level_defs():
    content = "x = 1\ny = 2\n" * 5
    chunks = chunk_python_file("script.py", content)
    assert len(chunks) == 1


def test_chunk_markdown_file_splits_on_headings():
    content = "# Title\nintro text\n\n## Section A\nbody a\n\n## Section B\nbody b\n"
    chunks = chunk_markdown_file("README.md", content)

    assert len(chunks) == 3
    assert all(c.chunk_type == "doc" for c in chunks)
    assert "Section A" in chunks[1].content
    assert "Section B" in chunks[2].content


def test_chunk_file_dispatches_by_extension():
    py_chunks = chunk_file("a.py", "def f():\n    pass\n")
    md_chunks = chunk_file("a.md", "# H\ntext\n")
    other_chunks = chunk_file("a.txt", "line1\nline2\n")

    assert py_chunks[0].language == "python"
    assert md_chunks[0].chunk_type == "doc"
    assert other_chunks[0].chunk_type == "code"


def test_content_hash_is_deterministic():
    chunks_a = chunk_file("a.py", "def f():\n    pass\n")
    chunks_b = chunk_file("a.py", "def f():\n    pass\n")
    assert chunks_a[0].content_hash == chunks_b[0].content_hash
