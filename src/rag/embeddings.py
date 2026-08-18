"""Local embedding model -- no API key needed, runs fully offline. This is
the only file that needs to change to swap in a hosted alternative
(Voyage/OpenAI) later; every caller goes through embed_texts/embed_query.

Model choice: all-MiniLM-L6-v2 (384-dim, general-purpose sentence
embeddings) rather than a code-specialized model -- it's small (~80MB),
fast on CPU, and good enough for this project's scale. A code-specialized
model (e.g. Voyage's voyage-code-3) is the documented upgrade path once a
hosted embedding API key is available; see EMBEDDING_PROVIDER in config.py.
Must match src/db/models.py's EMBEDDING_DIM (384) -- changing models means
a migration.
"""

from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = get_model()
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return vectors.tolist()


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
