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
    def __init__(self, rows):
        self.rows = rows

    def data(self):
        return self.rows


class FakeSession:
    params = None

    def __enter__(self):
        self.calls = []
        return self

    def __exit__(self, *args):
        return False

    def run(self, cypher, **params):
        self.params = params
        self.calls.append(params)
        if "RETURN DISTINCT n.name AS name" in cypher:
            return FakeResult([{"name": "Roman Republic"}])
        return FakeResult([{"s": "Roman Republic", "rel": "EXISTED_FROM", "o": "509 BCE"}])


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
    assert driver.session_obj.calls[0]["queries"] == ["La Mã", "Roman"]

class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def data(self):
        return self.rows

class NeighborhoodSession:
    params = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, cypher, **params):
        self.params.append(params)
        if "RETURN DISTINCT n.name AS name" in cypher:
            return FakeRows([{"name": "Alice Nguyen"}])
        return FakeRows([
            {"s": "REQ-005", "rel": "ASSIGNED_TO", "o": "Alice Nguyen"},
            {"s": "REQ-005", "rel": "HAS_CATEGORY", "o": "Security"},
            {"s": "Alice Nguyen", "rel": "HAS_ROLE", "o": "Backend Engineer"},
        ])

class NeighborhoodDriver:
    session_obj = NeighborhoodSession()

    def session(self):
        return self.session_obj

def test_query_returns_neighbor_relationships_for_matched_entity(monkeypatch):
    driver = NeighborhoodDriver()
    monkeypatch.setattr(graph.config, "llm", lambda: FakeLlm())
    monkeypatch.setattr(graph.config, "neo4j", lambda: driver)

    result = graph.query("Alice Nguyen", org_id="default")

    assert "(REQ-005) -ASSIGNED_TO-> (Alice Nguyen)" in result
    assert "(REQ-005) -HAS_CATEGORY-> (Security)" in result
    assert "(Alice Nguyen) -HAS_ROLE-> (Backend Engineer)" in result
    assert driver.session_obj.params[-1]["matched"] == ["Alice Nguyen"]

class ExactIdSession:
    params = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, cypher, **params):
        self.params.append(params)
        if "RETURN DISTINCT n.name AS name" in cypher:
            return FakeRows([{"name": "REQ-005"}])
        if "MATCH p=" not in cypher:
            return FakeRows([
                {"s": "REQ-005", "rel": "ASSIGNED_TO", "o": "Alice Nguyen"},
                {"s": "REQ-005", "rel": "HAS_CATEGORY", "o": "Security"},
            ])
        return FakeRows([
            {"s": "REQ-005", "rel": "ASSIGNED_TO", "o": "Alice Nguyen"},
            {"s": "REQ-005", "rel": "HAS_CATEGORY", "o": "Security"},
            {"s": "REQ-001", "rel": "ASSIGNED_TO", "o": "Alice Nguyen"},
        ])

class ExactIdDriver:
    session_obj = ExactIdSession()

    def session(self):
        return self.session_obj

def test_query_exact_id_does_not_expand_through_neighbor_hubs(monkeypatch):
    driver = ExactIdDriver()
    monkeypatch.setattr(graph.config, "llm", lambda: FakeLlm())
    monkeypatch.setattr(graph.config, "neo4j", lambda: driver)

    result = graph.query("REQ-005", org_id="default")

    assert "(REQ-005) -ASSIGNED_TO-> (Alice Nguyen)" in result
    assert "(REQ-005) -HAS_CATEGORY-> (Security)" in result
    assert "(REQ-001) -ASSIGNED_TO-> (Alice Nguyen)" not in result

class ExactNameSession:
    params = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, cypher, **params):
        self.params.append(params)
        if "RETURN DISTINCT n.name AS name" in cypher:
            return FakeRows([{"name": "Alice Nguyen"}, {"name": "Alice Johnson"}])
        rows = [{"s": "REQ-005", "rel": "ASSIGNED_TO", "o": "Alice Nguyen"}]
        if "Alice Johnson" in params["matched"]:
            rows.append({"s": "Alice Johnson", "rel": "HAS_EMAIL", "o": "alice@example.com"})
        return FakeRows(rows)

class ExactNameDriver:
    session_obj = ExactNameSession()

    def session(self):
        return self.session_obj

def test_query_exact_name_uses_only_exact_match(monkeypatch):
    driver = ExactNameDriver()
    monkeypatch.setattr(graph.config, "llm", lambda: FakeLlm())
    monkeypatch.setattr(graph.config, "neo4j", lambda: driver)

    result = graph.query("Alice Nguyen", org_id="default")

    assert driver.session_obj.params[-1]["matched"] == ["Alice Nguyen"]
    assert "(REQ-005) -ASSIGNED_TO-> (Alice Nguyen)" in result
    assert "Alice Johnson" not in result

class DirectedSession:
    params = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, cypher, **params):
        self.params.append(params)
        if "RETURN DISTINCT n.name AS name" in cypher:
            return FakeRows([{"name": "REQ-005"}])
        assert "-[r]->" in cypher
        assert "type(r) AS rel" in cypher
        return FakeRows([{"s": "REQ-005", "rel": "ASSIGNED_TO", "o": "Alice Nguyen"}])

class DirectedDriver:
    session_obj = DirectedSession()

    def session(self):
        return self.session_obj

def test_query_preserves_relationship_direction(monkeypatch):
    driver = DirectedDriver()
    monkeypatch.setattr(graph.config, "llm", lambda: FakeLlm())
    monkeypatch.setattr(graph.config, "neo4j", lambda: driver)

    result = graph.query("REQ-005", org_id="default")

    assert "(REQ-005) -ASSIGNED_TO-> (Alice Nguyen)" in result


def test_normalize_rel_keeps_new_domain_relations():
    assert graph._normalize_rel("has ingredient") == "HAS_INGREDIENT"
    assert graph._normalize_rel("directed-by") == "DIRECTED_BY"
    assert graph._normalize_rel("TREATS_DISEASE") == "TREATS_DISEASE"


def test_normalize_rel_merges_obvious_aliases_and_vague_relations():
    assert graph._normalize_rel("IS_ROLE") == "HAS_ROLE"
    assert graph._normalize_rel("works as") == "HAS_ROLE"
    assert graph._normalize_rel("is related to") == "MENTIONS"
    assert graph._normalize_rel("this is a long sentence relation") == "MENTIONS"


class WriteTx:
    kwargs = None

    def run(self, cypher, **kwargs):
        self.cypher = cypher
        self.kwargs = kwargs


def test_write_stores_normalized_dynamic_relation_and_raw_relation():
    tx = WriteTx()

    graph._write(tx, "Binh Nguyen", "IS_ROLE", "AI Engineer Intern", "default")

    assert "[r:`HAS_ROLE`]" in tx.cypher
    assert tx.kwargs["raw_rel"] == "IS_ROLE"
    assert "raw_type = $raw_rel" in tx.cypher


def test_write_rechecks_dynamic_relation_safety():
    tx = WriteTx()

    graph._write(tx, "A", "bad rel`) DELETE r //", "B", "default")

    assert "[r:`BAD_REL_DELETE_R`]" in tx.cypher
