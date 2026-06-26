import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from src import tools


class FakeMessage:
    content = '{"ranked_ids":[2,0]}'


class FakeLlm:
    called = False

    def invoke(self, prompt):
        self.called = True
        return FakeMessage()


def test_rerank_uses_gemini_ranked_ids(monkeypatch):
    llm = FakeLlm()
    monkeypatch.setattr(tools.config, "llm", lambda: llm)
    hits = [
        {"source": "a", "text": "first"},
        {"source": "b", "text": "second"},
        {"source": "c", "text": "third"},
    ]

    assert tools._rerank("alice nguyen lam gi", hits, 2) == [hits[2], hits[0]]
    assert llm.called
