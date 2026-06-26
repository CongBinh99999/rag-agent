import importlib
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


def test_rerank_pool_defaults_to_20_for_gemini_rerank(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("RERANK_POOL", raising=False)

    sys.modules.pop("src.config", None)
    config = importlib.import_module("src.config")

    assert config.RERANK_POOL == 20


def test_rerank_pool_can_be_overridden(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("RERANK_POOL", "12")

    sys.modules.pop("src.config", None)
    config = importlib.import_module("src.config")

    assert config.RERANK_POOL == 12


def test_rerank_can_be_disabled(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("RERANK", "0")

    sys.modules.pop("src.config", None)
    config = importlib.import_module("src.config")

    assert config.RERANK is False

def test_top_k_defaults_to_wide_pool_for_multilingual_retrieval(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("TOP_K", raising=False)

    sys.modules.pop("src.config", None)
    config = importlib.import_module("src.config")

    assert config.TOP_K == 10
