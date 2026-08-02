from __future__ import annotations

from .models import Lifecycle, TokenSnapshot, Universe


def infer_lifecycle(snapshot: TokenSnapshot) -> Lifecycle:
    if snapshot.universe == Universe.CEX_ESTABLISHED:
        return Lifecycle.ESTABLISHED
    if snapshot.developer_selling is True or snapshot.connected_wallet_risk is True:
        return Lifecycle.DISTRIBUTION_DETECTED
    if snapshot.liquidity_usd is not None and snapshot.liquidity_usd < 5_000:
        return Lifecycle.COLLAPSED_OR_INACTIVE
    if snapshot.graduated is not True:
        progress = snapshot.bonding_curve_progress_pct
        if progress is None or progress < 5:
            return Lifecycle.LAUNCHED
        if progress >= 85:
            return Lifecycle.NEAR_GRADUATION
        return Lifecycle.BONDING_CURVE_ACTIVE
    if (snapshot.buyer_growth_pct or 0) > 25 and (snapshot.price_momentum or 0) > 0:
        return Lifecycle.MOMENTUM_CONFIRMED
    if (snapshot.liquidity_growth_pct or 0) > 15:
        return Lifecycle.LIQUIDITY_EXPANDING
    return Lifecycle.GRADUATED
