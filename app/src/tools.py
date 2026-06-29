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
    """Gemini rerank. Falls back to cosine order on any failure."""
    chunks = "\n\n".join(f"{i}. [{h['source']}]\n{h['text']}" for i, h in enumerate(hits))
    prompt = (
        "Bạn là reranker cho hệ thống RAG tiếng Việt.\n"
        "Chỉ xếp hạng các đoạn trực tiếp giúp trả lời query.\n"
        "Trả về JSON duy nhất dạng {\"ranked_ids\":[2,0,1]}. Không giải thích.\n\n"
        f"Query:\n{query}\n\nCác đoạn tài liệu:\n{chunks}"
    )
    try:
        raw = config.llm().invoke(prompt).content
        if isinstance(raw, list):
            raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict))
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        ranked_ids = json.loads(raw)["ranked_ids"]
        seen = set()
        reranked = []
        for i in ranked_ids:
            if isinstance(i, int) and 0 <= i < len(hits) and i not in seen:
                seen.add(i)
                reranked.append(hits[i])
        return (reranked + [h for i, h in enumerate(hits) if i not in seen])[:top_k]
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
    """Search internal document passages that are semantically relevant to the user's question.

    Use this for questions that require facts from the document knowledge base. The query should be the user's information need, including important names, IDs, or keywords. Returns matching passages grouped with source filenames; it does not query graph relationships.
    """
    hits = retrieve(query, runnable_config=runnable_config)
    if not hits:
        return "No relevant documents found."
    return "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits)


@tool
def submit_user_preference(rule: str) -> str:
    """Submit a user tone, language, or formatting preference for human review.

    Use this only when the user explicitly gives behavioral feedback about how responses should be written. The rule parameter should contain only the preference text, not unrelated question content or policy changes. This tool stages the preference for review and does not make the rule active immediately.
    """
    stores.add_rule(rule, org_id=config.ORG_ID)
    return f"Submitted preference for review: {rule}"


@tool
def graph_query(entity: str) -> str:
    """Look up relationships around a named entity in the knowledge graph.

    Use this for people, projects, requirements, bugs, document IDs, or other entities mentioned in the user's question. The entity parameter should be the most specific name or identifier available. Returns graph relationships near the entity; it does not return full document passages.
    """
    return graph.query(entity, org_id=config.ORG_ID)


TOOLS = [retrieve_docs, submit_user_preference, graph_query]
