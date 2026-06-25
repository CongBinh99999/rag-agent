"""Single agent with parallel tool calling.

Structure:
  agent
   ├── retrieve_docs   [Qdrant vector search]
   ├── graph_query     [Neo4j GraphRAG]
   └── save_rule       [personalization]

System prompt = agent_system.xml + org rules from Mongo.
History persisted in Redis, keyed by session/thread id.
"""
from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from langchain_community.chat_message_histories import RedisChatMessageHistory
from langfuse.langchain import CallbackHandler

from . import config, stores
from .tools import TOOLS


def _text(content) -> str:
    """LangChain 1.x msg.content may be a str or a list of content blocks."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def _system() -> str:
    base = config.load_prompt("agent_system.xml")
    rules = stores.get_rules(config.ORG_ID)
    rules_txt = "\n".join(f"- {r}" for r in rules) if rules else "(none yet)"
    return base.replace("{rules}", rules_txt)


def build_agent():
    """Fresh build each chat start so newly-saved rules load into the system prompt."""
    return create_agent(config.llm(), TOOLS, system_prompt=_system())


def run(agent, session_id: str, user_input: str, run_config: dict | None = None) -> str:
    """One turn. Loads/saves history in Redis. run_config carries Chainlit callbacks."""
    hist = RedisChatMessageHistory(session_id, url=config.REDIS_URL)
    hist.add_user_message(user_input)

    lf_cb = CallbackHandler()

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
    a = build_agent()
    print("--- Q1 (graph) ---")
    print(run(a, "selfcheck2", "What does the Backend Team own?"))
    print("--- Q2 (docs) ---")
    print(run(a, "selfcheck2", "How many annual leave days do employees get?"))
    print("--- Q3 (parallel) ---")
    print(run(a, "selfcheck2", "Who is the owner of US-031 and what are the details from the Sprint 3 planning meeting?"))
    print("OK")