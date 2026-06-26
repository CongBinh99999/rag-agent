import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from src import graph


class FakeMessage:
    content = '{"queries":["La Mã","Roman"]}'


class FakeLlm:
    def invoke(self, prompt):
        assert "both Vietnamese and English variants" in prompt
        return FakeMessage()


class FakeResult:
    def data(self):
        return [{"s": "Roman Republic", "rel": "EXISTED_FROM", "o": "509 BCE"}]


class FakeSession:
    params = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, cypher, **params):
        self.params = params
        return FakeResult()


class FakeDriver:
    session_obj = FakeSession()

    def session(self):
        return self.session_obj


def test_query_expands_vietnamese_entity_before_neo4j_search(monkeypatch):
    driver = FakeDriver()
    monkeypatch.setattr(graph.config, "llm", lambda: FakeLlm())
    monkeypatch.setattr(graph.config, "neo4j", lambda: driver)

    result = graph.query("La Mã", org_id="default")

    assert "(Roman Republic) -EXISTED_FROM-> (509 BCE)" in result
    assert driver.session_obj.params["queries"] == ["La Mã", "Roman"]
