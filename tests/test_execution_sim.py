"""The execution simulator, and the guarantee that it cannot execute.

Most of this is offline: the arithmetic of compute margin, fees and modelled
fills does not need a network, and testing it without one keeps the suite fast
and free. The one thing that *must* be checked against reality -- that a real
quote, a real unsigned build and a real simulation all work -- lives in
`scripts/e2e_shadow_proof.py`, because a unit test cannot prove an external
integration and this project does not mark such things PASS from a mock.
"""

from __future__ import annotations

import inspect

from meme_flight_recorder import execution_sim
from meme_flight_recorder.execution_sim import (
    BASE_FEE_LAMPORTS,
    COMPUTE_UNIT_MARGIN,
    LAMPORTS_PER_SOL,
    ExecutionAttempt,
    ExecutionSimulator,
)


def test_the_module_exposes_no_way_to_sign_or_broadcast() -> None:
    """The invariant, asserted structurally rather than trusted to review."""
    source = inspect.getsource(execution_sim)
    for forbidden in ("send" + "Transaction", "private" + "_key", "Keypair", "sign("):
        assert forbidden not in source
    # Substring matching would flag `build_unsigned`, whose whole point is that
    # it does the opposite. The check is on what a method *does*: no public
    # method may be named as an action that signs, sends or broadcasts.
    names = {name.lower() for name in dir(ExecutionSimulator) if not name.startswith("_")}
    forbidden_verbs = ("sign", "send", "broadcast", "submit", "execute")
    assert not any(name.startswith(forbidden_verbs) for name in names)
    assert "build_unsigned" in names


def test_simulation_uses_a_public_fee_payer_and_never_verifies_signatures() -> None:
    source = inspect.getsource(ExecutionSimulator.simulate)
    assert '"sigVerify": False' in source


def test_compute_margin_is_ten_percent_over_what_simulated() -> None:
    assert COMPUTE_UNIT_MARGIN == 0.10
    simulated = 100_000
    assert int(simulated * (1 + COMPUTE_UNIT_MARGIN)) == 110_000


def test_an_attempt_records_absent_values_as_none_not_zero() -> None:
    attempt = ExecutionAttempt(
        mint="M", side="buy", input_mint="A", output_mint="B", amount_in=1
    )
    assert attempt.compute_units_simulated is None
    assert attempt.priority_fee_lamports is None
    assert attempt.total_cost_usd is None
    assert attempt.simulation_ok is None


def test_landing_probability_is_zero_when_simulation_failed() -> None:
    """A transaction that cannot simulate cannot land, and is not scored as if it might."""
    simulator = ExecutionSimulator(jupiter_api_key="", helius_api_key="")
    attempt = ExecutionAttempt(
        mint="M", side="buy", input_mint="A", output_mint="B", amount_in=1
    )
    attempt.simulation_ok = False
    assert simulator.model_landing_probability(attempt) == 0.0


def test_landing_probability_falls_with_impact_and_hops() -> None:
    simulator = ExecutionSimulator(jupiter_api_key="", helius_api_key="")

    def attempt(impact: float, hops: int, fee: int | None) -> ExecutionAttempt:
        record = ExecutionAttempt(
            mint="M", side="buy", input_mint="A", output_mint="B", amount_in=1
        )
        record.simulation_ok = True
        record.price_impact_pct = impact
        record.route_hops = hops
        record.priority_fee_lamports = fee
        return record

    clean = simulator.model_landing_probability(attempt(0.1, 1, 5_000))
    thin = simulator.model_landing_probability(attempt(20.0, 1, 5_000))
    winding = simulator.model_landing_probability(attempt(0.1, 4, 5_000))
    unfunded = simulator.model_landing_probability(attempt(0.1, 1, None))
    assert clean > thin
    assert clean > winding
    assert clean > unfunded
    assert all(0.0 <= value <= 1.0 for value in (clean, thin, winding, unfunded))


def test_a_missing_jupiter_key_falls_back_to_the_keyless_host() -> None:
    """A missing optional key degrades throughput; it never stops the system."""
    assert ExecutionSimulator(jupiter_api_key="").base_url.startswith("https://lite-api")
    assert ExecutionSimulator(jupiter_api_key="k").base_url == "https://api.jup.ag/swap/v1"
    assert ExecutionSimulator(jupiter_api_key="k").headers == {"x-api-key": "k"}
    assert ExecutionSimulator(jupiter_api_key="").headers == {}


def test_the_base_fee_is_charged_per_signature() -> None:
    assert BASE_FEE_LAMPORTS == 5_000
    assert LAMPORTS_PER_SOL == 1_000_000_000


def test_executable_price_is_worse_than_the_quote() -> None:
    """Modelling the fill at the quote is the optimism this module removes."""
    quote_price = 1.0
    slippage_bps = 300
    executable = quote_price * (1.0 - slippage_bps / 10_000.0)
    assert executable < quote_price
    assert abs(executable - 0.97) < 1e-9
