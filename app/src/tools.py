"""Agent tools: retrieve internal knowledge, query knowledge graph, personalize via rules."""
import json

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from . import config, stores, embeddings, graph


def _filter_chunks(query: str, hits: list[dict], runnable_config: RunnableConfig | None = None) -> list[dict]:
    """LLM drops chunk text irrelevant to the query. Falls back to unfiltered hits on any failure."""
    chunks = "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits)
    prompt = config.load_prompt("filter_chunks.xml").replace("{query}", query).replace("{chunks}", chunks)
    try:
        raw = config.llm().invoke(prompt, config=runnable_config).content
        if isinstance(raw, list):
            raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict))
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        kept = [k for k in json.loads(raw)["kept"] if k.get("text")]
        for k in kept:
            k.setdefault("source", "?")
        return kept or hits
    except Exception:
        return hits


def _rerank(query: str, hits: list[dict], top_k: int) -> list[dict]:
    """Cross-encoder rerank. Falls back to cosine order on any failure."""
    try:
        from flashrank import RerankRequest
        passages = [{"id": i, "text": h["text"], "meta": h} for i, h in enumerate(hits)]
        ranked = config.ranker().rerank(RerankRequest(query=query, passages=passages))
        return [r["meta"] for r in ranked[:top_k]]
    except Exception:
        return hits[:top_k]


def retrieve(query: str, k: int | None = None, runnable_config: RunnableConfig | None = None) -> list[dict]:
    """Core retrieval used by BOTH the agent tool and eval.
    Pipeline: cosine pool -> cross-encoder rerank -> top_k -> LLM filter."""
    top_k = k or config.TOP_K
    pool = config.RERANK_POOL if config.RERANK else top_k
    vec = embeddings.embed_query(query)
    hits = [h.payload for h in stores.search(vec, k=pool, org_id=config.ORG_ID)]
    if config.RERANK and hits:
        hits = _rerank(query, hits, top_k)
    return _filter_chunks(query, hits, runnable_config=runnable_config) if config.FILTER and hits else hits


@tool
def retrieve_docs(query: str, runnable_config: RunnableConfig) -> str:
    """Search the internal knowledge base for passages relevant to the query."""
    hits = retrieve(query, runnable_config=runnable_config)
    if not hits:
        return "No relevant documents found."
    return "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits)


@tool
def save_rule(rule: str) -> str:
    """Remember a user preference or feedback rule for future conversations.
    Call this when the user gives feedback on how you should behave
    (e.g. 'always answer in Vietnamese', 'be more concise')."""
    stores.add_rule(rule, org_id=config.ORG_ID)
    return f"Saved rule: {rule}"


@tool
def graph_query(entity: str) -> str:
    """Look up an entity in the knowledge graph and return its relationships."""
    return graph.query(entity, org_id=config.ORG_ID)


TOOLS = [retrieve_docs, save_rule, graph_query]