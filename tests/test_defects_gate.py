from pathlib import Path

import pytest

from frameproof import gate
from frameproof.brief import Brief
from frameproof.defects import OPERATORS, mutants

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "tidepool-launch" / "index.html"


def test_every_operator_applies_to_example():
    names = {m.name for m in mutants(EXAMPLE.read_text())}
    assert names == set(OPERATORS)


def test_mutants_are_deterministic():
    html = EXAMPLE.read_text()
    assert [m.html for m in mutants(html, seed=3)] == [m.html for m in mutants(html, seed=3)]


def test_typo_never_touches_script():
    html = EXAMPLE.read_text()
    for seed in range(20):
        m = mutants(html, seed=seed, only=["typo"])[0]
        assert m.html.split("<script>")[-1] == html.split("<script>")[-1]


def test_brief_parsing():
    b = Brief(id="x", text="16:9, about 6 s. Palette #0b1f3a and #FF6B35. Scenes: 0-2 s hook, 2-4.5 s body.")
    assert b.duration == 6.0
    assert b.palette == ["#0B1F3A", "#FF6B35"]
    assert (2.0, 4.5) in b.scenes


def test_gate_adopts_clear_gain_and_rejects_noise():
    base = [0.5, 0.6, 0.55, 0.62, 0.48, 0.58, 0.51, 0.6]
    better = [s + 0.1 for s in base]
    noisy = [s + d for s, d in zip(base, [0.05, -0.06, 0.04, -0.05, 0.03, -0.04, 0.02, 0.0])]
    assert gate.decide("v1", "v2", base, better).adopt
    assert not gate.decide("v1", "v3", base, noisy).adopt
    with pytest.raises(ValueError):
        gate.paired_bootstrap([1.0], [])
