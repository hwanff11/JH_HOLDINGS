import pandas as pd

from scripts.research_initial_entry_scenarios import (
    _market_context,
    _select_current_like_rows,
    _worst_lump_comparison,
)


def test_market_context_uses_only_history_through_timestamp():
    index = pd.bdate_range("2025-01-02", periods=260)
    qqq = pd.DataFrame({"close": [100.0 + i for i in range(len(index))]}, index=index)
    targets = pd.DataFrame({"leverage": [1.5] * len(index)}, index=index)

    context = _market_context(qqq, targets, index[-1])

    assert context["prior_leverage"] == 1.5
    assert context["qqq_above_sma200"] is True
    assert context["qqq_high_bucket"] == "HIGH_0_5"
    assert context["qqq_drawdown_252_high_pct"] == 0.0


def test_current_like_selection_relaxes_only_when_sample_count_is_small():
    rows = []
    for day in range(40):
        for scenario in ("LUMP_100", "CURRENT_50_75_100_3D"):
            rows.append(
                {
                    "start_date": f"2025-01-{day + 1:02d}",
                    "scenario": scenario,
                    "prior_leverage": 1.5,
                    "context_qqq_above_sma200": True,
                    "context_qqq_high_bucket": "HIGH_0_5",
                }
            )
    latest = {
        "prior_leverage": 1.5,
        "qqq_above_sma200": True,
        "qqq_high_bucket": "HIGH_0_5",
    }

    selected, criteria, samples = _select_current_like_rows(rows, latest, minimum_samples=30)

    assert samples == 40
    assert criteria.startswith("same leverage")
    assert len(selected) == 80


def test_worst_lump_comparison_uses_same_start_dates_for_all_scenarios():
    rows = []
    for index, mdd in enumerate((-0.30, -0.20, -0.10)):
        start = f"2025-01-0{index + 1}"
        rows.extend(
            [
                {
                    "start_date": start,
                    "scenario": "LUMP_100",
                    "return_21": -0.01,
                    "mdd_21": mdd,
                    "return_63": -0.05 - index * 0.01,
                    "mdd_63": mdd,
                    "return_126": 0.01,
                    "mdd_126": mdd,
                    "trade_count": 1,
                    "prior_leverage": 1.0,
                },
                {
                    "start_date": start,
                    "scenario": "CURRENT_50_75_100_3D",
                    "return_21": 0.0,
                    "mdd_21": mdd + 0.05,
                    "return_63": -0.02,
                    "mdd_63": mdd + 0.05,
                    "return_126": 0.02,
                    "mdd_126": mdd + 0.05,
                    "trade_count": 3,
                    "prior_leverage": 1.0,
                },
            ]
        )

    starts, summary = _worst_lump_comparison(rows, count=2)

    assert [row["start_date"] for row in starts] == ["2025-01-01", "2025-01-02"]
    assert all(row["current_mdd_improvement_63_pctpt"] > 0 for row in starts)
    assert {row["scenario"] for row in summary} == {
        "LUMP_100",
        "CURRENT_50_75_100_3D",
    }
