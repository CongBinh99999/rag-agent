"""Inspect what's stored across the 4 stores. Run: python -m src.inspect_db"""
from . import config


def main() -> None:
    q = config.qdrant()
    db = config.db()

    # Qdrant: count + one sample point (payload + vector dim)
    count = q.count(config.COLLECTION).count
    print(f"=== QDRANT ({config.COLLECTION}) ===")
    print(f"total chunks: {count}")
    pts, _ = q.scroll(config.COLLECTION, limit=1, with_payload=True, with_vectors=True)
    if pts:
        p = pts[0]
        vec = p.vector
        print(f"sample point id: {p.id}")
        print(f"  source : {p.payload.get('source')}")
        print(f"  org_id : {p.payload.get('org_id')}")
        print(f"  text   : {p.payload.get('text', '')[:120]}...")
        print(f"  vector : dim={len(vec)}  first5={[round(x, 4) for x in vec[:5]]}")

    # Mongo: docs + rules (feedback)
    print("\n=== MONGO (ragagent) ===")
    docs = list(db["docs"].find({}, {"source": 1, "org_id": 1}))
    print(f"docs: {len(docs)}")
    for d in docs:
        md_len = len(db["docs"].find_one({"_id": d["_id"]})["markdown"])
        print(f"  - {d['source']}  (org={d['org_id']}, md_chars={md_len})")
    rules = list(db["rules"].find({}))
    print(f"rules (feedback): {len(rules)}")
    for r in rules:
        print(f"  - [{r['org_id']}] {r['rule']}")


if __name__ == "__main__":
    main()
