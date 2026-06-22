"""Gemini embeddings with Redis cache. ponytail: cache keyed by sha1(model+text)."""
import hashlib
import json

from . import config


def _key(text: str) -> str:
    h = hashlib.sha1(f"{config.EMBED_MODEL}:{text}".encode()).hexdigest()
    return f"emb:{h}"


def embed_query(text: str) -> list[float]:
    cached = config.rds().get(_key(text))
    if cached:
        return json.loads(cached)
    vec = config.embeddings().embed_query(text)
    config.rds().set(_key(text), json.dumps(vec))
    return vec


def embed_docs(texts: list[str]) -> list[list[float]]:
    # ponytail: ingestion is one-shot, no cache here; query path is the hot one.
    return config.embeddings().embed_documents(texts)
