"""Task 2 (personalization) self-check: the feedback loop end-to-end.

  1. feedback -> agent calls submit_user_preference -> rule lands in Mongo (per org)
  2. saved rule is injected into a fresh agent's system prompt and obeyed
  3. rules are isolated per org_id (org A can't see org B's rule)

Run: python -m tests.test_rules
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from src import config, stores  # noqa: E402
from src.agent import build_agent, run  # noqa: E402

ORG_A = "test-org-a"
ORG_B = "test-org-b"


def _clean():
    config.db()["rules"].delete_many({"org_id": {"$in": [ORG_A, ORG_B]}})
    import redis
    try:
        r = redis.Redis.from_url(config.REDIS_URL)
        r.delete("message_store:rules-1", "message_store:rules-2")
    except Exception as e:
        print("Warning: could not clear Redis history:", e)


def _set_org(org_id: str):
    config.ORG_ID = org_id  # tools + agent read config.ORG_ID at call time


def main():
    _clean()
    try:
        # 1. feedback -> submit_user_preference fires -> rule persisted for ORG_A
        _set_org(ORG_A)
        agent = build_agent()
        run(agent, "rules-1",
            "Feedback: always end every answer with the exact marker <<DONE>>. "
            "Remember this for next time.")
        rules = stores.get_rules(ORG_A)
        assert rules, f"agent did not save any rule for {ORG_A}"
        assert any("<<DONE>>" in r for r in rules), f"marker rule not saved: {rules}"
        print(f"[1] submit_user_preference fired, rules={rules}")

        # 2. fresh agent loads the rule into its system prompt and obeys it
        agent2 = build_agent()
        answer = run(agent2, "rules-2", "How many bugs are in the bug tracker?")
        assert "<<DONE>>" in answer, f"rule not obeyed, answer={answer!r}"
        print(f"[2] rule obeyed, answer ends with marker")

        # 3. isolation: ORG_B has no rules, must not see ORG_A's
        _set_org(ORG_B)
        assert stores.get_rules(ORG_B) == [], "ORG_B leaked ORG_A's rules"
        print("[3] per-org isolation holds")

        print("OK")
    finally:
        _set_org(os.getenv("ORG_ID", "default"))
        _clean()


if __name__ == "__main__":
    main()
