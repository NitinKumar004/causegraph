"""scoring.combine: [0,1] clamp, monotonicity, provenance ordering, min_source."""
from causegraph.graph import scoring


def test_clamped_to_unit_interval():
    assert scoring.combine(2.0, "native", 0, 10) == 1.0   # base>1 clamped
    assert scoring.combine(-1.0, "native", 0, 10) == 0.0  # base<0 clamped
    v = scoring.combine(0.5, "poll", 5, 10)
    assert 0.0 <= v <= 1.0


def test_provenance_ordering():
    # native >= fsnotify >= poll for identical base/dt
    n = scoring.combine(0.9, "native", 0, 10)
    f = scoring.combine(0.9, "fsnotify", 0, 10)
    p = scoring.combine(0.9, "poll", 0, 10)
    assert n >= f >= p
    assert n == 0.9 and abs(f - 0.81) < 1e-9 and abs(p - 0.72) < 1e-9


def test_monotonic_in_base_and_time():
    assert scoring.combine(0.9, "poll", 0, 10) > scoring.combine(0.5, "poll", 0, 10)
    # smaller dt (fresher) -> higher confidence
    assert scoring.combine(0.9, "poll", 1, 100) > scoring.combine(0.9, "poll", 50, 100)


def test_min_source_picks_weaker():
    assert scoring.min_source("fsnotify", "poll") == "poll"
    assert scoring.min_source("native", "fsnotify") == "fsnotify"


def test_zero_half_life_step_function():
    assert scoring.combine(0.9, "native", 0, 0) == 0.9   # same instant
    assert scoring.combine(0.9, "native", 1, 0) == 0.0   # any lag -> 0
