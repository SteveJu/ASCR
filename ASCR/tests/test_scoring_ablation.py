"""Tests for scoring ablation metrics."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scoring_ablation import _evaluate_rows, _date_splits


def _rows():
    rows = []
    for d in ["2026-01-01", "2026-01-08", "2026-01-15", "2026-01-22"]:
        for i in range(10):
            rows.append({
                "date": d,
                "ticker": f"T{i}",
                "forward_return": i - 5,
                "baseline_score": i,
                "baseline_score_rating": "B" if i >= 5 else "C",
            })
    return rows


def test_evaluate_rows_positive_ic_and_spread():
    result = _evaluate_rows(_rows(), "baseline_score")
    assert result["sample_size"] == 40
    assert result["ic"] > 0.9
    assert result["top5"]["spread"] > 0
    assert result["rating_distribution"]["B"] == 20


def test_date_splits_cover_all_dates():
    splits = _date_splits(_rows(), folds=2)
    covered = set()
    for split in splits:
        covered.update(split["dates"])
    assert covered == {"2026-01-01", "2026-01-08", "2026-01-15", "2026-01-22"}


if __name__ == "__main__":
    test_evaluate_rows_positive_ic_and_spread()
    test_date_splits_cover_all_dates()
    print("All scoring ablation tests passed!")
