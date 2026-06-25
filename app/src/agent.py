"""Multi-agent (LangChain 1.x `create_agent`), agent-as-tool pattern.

Structure:
  supervisor
   ├── knowledge_agent  (sub-agent)  -> retrieve_docs   [Qdrant vector search]
   ├── graph_agent      (sub-agent)  -> graph_query      [Neo4j GraphRAG]
   └── save_rule        (tool)                           [personalization]

Supervisor system prompt = agent_system.xml + org rules from Mongo.
History persisted in Redis, keyed by session/thread id.
"""
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_community.chat_message_histories import RedisChatMessageHistory
from langfuse.langchain import CallbackHandler

from . import config, stores
from .tools import retrieve_docs, save_rule, graph_query


def _text(content) -> str:
    """LangChain 1.x msg.content may be a str or a list of content blocks."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def _run_sub(agent, question: str, config: RunnableConfig | None = None) -> str:
    """Invoke a sub-agent with a single question, return its final text."""
    result = agent.invoke({"messages": [{"role": "user", "content": question}]}, config=config)
    return _text(result["messages"][-1].content)


# ponytail: sub-agents are single-purpose workers; one-line system prompt is enough.
def _build_subagents():
    knowledge = create_agent(
        config.llm(), [retrieve_docs],
        system_prompt=(
            "You are a strict document retrieval specialist.\n"
            "CRITICAL RULES:\n"
            "1. You MUST ALWAYS call the `retrieve_docs` tool to search the knowledge base. Never answer from your own parametric memory or make assumptions.\n"
            "2. If `retrieve_docs` returns no matching documents or irrelevant passages, state clearly: 'I cannot find this information in the database.' Do NOT invent any facts or objectives.\n"
            "3. Always cite the exact source filename returned by the tool."
        ),
    )
    graph = create_agent(
        config.llm(), [graph_query],
        system_prompt="You are a knowledge-graph specialist. Use graph_query to find "
        "how entities connect (ownership, dependencies, structure) and report the relationships.",
    )

    @tool
    def knowledge_agent(question: str, runnable_config: RunnableConfig) -> str:
        """Delegate to the document content retrieval specialist.
        Use for standard text-search questions, detailed facts, figures, requirements, status, budgets, or specific meeting details.
        Do NOT use for questions asking about connections, ownership, dependencies, or assignments between entities."""
        return _run_sub(knowledge, question, runnable_config)

    @tool
    def graph_agent(question: str, runnable_config: RunnableConfig) -> str:
        """Delegate to the knowledge-graph specialist.
        Use for questions asking about connections, linkages, ownership, dependencies, assignments, structure, or relations between entities (e.g. 'who owns what', 'what depends on what', 'what is assigned to whom')."""
        return _run_sub(graph, question, runnable_config)

    return [knowledge_agent, graph_agent]


def _system() -> str:
    base = config.load_prompt("agent_system.xml")
    rules = stores.get_rules(config.ORG_ID)
    rules_txt = "\n".join(f"- {r}" for r in rules) if rules else "(none yet)"
    return base.replace("{rules}", rules_txt)


def build_agent():
    """Fresh build each chat start so newly-saved rules load into the system prompt."""
    tools = _build_subagents() + [save_rule]
    return create_agent(config.llm(), tools, system_prompt=_system())


def run(agent, session_id: str, user_input: str, run_config: dict | None = None) -> str:
    """One turn. Loads/saves history in Redis. run_config carries Chainlit callbacks."""
    hist = RedisChatMessageHistory(session_id, url=config.REDIS_URL)
    hist.add_user_message(user_input)
    
    # Initialize Langfuse CallbackHandler
    lf_cb = CallbackHandler()
    
    # Merge callbacks and metadata
    run_config = run_config or {}
    
    callbacks = run_config.get("callbacks", [])
    run_config["callbacks"] = list(callbacks) + [lf_cb]
    
    if "metadata" not in run_config:
        run_config["metadata"] = {}
    run_config["metadata"]["langfuse_session_id"] = session_id
    
    result = agent.invoke({"messages": hist.messages}, config=run_config)
    answer = _text(result["messages"][-1].content)
    hist.add_ai_message(answer)
    return answer


if __name__ == "__main__":
    # Self-check: route a graph question + a doc question through the supervisor.
    a = build_agent()
    print("--- Q1 (graph) ---")
    print(run(a, "selfcheck2", "What does the Backend Team own?"))
    print("--- Q2 (docs) ---")
    print(run(a, "selfcheck2", "How many annual leave days do employees get?"))
    print("--- Q3 (both graph & docs) ---")
    print(run(a, "selfcheck2", "Who is the owner of US-031 and what are the details from the Sprint 3 planning meeting?"))
    print("OK")
