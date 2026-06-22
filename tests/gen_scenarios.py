"""Generate eval scenarios with deepeval, grounded in REAL Qdrant chunks.

Why from_contexts (not from_docs): from_docs makes deepeval chunk + embed the
files itself, defaulting to OpenAI embeddings -> fails with a Gemini-only key.
Feeding our own Qdrant chunks as contexts sidesteps deepeval's embedder entirely.

  single : python -m tests.gen_scenarios single  -> 5 single-turn goldens -> goldens_single.json
  multi  : python -m tests.gen_scenarios multi    -> 5 conversational goldens -> goldens_multi.json

Review the JSON before using it for eval (P0.3 asks for human review).
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from src import config  # noqa: E402
from tests.gemini_judge import GeminiJudge  # noqa: E402

OUT = Path(__file__).resolve().parent


def _styling():
    """Single-turn styling: grounded employee->assistant Q&A."""
    from deepeval.synthesizer.config import StylingConfig

    return StylingConfig(
        scenario="An employee of the organization asks the internal knowledge "
        "assistant questions about company project documents.",
        task="The assistant answers factual questions grounded ONLY in retrieved "
        "internal documents (requirements, bug tracker, meeting minutes, API spec, "
        "README). It does not speculate, give opinions, or discuss hypotheticals.",
        input_format="A direct factual question a real employee would ask, "
        "answerable from the documents. No 'what if', no opinion, no roleplay.",
        expected_output_format="A concise factual answer grounded in the documents.",
    )


def _conv_styling():
    """Multi-turn styling. SEPARATE config with different field names — the
    conversational generator ignores StylingConfig. participant_roles is the key
    knob: left at default deepeval invents two-human debates. We force exactly
    two roles (employee, assistant) so scenarios become real Q&A sessions."""
    from deepeval.synthesizer.config import ConversationalStylingConfig

    return ConversationalStylingConfig(
        participant_roles="An employee asking questions, and the internal "
        "knowledge assistant answering them. NOT two humans debating each other.",
        scenario_context="An employee has a multi-turn Q&A session with the "
        "internal knowledge assistant about company project documents, asking "
        "follow-up questions that build on earlier answers.",
        conversational_task="The assistant answers each factual question grounded "
        "ONLY in retrieved internal documents (requirements, bug tracker, meeting "
        "minutes, API spec, README). No speculation, opinions, or 'what if' hypotheticals.",
        expected_outcome_format="The employee's factual questions are all answered "
        "correctly and concisely from the documents across the turns.",
    )


def _contexts_by_source() -> list[list[str]]:
    """One context = the chunks of one source file. Skip sample.txt (test leftover)."""
    pts, _ = config.qdrant().scroll(config.COLLECTION, limit=200, with_payload=True)
    by_src: dict[str, list[str]] = defaultdict(list)
    for p in pts:
        src = os.path.basename(p.payload.get("source", "?"))
        if src == "sample.txt":
            continue
        by_src[src].append(p.payload["text"])
    return list(by_src.values())


def gen_single() -> None:
    from deepeval.synthesizer import Synthesizer

    judge = GeminiJudge()
    contexts = _contexts_by_source()[:20]  # all sources -> aim for ~20 goldens
    synth = Synthesizer(model=judge, styling_config=_styling())
    goldens = synth.generate_goldens_from_contexts(
        contexts=contexts,
        include_expected_output=True,
        max_goldens_per_context=3,
    )
    data = [
        {"input": g.input, "expected_output": g.expected_output, "context": g.context}
        for g in goldens
    ]
    (OUT / "goldens_single.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(data)} single-turn goldens")
    assert data, "no goldens generated"


def gen_multi() -> None:
    from deepeval.synthesizer import Synthesizer

    judge = GeminiJudge()
    contexts = _contexts_by_source()[:20]
    synth = Synthesizer(model=judge, conversational_styling_config=_conv_styling())
    goldens = synth.generate_conversational_goldens_from_contexts(
        contexts=contexts,
        max_goldens_per_context=3,
    )
    data = [
        {"scenario": g.scenario, "expected_outcome": g.expected_outcome,
         "user_description": getattr(g, "user_description", None)}
        for g in goldens
    ]
    (OUT / "goldens_multi.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(data)} conversational goldens")
    assert data, "no goldens generated"


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "single"
    gen_single() if mode == "single" else gen_multi()
    print("OK")
