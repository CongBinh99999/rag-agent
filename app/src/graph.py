"""GraphRAG: extract entities/relations from docs (Gemini) -> Neo4j, and query them.
Run build: python -m src.graph
"""
import json
import sys

from . import config

_PROMPT = None


def _prompt() -> str:
    global _PROMPT
    if _PROMPT is None:
        _PROMPT = config.load_prompt("extract_graph.xml")
    return _PROMPT


def _extract(text: str) -> list[dict]:
    """Gemini -> triples. Returns [] on parse failure (ponytail: skip bad docs, don't crash build)."""
    raw = config.llm().invoke(_prompt().replace("{text}", text[:8000])).content
    if isinstance(raw, list):
        raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict))
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(raw).get("triples", [])
    except json.JSONDecodeError:
        return []


def _write(tx, src: str, rel: str, dst: str, org_id: str) -> None:
    tx.run(
        "MERGE (a:Entity {name:$src, org_id:$org}) "
        "MERGE (b:Entity {name:$dst, org_id:$org}) "
        "MERGE (a)-[r:REL {type:$rel}]->(b)",
        src=src, dst=dst, rel=rel, org=org_id,
    )


def _expand_entity(entity: str) -> list[str]:
    prompt = (
        "Expand this graph entity search term for a bilingual Vietnamese/English knowledge graph.\n"
        "Return JSON only: {\"queries\":[\"original\",\"translated alias\",\"root/adjective/broad term\"]}.\n"
        "Include both Vietnamese and English variants when useful, and include broad adjective/root terms like Roman for Rome/La Mã. Keep at most 5 short entity names. No explanations.\n\n"
        f"Entity: {entity}"
    )
    try:
        raw = config.llm().invoke(prompt).content
        if isinstance(raw, list):
            raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict))
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        queries = [entity]
        for q in json.loads(raw).get("queries", []):
            if isinstance(q, str) and q.strip() and q.strip() not in queries:
                queries.append(q.strip())
        return queries[:5]
    except Exception:
        return [entity]


def build(org_id: str | None = None) -> int:
    """Extract triples from all Mongo docs into Neo4j. Returns triple count."""
    org_id = org_id or config.ORG_ID
    docs = list(config.db()["docs"].find({"org_id": org_id}))
    total = 0
    with config.neo4j().session() as s:
        for d in docs:
            for t in _extract(d["markdown"]):
                if t.get("subject") and t.get("relation") and t.get("object"):
                    s.execute_write(_write, t["subject"], t["relation"], t["object"], org_id)
                    total += 1
    return total


def ingest_doc_graph(source_path: str, org_id: str | None = None) -> int:
    """Extract triples from a single Mongo doc by source path and write them to Neo4j. Returns triple count."""
    org_id = org_id or config.ORG_ID
    doc = config.db()["docs"].find_one({"source": source_path, "org_id": org_id})
    if not doc or not doc.get("markdown"):
        return 0
    extracted = _extract(doc["markdown"])
    if not extracted:
        return 0
    triples = 0
    with config.neo4j().session() as s:
        for t in extracted:
            if t.get("subject") and t.get("relation") and t.get("object"):
                s.execute_write(_write, t["subject"], t["relation"], t["object"], org_id)
                triples += 1
    return triples


def query(entity: str, org_id: str | None = None, limit: int = 25) -> str:
    """Find relationships touching entities whose name matches the query term."""
    org_id = org_id or config.ORG_ID
    queries = _expand_entity(entity)
    cypher = (
        "MATCH (a:Entity {org_id:$org})-[r:REL]->(b:Entity) "
        "WHERE ANY(q IN $queries WHERE toLower(a.name) CONTAINS toLower(q) OR toLower(b.name) CONTAINS toLower(q)) "
        "RETURN a.name AS s, r.type AS rel, b.name AS o LIMIT $lim"
    )
    with config.neo4j().session() as s:
        rows = s.run(cypher, queries=queries, org=org_id, lim=limit).data()
    if not rows:
        return "No graph relationships found for that term."
    return "\n".join(f"({r['s']}) -{r['rel']}-> ({r['o']})" for r in rows)


if __name__ == "__main__":
    n = build()
    print(f"wrote {n} triples to Neo4j")
    assert n > 0, "no triples extracted"
    print(query(sys.argv[1] if len(sys.argv) > 1 else "API"))
    print("OK")
