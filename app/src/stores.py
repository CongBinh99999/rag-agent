"""Thin wrappers over Qdrant + Mongo. Clients live in config."""
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

from . import config


def ensure_collection(dim: int) -> None:
    q = config.qdrant()
    names = [c.name for c in q.get_collections().collections]
    if config.COLLECTION not in names:
        q.create_collection(
            config.COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )


def upsert(points: list[PointStruct]) -> None:
    config.qdrant().upsert(config.COLLECTION, points=points)


def search(vector: list[float], k: int, org_id: str):
    flt = Filter(must=[FieldCondition(key="org_id", match=MatchValue(value=org_id))])
    return config.qdrant().query_points(
        config.COLLECTION, query=vector, query_filter=flt, limit=k
    ).points


def save_doc(source: str, markdown: str, org_id: str) -> None:
    config.db()["docs"].update_one(
        {"source": source, "org_id": org_id},
        {"$set": {"markdown": markdown}},
        upsert=True,
    )


def add_rule(rule: str, org_id: str) -> None:
    config.db()["rules"].insert_one({"org_id": org_id, "rule": rule})


def get_rules(org_id: str) -> list[str]:
    return [r["rule"] for r in config.db()["rules"].find({"org_id": org_id})]
