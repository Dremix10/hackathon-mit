"""Real-API spend excludes mock and scripted-only runs."""

from coop.eval.spend import HARD_USD, WARN_USD, flag_for, is_real_api_run, render_spend, tally_metas


def _meta(model: str, usd: float, driver: str = "scripted") -> dict:
    return {
        "total_usd": usd,
        "insider_driver": driver,
        "agents": [
            {"id": "a0", "model": "scripted"},
            {"id": "a1", "model": model},
        ],
    }


def test_mock_and_scripted_only_are_excluded():
    assert is_real_api_run(_meta("mock", 9)) is False
    assert is_real_api_run(_meta("scripted", 9)) is False
    assert is_real_api_run(_meta("scripted", 4, driver="llm")) is True
    tally = tally_metas(
        [
            _meta("mock", 50),
            _meta("scripted", 25),
            _meta("claude-test", 30),
            _meta("claude-test", 40),
        ]
    )
    assert tally.n_excluded_runs == 2
    assert tally.n_real_runs == 2
    assert tally.total_usd == 70
    assert tally.flag == "ok"


def test_warn_and_hard_flags():
    assert flag_for(WARN_USD - 0.01) == "ok"
    assert flag_for(WARN_USD) == "warn"
    assert flag_for(HARD_USD - 0.01) == "warn"
    assert flag_for(HARD_USD) == "hard"
    warned = tally_metas([_meta("claude-test", 80)])
    assert warned.flag == "warn"
    assert "WARN" in render_spend(warned)
    capped = tally_metas([_meta("claude-test", 100)])
    assert capped.flag == "hard"
    assert "HARD FLAG" in render_spend(capped)
