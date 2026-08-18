"""Send a scout decision to Telegram.

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` have been sitting in `.env` unused
since the project started -- the system matrix has carried "Notifications:
NOT_IMPLEMENTED, TELEGRAM_BOT_TOKEN present in .env but unused" as row 77 for as
long as it has existed. This is that row.

**Why this matters more than it looks.** The evidence gate reads 0 of 30 forward
trades, so the scout cannot open a position and will not for some time. Until it
can, an alert *is* the product: it carries every signal, entry, stop and target
the mandate produces, in real time, at zero risk -- and the stream of alerts is
also the forward record the gate needs in order to ever be satisfied. A bot that
tells you what it would have done, honestly, is the only useful thing a bot can
be before it has earned the right to trade.

Two rules this module holds to:

  * **Off unless configured.** A missing token is `NOT_CONFIGURED`, which is a
    distinct outcome from a send that failed. Absence of credentials is not a
    delivery failure and must not be counted as one.
  * **A failed send is journalled, never swallowed.** Silence about a dropped
    alert is how an operator concludes the market was quiet when the network was.

Sending is deliberately not automatic anywhere in this codebase: the caller must
pass `enabled=True`, and nothing sets that by default.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import StrEnum

TELEGRAM_API = "https://api.telegram.org"


class DeliveryState(StrEnum):
    SENT = "sent"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    FAILED = "failed"


@dataclass(frozen=True)
class DeliveryResult:
    state: DeliveryState
    detail: str | None = None

    @property
    def delivered(self) -> bool:
        return self.state is DeliveryState.SENT


def credentials() -> tuple[str | None, str | None]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or None
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or None
    return token, chat_id


def configured() -> bool:
    token, chat_id = credentials()
    return bool(token and chat_id)


def format_decision(
    symbol: str,
    mint: str,
    verdict: str,
    present: int,
    required: int,
    unknown: int,
    level: str,
    reasons: tuple[str, ...] = (),
    position_usd: float | None = None,
) -> str:
    """One decision as a message.

    The unknown count is in the headline rather than a footnote. A reader told
    "3 of 2 signals" would reasonably assume the other evidence was checked and
    found wanting; told "3 of 2, 11 unknown" they can see that most of the picture
    was never available. That distinction is the whole discipline of this project
    and it does not survive being summarised away.

    The mint is included in full because identity is the mint address, never the
    ticker -- five CATE mints appeared here in one night.
    """
    safe_symbol = symbol.encode("ascii", "replace").decode("ascii")[:16] or "?"
    lines = [
        f"{verdict.upper()} {safe_symbol}",
        f"level {level} | confluence {present}/{required} | {unknown} unknown",
        f"mint {mint}",
    ]
    if position_usd is not None:
        lines.append(f"size ${position_usd:.2f} (paper)")
    for reason in reasons[:3]:
        lines.append(f"- {reason}")
    lines.append("PAPER ONLY - nothing was signed or broadcast.")
    return "\n".join(lines)


def send(message: str, *, enabled: bool = False, timeout: int = 10) -> DeliveryResult:
    """Deliver one message, or say precisely why it was not delivered.

    `enabled` defaults to False so that importing this module, or calling it from
    a study, cannot message anyone. Outbound messages are a side effect on the
    world and this project's default for those is off.
    """
    if not enabled:
        return DeliveryResult(DeliveryState.DISABLED, "caller did not enable sending")
    token, chat_id = credentials()
    if not token or not chat_id:
        missing = "TELEGRAM_BOT_TOKEN" if not token else "TELEGRAM_CHAT_ID"
        return DeliveryResult(DeliveryState.NOT_CONFIGURED, f"{missing} is absent")

    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}
    ).encode()
    request = urllib.request.Request(
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        # Telegram puts the useful part in the body, not the status line -- the
        # same shape as Birdeye's quota message arriving inside a 400.
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 - body is best effort
            detail = ""
        return DeliveryResult(DeliveryState.FAILED, f"HTTP {error.code}: {detail}")
    except Exception as error:  # noqa: BLE001 - transport failure, reported not swallowed
        return DeliveryResult(DeliveryState.FAILED, f"{type(error).__name__}: {error}")

    if not body.get("ok"):
        return DeliveryResult(DeliveryState.FAILED, str(body.get("description"))[:200])
    return DeliveryResult(DeliveryState.SENT)
