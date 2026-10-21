"""Pytest gate for the cache-release check's verdict: when has LAS X let go of the captures?

Each capture leaves a hidden copy in LAS X's cache, at least the capture's
own size, and only clearing the Navigator canvas lets go of it. So a run
passes when every capture was saved, every Clear press went through, and the
hidden copies did not grow: a press that goes through but frees nothing has
to fail too.
"""

import check_cache_release


def test_a_run_that_kept_the_cache_flat_passes():
    checks = check_cache_release.verdict(
        captures=10, done=10, errors=[], not_cleared=0, hidden_grown_mb=-11.1
    )
    assert all(checks.values()), checks


def test_hidden_copies_that_pile_up_fail_even_when_every_press_went_through():
    checks = check_cache_release.verdict(
        captures=10, done=10, errors=[], not_cleared=0, hidden_grown_mb=12.4
    )
    assert checks["the hidden copies did not grow"] is False


def test_a_margin_well_below_one_capture_is_allowed():
    checks = check_cache_release.verdict(
        captures=10, done=10, errors=[], not_cleared=0, hidden_grown_mb=0.4
    )
    assert checks["the hidden copies did not grow"] is True


def test_a_press_that_did_not_go_through_fails():
    checks = check_cache_release.verdict(
        captures=10, done=10, errors=[], not_cleared=1, hidden_grown_mb=0.0
    )
    assert checks["the canvas cleared at every release"] is False


def test_a_failed_capture_fails():
    checks = check_cache_release.verdict(
        captures=10,
        done=10,
        errors=["capture 3: no picture"],
        not_cleared=0,
        hidden_grown_mb=0.0,
    )
    assert checks["every capture saved"] is False
