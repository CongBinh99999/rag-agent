"""RAG evaluation with deepeval, Gemini as judge.

Run styles:
  single : python -m tests.test_rag single   -> RAG triad over goldens_single.json
  multi  : python -m tests.test_rag multi     -> conversational metrics over goldens_multi.json

Single metrics (RAG triad): AnswerRelevancy, Faithfulness, ContextualRelevancy.
Multi metrics: TurnRelevancy (per-turn answer relevancy), KnowledgeRetention
  (does the agent remember facts across turns).

Both run through the SAME agent pipeline (tools.retrieve + agent.run), so tuning
config.TOP_K / CHUNK_SIZE / FILTER changes what the eval measures.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from deepeval import evaluate  # noqa: E402
from deepeval.evaluate.configs import AsyncConfig  # noqa: E402
from deepeval.evaluate.configs import AsyncConfig  # noqa: E402
from deepeval.test_case import LLMTestCase, Turn  # noqa: E402
from deepeval.dataset import ConversationalGolden  # noqa: E402
from deepeval.simulator import ConversationSimulator  # noqa: E402
from deepeval.metrics import (  # noqa: E402
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    ContextualRecallMetric,
    TurnRelevancyMetric,
    KnowledgeRetentionMetric,
    TurnFaithfulnessMetric,
)

from src import config  # noqa: E402
from src.tools import retrieve  # noqa: E402
from src.agent import build_agent, run  # noqa: E402
from tests.gemini_judge import GeminiJudge  # noqa: E402

HERE = Path(__file__).resolve().parent


def _dump(res, name: str) -> None:
    """Per-case scores to JSON. deepeval's rich table overwrites in place, so a
    piped log only keeps the last frame; this file is the durable record."""
    dump = [
        {"id": getattr(t, "input", None) or getattr(t, "name", None),
         "metrics": [{"name": m.name, "score": m.score, "passed": m.success,
                      "reason": m.reason} for m in t.metrics_data]}
        for t in res.test_results
    ]
    (HERE / name).write_text(json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------- single-turn ----------
def _metrics(judge):
    # 3 metrics, one per RAG role: AnswerRelevancy (on-topic) + Faithfulness
    # (no hallucination) are reference-free; ContextualRecall is the single
    # retriever metric (did we fetch enough). Dropped ContextualPrecision
    # (ranking is moot at top_k=4 after rerank) and ContextualRelevancy
    # (penalizes chunk noise -> false fails when the answer is correct).
    return [
        AnswerRelevancyMetric(model=judge),
        FaithfulnessMetric(model=judge),
        ContextualRecallMetric(model=judge),
    ]


def _case(agent, question: str, expected: str | None = None) -> LLMTestCase:
    """Real pipeline: retrieved context (tools.retrieve) + agent answer."""
    context = [h["text"] for h in retrieve(question)]
    answer = run(agent, f"eval-{abs(hash(question))}", question)
    return LLMTestCase(input=question, actual_output=answer,
                       expected_output=expected, retrieval_context=context)


def run_single() -> None:
    (HERE / "eval_result.json").unlink(missing_ok=True)  # fresh run
    goldens = json.loads((HERE / "goldens_single.json").read_text(encoding="utf-8"))
    judge = GeminiJudge()
    agent = build_agent()
    cases = [_case(agent, g["input"], g.get("expected_output")) for g in goldens]
    res = evaluate(test_cases=cases, metrics=_metrics(judge))
    _dump(res, "eval_result.json")


# ---------- multi-turn ----------
def run_multi() -> None:
    (HERE / "eval_multi_result.json").unlink(missing_ok=True)
    raw = json.loads((HERE / "goldens_multi.json").read_text(encoding="utf-8"))
    judge = GeminiJudge()
    agent = build_agent()

    def model_callback(input: str, thread_id: str) -> Turn:
        # thread_id (one per simulated conversation) -> Redis session, so the
        # agent's history accumulates exactly like a real chat. Attach the same
        # retrieved context the agent used, so turn-level RAG metrics can score it.
        context = [h["text"] for h in retrieve(input)]
        answer = run(agent, f"sim-{thread_id}", input)
        return Turn(role="assistant", content=answer, retrieval_context=context)

    goldens = [
        ConversationalGolden(
            scenario=g["scenario"],
            expected_outcome=g.get("expected_outcome"),
            user_description=g.get("user_description")
            or "An employee querying the internal knowledge base.",
        )
        for g in raw
    ]
    sim = ConversationSimulator(model_callback=model_callback, simulator_model=judge)
    convos = sim.simulate(conversational_goldens=goldens, max_user_simulations=3)
    # 3 metrics: TurnRelevancy + TurnFaithfulness are the reference-free
    # generator pair; KnowledgeRetention is the one metric unique to multi-turn
    # (does the agent remember facts across turns). Dropped the 3 Turn-Contextual
    # metrics -> they duplicate single-turn retriever signal and are expensive
    # (drove the 429s we throttle for below).
    metrics = [
        TurnRelevancyMetric(model=judge),
        TurnFaithfulnessMetric(model=judge),
        KnowledgeRetentionMetric(model=judge),
    ]
    # Paid plan: utilize higher concurrency (20-way) and no throttling for faster evaluation
    res = evaluate(test_cases=convos, metrics=metrics,
                   async_config=AsyncConfig(max_concurrent=20, throttle_value=0))
    _dump(res, "eval_multi_result.json")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "single"
    if mode == "single":
        run_single()
    elif mode == "multi":
        run_multi()
    else:
        sys.exit("mode must be 'single' or 'multi'")
    print("OK")
