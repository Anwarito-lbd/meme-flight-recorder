"""The full paper execution path: quote, build, simulate, model the fill.

Every earlier expectancy number in this project used the quoted price as the
fill. That is not a fill. Between a quote and a landed trade sit compute units,
a base fee, a priority fee, a tip, a slippage band, and a real probability that
the transaction never lands at all -- and at a $0.80 position those terms are
not rounding, they are most of the edge.

This module closes that gap without ever touching a key:

    quote -> unsigned transaction -> RPC simulation -> compute units
          -> +10% CU margin -> priority fee -> modeled tip
          -> landing probability -> modeled fill

**It cannot sign and it cannot send.** There is no signer, no keypair, no
broadcast RPC call and no code path that could acquire one. The tripwire in
`tests/test_no_live_execution.py` scans this source for the literal names of the
broadcast method and of key material, so those words are deliberately absent
here rather than merely unused. Simulation runs with
``sigVerify: false`` against a *public* fee payer, which is why no private key is
required: the RPC is being asked "what would this cost", not "please do this".
`tests/test_no_live_execution.py` stays green and unmodified.

The fee payer is a real, funded, public mainnet account used **read-only** as a
placeholder so the simulator can measure compute units. Nothing is ever signed
on its behalf and it is never credited or debited; a simulation mutates nothing.
Using an unfunded placeholder instead returns `InvalidAccountForFee` and zero
compute units, which would silently report every trade as free.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

JUPITER_KEYED = "https://api.jup.ag/swap/v1"
JUPITER_LITE = "https://lite-api.jup.ag/swap/v1"

# A funded public mainnet account, used only as a simulation fee payer so that
# compute units can be measured. Verified at 10,755,444 SOL when chosen. It is
# never signed for; `sigVerify` is false and a simulation changes no state.
REFERENCE_FEE_PAYER = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"

# The margin the request specifies over simulated compute. A transaction that
# requests exactly what it simulated fails whenever the account state shifts
# between simulation and landing, which on a launching token is most of the time.
COMPUTE_UNIT_MARGIN = 0.10

LAMPORTS_PER_SOL = 1_000_000_000
# Charged per signature by the runtime. One signer for a Jupiter swap.
BASE_FEE_LAMPORTS = 5_000


@dataclass
class ExecutionAttempt:
    """Everything measured about one modelled execution. All of it is recorded."""

    mint: str
    side: str
    input_mint: str
    output_mint: str
    amount_in: int

    quote_price: float | None = None
    executable_price: float | None = None
    price_impact_pct: float | None = None
    slippage_bps: int = 0
    route: tuple[str, ...] = ()
    route_hops: int = 0

    compute_units_simulated: int | None = None
    compute_units_requested: int | None = None
    base_fee_lamports: int = BASE_FEE_LAMPORTS
    priority_fee_lamports: int | None = None
    jito_tip_lamports: int | None = None
    total_cost_lamports: int | None = None
    total_cost_usd: float | None = None

    simulation_ok: bool | None = None
    simulation_error: str | None = None
    landing_probability: float | None = None
    modeled_fill_price: float | None = None

    quote_latency_ms: float | None = None
    build_latency_ms: float | None = None
    simulate_latency_ms: float | None = None
    total_latency_ms: float | None = None

    failure: str | None = None
    stages: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        record = asdict(self)
        record["route"] = list(self.route)
        return record


def _post(url: str, body: dict[str, Any], headers: dict[str, str], timeout: int = 20) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _get(url: str, headers: dict[str, str], timeout: int = 20) -> Any:
    request = urllib.request.Request(url, headers={"accept": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


class ExecutionSimulator:
    """Models what a swap would cost and whether it would land. Never executes."""

    def __init__(
        self,
        jupiter_api_key: str | None = None,
        helius_api_key: str | None = None,
        fee_payer: str = REFERENCE_FEE_PAYER,
        sol_price_usd: float = 150.0,
    ) -> None:
        self.jupiter_api_key = (
            jupiter_api_key if jupiter_api_key is not None else os.getenv("JUPITER_API_KEY", "")
        )
        self.helius_api_key = (
            helius_api_key if helius_api_key is not None else os.getenv("HELIUS_API_KEY", "")
        )
        self.base_url = JUPITER_KEYED if self.jupiter_api_key else JUPITER_LITE
        self.fee_payer = fee_payer
        self.sol_price_usd = sol_price_usd

    @property
    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.jupiter_api_key} if self.jupiter_api_key else {}

    @property
    def rpc_url(self) -> str:
        return f"https://mainnet.helius-rpc.com/?api-key={self.helius_api_key}"

    def quote(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int) -> Any:
        url = (
            f"{self.base_url}/quote?inputMint={input_mint}&outputMint={output_mint}"
            f"&amount={amount}&slippageBps={slippage_bps}&restrictIntermediateTokens=true"
        )
        return _get(url, self.headers)

    def build_unsigned(self, quote: Any) -> Any:
        """Ask Jupiter for the transaction bytes. It is never signed."""
        return _post(
            f"{self.base_url}/swap",
            {
                "quoteResponse": quote,
                "userPublicKey": self.fee_payer,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
            },
            self.headers,
        )

    def simulate(self, transaction_base64: str) -> Any:
        """`sigVerify: false` is what makes this possible without a key."""
        return _post(
            self.rpc_url,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "simulateTransaction",
                "params": [
                    transaction_base64,
                    {
                        "encoding": "base64",
                        "replaceRecentBlockhash": True,
                        "sigVerify": False,
                        "commitment": "processed",
                    },
                ],
            },
            {},
        )

    def priority_fee_lamports(self, compute_units: int) -> int | None:
        """Recent priority fee for this account set, converted to a total.

        The RPC reports micro-lamports **per compute unit**, so it only becomes
        a cost once multiplied by the units actually requested. Reporting the
        raw figure as a fee is off by six orders of magnitude.
        """
        try:
            payload = _post(
                self.rpc_url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getRecentPrioritizationFees",
                    "params": [[self.fee_payer]],
                },
                {},
            )
        except Exception:  # noqa: BLE001 - a missing fee estimate is None, never zero
            return None
        samples = [
            int(row.get("prioritizationFee") or 0)
            for row in (payload.get("result") or [])
            if row.get("prioritizationFee") is not None
        ]
        if not samples:
            return None
        samples.sort()
        # The median recent fee. A mean is dragged by single spiking blocks.
        median_micro_lamports = samples[len(samples) // 2]
        return int(median_micro_lamports * compute_units / 1_000_000)

    def model_landing_probability(self, attempt: ExecutionAttempt) -> float:
        """How likely this transaction is to land, from what was measured.

        Deliberately crude and deliberately pessimistic, because the honest
        input -- our own landed/dropped history -- does not exist yet: nothing
        has ever been broadcast. It is a *model*, labelled as one, and it is
        recorded per attempt so it can be replaced with measured landing rates
        the moment a live adapter produces any.
        """
        if attempt.simulation_ok is not True:
            return 0.0
        probability = 0.90
        if (attempt.price_impact_pct or 0.0) > 5.0:
            # High impact means a thin pool, where competing flow moves the
            # price past the slippage band before the transaction lands.
            probability -= 0.25
        if attempt.route_hops > 2:
            probability -= 0.10
        if not attempt.priority_fee_lamports:
            probability -= 0.15
        return max(0.05, min(0.99, probability))

    def attempt(
        self,
        mint: str,
        side: str,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 100,
    ) -> ExecutionAttempt:
        """Run the whole modelled path and record every stage.

        Timing uses `perf_counter_ns` throughout. The wall clock on this host
        has 16ms granularity, which reported whole pipeline stages as "0ms" and
        is not a measurement.
        """
        record = ExecutionAttempt(
            mint=mint,
            side=side,
            input_mint=input_mint,
            output_mint=output_mint,
            amount_in=amount,
            slippage_bps=slippage_bps,
        )
        overall = time.perf_counter_ns()

        started = time.perf_counter_ns()
        try:
            quote = self.quote(input_mint, output_mint, amount, slippage_bps)
        except Exception as error:  # noqa: BLE001
            record.failure = f"quote_failed: {type(error).__name__}"
            record.quote_latency_ms = (time.perf_counter_ns() - started) / 1e6
            return record
        record.quote_latency_ms = (time.perf_counter_ns() - started) / 1e6
        record.stages["quote_ms"] = record.quote_latency_ms

        out_amount = int(quote.get("outAmount") or 0)
        if not out_amount:
            record.failure = "no_route"
            return record
        record.quote_price = out_amount / amount
        record.price_impact_pct = (
            float(quote["priceImpactPct"]) * 100.0 if quote.get("priceImpactPct") else None
        )
        plan = quote.get("routePlan") or []
        record.route = tuple(
            str((hop.get("swapInfo") or {}).get("label") or "?") for hop in plan
        )
        record.route_hops = len(plan)

        started = time.perf_counter_ns()
        try:
            built = self.build_unsigned(quote)
        except Exception as error:  # noqa: BLE001
            record.failure = f"build_failed: {type(error).__name__}"
            return record
        record.build_latency_ms = (time.perf_counter_ns() - started) / 1e6
        record.stages["build_ms"] = record.build_latency_ms

        transaction = built.get("swapTransaction")
        if not transaction:
            record.failure = "no_transaction_returned"
            return record
        # Proof the bytes are real, and a cheap guard against a provider
        # returning something that is not a transaction at all.
        record.stages["tx_bytes"] = float(len(base64.b64decode(transaction)))

        started = time.perf_counter_ns()
        try:
            simulated = self.simulate(transaction)
        except Exception as error:  # noqa: BLE001
            record.failure = f"simulation_failed: {type(error).__name__}"
            return record
        record.simulate_latency_ms = (time.perf_counter_ns() - started) / 1e6
        record.stages["simulate_ms"] = record.simulate_latency_ms

        value = (simulated.get("result") or {}).get("value") or {}
        error = value.get("err")
        record.simulation_ok = error is None
        record.simulation_error = json.dumps(error) if error else None
        units = value.get("unitsConsumed")
        record.compute_units_simulated = int(units) if units is not None else None
        if record.compute_units_simulated:
            record.compute_units_requested = int(
                record.compute_units_simulated * (1.0 + COMPUTE_UNIT_MARGIN)
            )
            record.priority_fee_lamports = self.priority_fee_lamports(
                record.compute_units_requested
            )

        # Jito is modelled, never submitted. A tip only buys anything on a
        # bundle path this project cannot reach at $10-40 of capital, so it is
        # recorded at zero and kept as a field so a future live adapter has
        # somewhere to put the real number.
        record.jito_tip_lamports = 0

        record.total_cost_lamports = (
            record.base_fee_lamports
            + (record.priority_fee_lamports or 0)
            + (record.jito_tip_lamports or 0)
        )
        record.total_cost_usd = (
            record.total_cost_lamports / LAMPORTS_PER_SOL * self.sol_price_usd
        )

        # The executable price is the quote after the slippage the trade is
        # willing to accept. Modelling the fill *at* the quote is the optimism
        # this module exists to remove.
        record.executable_price = record.quote_price * (1.0 - slippage_bps / 10_000.0)
        record.landing_probability = self.model_landing_probability(record)
        record.modeled_fill_price = record.executable_price

        record.total_latency_ms = (time.perf_counter_ns() - overall) / 1e6
        record.stages["total_ms"] = record.total_latency_ms
        return record
