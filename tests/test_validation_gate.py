import csv

import pytest

from scripts.validate_lookahead import validate


def write_result(path, has_bias="False", total_signals="100") -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["strategy", "has_bias", "total_signals"])
        writer.writeheader()
        writer.writerow(
            {"strategy": "MemeBreakoutRetestStrategy", "has_bias": has_bias, "total_signals": total_signals}
        )


def test_lookahead_gate_accepts_complete_unbiased_result(tmp_path) -> None:
    result = tmp_path / "lookahead.csv"
    log = tmp_path / "lookahead.log"
    write_result(result)
    log.write_text("analysis complete")
    validate(result, log, "success", 10)


@pytest.mark.parametrize(
    ("outcome", "bias", "signals"),
    [("failure", "False", "100"), ("success", "True", "100"), ("success", "False", "2")],
)
def test_lookahead_gate_rejects_invalid_or_inconclusive_result(
    tmp_path, outcome, bias, signals
) -> None:
    result = tmp_path / "lookahead.csv"
    log = tmp_path / "lookahead.log"
    write_result(result, bias, signals)
    log.write_text("analysis complete")
    with pytest.raises(ValueError):
        validate(result, log, outcome, 10)
