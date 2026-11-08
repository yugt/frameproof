from pathlib import Path

from frameproof.brief import Brief
from frameproof.checks import Check, CheckResult
from frameproof.improve import run
from frameproof.memory import MemoryHit
from frameproof.memory import MemoryStore
from frameproof.verify import Verdict


class FakeMemory:
    def __init__(self):
        self.failures = []
        self.fixes = []
        self.requirements = []
        self.events = []

    def ping(self): pass
    def remember_requirement(self, brief):
        self.requirements.append(brief.id)
        self.events.append(("remember", brief.id))
    def remember_failure(self, brief, verdict, attempt): self.failures.append((brief.id, attempt))
    def remember_fix(self, brief, before, after, guidance, attempt): self.fixes.append((brief.id, attempt))
    def search(self, query, *, types=(), limit=5, exclude_brief_id=None, min_score=0.70):
        self.events.append(("search", exclude_brief_id))
        return [MemoryHit("successful_fix", "Use the exact brief color at the required time.", 0.9, "old")]


def test_improve_retries_and_records_fix(tmp_path: Path):
    memory = FakeMemory()
    generated_feedback = []
    calls = 0

    def fake_generate(brief, out, **kwargs):
        generated_feedback.append(kwargs.get("feedback"))
        out.mkdir(parents=True)
        p = out / "index.html"
        p.write_text("<html></html>")
        return p

    def fake_verify(brief, html, checks, **kwargs):
        nonlocal calls
        calls += 1
        passed = calls == 2
        result = CheckResult("color", "text_color", passed, "hard",
                             "white" if passed else "expected white, got blue")
        return Verdict("ACCEPT" if passed else "BLOCK", 1.0 if passed else 0.0,
                       [] if passed else ["hard check failed"], [result])

    result = run(Brief("demo", "White headline"), [Check("color", "text_color")], tmp_path,
                 memory=memory, generate_fn=fake_generate, verify_fn=fake_verify)
    assert result.decision == "ACCEPT"
    assert result.attempts == 2
    assert memory.requirements == ["demo"]
    assert memory.events[:2] == [("search", "demo"), ("remember", "demo")]
    assert all(event[1] == "demo" for event in memory.events if event[0] == "search")
    assert memory.failures == [("demo", 1)]
    assert memory.fixes == [("demo", 2)]
    assert "expected white, got blue" in " ".join(generated_feedback[1])


def test_hidden_check_details_never_reach_memory_or_generation(tmp_path: Path):
    memory = FakeMemory()
    queries = []
    feedback_seen = []

    def search(query, **kwargs):
        queries.append(query)
        return []

    memory.search = search

    def fake_generate(brief, out, **kwargs):
        feedback_seen.append(" ".join(kwargs.get("feedback") or []))
        out.mkdir(parents=True)
        path = out / "index.html"
        path.write_text("<html></html>")
        return path

    def fake_verify(brief, html, checks, **kwargs):
        result = CheckResult("secret-check", "text_color", False, "hard",
                             "secret expected #FFFFFF", hidden=True)
        return Verdict("BLOCK", 0.0, ["hard check failed: secret-check (secret expected #FFFFFF)"], [result])

    run(Brief("secret", "A simple video"), [Check("secret-check", "text_color", hidden=True)],
        tmp_path, memory=memory, max_attempts=2,
        generate_fn=fake_generate, verify_fn=fake_verify)

    exposed = " ".join([*queries, *feedback_seen])
    assert "secret expected #FFFFFF" not in exposed
    assert "secret-check" not in exposed
    assert "withheld check" in exposed


class FakeCollection:
    def __init__(self):
        self.updates = []

    def update_one(self, query, update, upsert=False):
        self.updates.append((query, update, upsert))


def test_duplicate_failures_are_upserted_with_occurrence_counter():
    store = object.__new__(MemoryStore)
    store.collection = FakeCollection()
    failed = CheckResult("color", "text_color", False, "hard", "expected white, got blue")
    verdict = Verdict("BLOCK", 0.0, ["hard check failed"], [failed])
    brief = Brief("demo", "White headline")

    store.remember_failure(brief, verdict, 1)
    store.remember_failure(brief, verdict, 2)

    first, second = store.collection.updates
    assert first[0] == second[0]
    assert first[0]["memory_key"].startswith("verification_failure:")
    assert first[1]["$inc"] == {"occurrences": 1}
    assert first[2] is True


def test_user_feedback_is_saved_as_three_retrievable_memory_types():
    store = object.__new__(MemoryStore)
    store.collection = FakeCollection()

    feedback_id = store.remember_user_feedback(
        "cta-review-1",
        "Keep the call-to-action inside the safe area.",
        "The button label overflowed its container.",
        "Increase the button width and add horizontal padding before rendering.",
    )

    assert len(feedback_id) == 20
    assert len(store.collection.updates) == 3
    docs = [update[1]["$set"] for update in store.collection.updates]
    assert [doc["type"] for doc in docs] == [
        "brief_requirement", "verification_failure", "successful_fix"
    ]
    assert all(doc["origin"] == "user_feedback" for doc in docs)
    assert "horizontal padding" in docs[-1]["content"]


def test_vector_search_drops_hits_below_point_seven():
    class SearchCollection:
        def aggregate(self, pipeline):
            return [
                {"type": "successful_fix", "content": "strong match",
                 "brief_id": "a", "_memory_score": 0.81},
                {"type": "successful_fix", "content": "weak match",
                 "brief_id": "b", "_memory_score": 0.69},
            ]

    store = object.__new__(MemoryStore)
    store.collection = SearchCollection()
    store.index = "frameproof_memory"

    hits = store.search("fix the color", types=("successful_fix",))
    assert [hit.content for hit in hits] == ["strong match"]
