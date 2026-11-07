"""Browser tests: need `python -m playwright install chromium`."""

from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "tidepool-launch"

pytest.importorskip("playwright")
pytestmark = pytest.mark.browser


def test_probe_sees_scenes_at_the_right_times():
    from frameproof.checks import find_text
    from frameproof.probe import probe

    obs = probe(EXAMPLE / "index.html", times=[1.2, 3.3, 5.4])
    assert obs.timeline_ok and not obs.errors
    assert (obs.width, obs.height, obs.duration) == (1920, 1080, 6.0)
    W, H = obs.width, obs.height
    assert find_text(obs.frames[1.2], "Notes that think", W, H)
    assert not find_text(obs.frames[1.2], "Try Tidepool free", W, H)
    assert find_text(obs.frames[3.3], "Auto-link every idea", W, H)
    assert find_text(obs.frames[5.4], "Try Tidepool free", W, H)


def test_seek_is_deterministic():
    from frameproof.checks import frame_change
    from frameproof.probe import probe

    a = probe(EXAMPLE / "index.html", times=[0.4, 2.6])
    b = probe(EXAMPLE / "index.html", times=[2.6, 0.4])  # seek order must not matter
    assert frame_change(a.frames[0.4], b.frames[0.4]) == 0.0
    assert frame_change(a.frames[2.6], b.frames[2.6]) == 0.0
