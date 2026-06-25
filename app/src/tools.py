"""Agent tools: Task 1 = retrieve internal knowledge, Task 2 = personalize via rules."""
import json

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from . import config, stores, embeddings, graph


def _filter_chunks(query: str, hits: list[dict], runnable_config: RunnableConfig | None = None) -> list[dict]:
    """B2: LLM drops chunk text irrelevant to the query (cuts in-chunk noise that
    top_k/chunk_size can't). Falls back to unfiltered hits on any failure."""
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
        return kept or hits  # empty filter result -> keep original, don't starve the answer
    except Exception:
        return hits


def _rerank(query: str, hits: list[dict], top_k: int) -> list[dict]:
    """Cross-encoder rerank: cosine can't tell 'mentions JWT' from 'states the
    token TTL', so the answer chunk sinks below keyword-dense noise. flashrank
    scores (query, chunk) jointly and reorders. Falls back to cosine order on any
    failure. ponytail: TinyBERT ONNX, no torch."""
    try:
        from flashrank import RerankRequest
        passages = [{"id": i, "text": h["text"], "meta": h} for i, h in enumerate(hits)]
        ranked = config.ranker().rerank(RerankRequest(query=query, passages=passages))
        return [r["meta"] for r in ranked[:top_k]]
    except Exception:
        return hits[:top_k]


def retrieve(query: str, k: int | None = None, runnable_config: RunnableConfig | None = None) -> list[dict]:
    """Core retrieval used by BOTH the agent tool and eval. One path, so tuning
    config.TOP_K changes what eval measures too. Returns chunk payloads (text+source).
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
    """Search internal knowledge base for info relevant to the query.
    Returns matching passages with their source. Use for any factual question."""
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
    """Look up how an entity relates to others in the knowledge graph.
    Use for questions about connections, ownership, dependencies, or structure
    (e.g. 'what does the Backend Team own?', 'what depends on FastAPI?')."""
    return graph.query(entity, org_id=config.ORG_ID)


TOOLS = [retrieve_docs, save_rule, graph_query]
