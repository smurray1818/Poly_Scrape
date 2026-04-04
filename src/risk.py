"""
Pre-trade and portfolio-level risk checks.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    max_drawdown_pct: float = 0.10        # halt if portfolio drops > 10% from peak
    max_daily_loss_pct: float = 0.05      # halt if daily P&L < -5% of starting bankroll
    max_open_positions: int = 5           # max concurrent positions
    max_position_pct: float = 0.05        # max single position as fraction of bankroll
    max_notional_per_trade: float = 500.0 # hard USD cap per order
    min_edge: float = 0.015               # reject signals below this edge
    max_spread_pct: float = 0.10          # reject if Polymarket spread > 10%
    cooldown_seconds: float = 5.0         # min seconds between fills on same token


@dataclass
class RejectionReason:
    code: str
    detail: str


class RiskManager:
    """
    Stateful risk gate.  Call check() before every order.
    Must be notified of fills and P&L updates via record_fill() and update_pnl().
    """

    def __init__(self, bankroll: float, config: Optional[RiskConfig] = None):
        self.bankroll = bankroll
        self.peak_bankroll = bankroll
        self.daily_start_bankroll = bankroll
        self.config = config or RiskConfig()

        self._open_positions: Dict[str, float] = {}  # token_id -> notional
        self._last_fill_time: Dict[str, float] = {}  # token_id -> epoch
        self._halted = False
        self._halt_reason: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def check(
        self,
        token_id: str,
        side: str,
        notional: float,
        edge: float,
        poly_bid: Optional[float],
        poly_ask: Optional[float],
    ) -> Tuple[bool, Optional[RejectionReason]]:
        """
        Returns (True, None) if trade passes all checks,
        or (False, RejectionReason) if rejected.
        """
        if self._halted:
            return False, RejectionReason("HALTED", self._halt_reason or "risk halt active")

        # Edge floor
        if edge < self.config.min_edge:
            return False, RejectionReason("LOW_EDGE", f"edge {edge:.4f} < {self.config.min_edge}")

        # Spread check
        if poly_bid and poly_ask and poly_ask > 0:
            spread_pct = (poly_ask - poly_bid) / poly_ask
            if spread_pct > self.config.max_spread_pct:
                return False, RejectionReason(
                    "WIDE_SPREAD", f"spread {spread_pct:.2%} > {self.config.max_spread_pct:.2%}"
                )

        # Notional cap
        if notional > self.config.max_notional_per_trade:
            return False, RejectionReason(
                "MAX_NOTIONAL",
                f"notional ${notional:.2f} > ${self.config.max_notional_per_trade:.2f}",
            )

        # Max open positions
        if len(self._open_positions) >= self.config.max_open_positions and token_id not in self._open_positions:
            return False, RejectionReason(
                "MAX_POSITIONS",
                f"already {len(self._open_positions)} open positions",
            )

        # Cooldown
        last = self._last_fill_time.get(token_id, 0)
        if time.time() - last < self.config.cooldown_seconds:
            return False, RejectionReason(
                "COOLDOWN",
                f"cooldown active for {token_id}",
            )

        # Drawdown gate
        drawdown = (self.peak_bankroll - self.bankroll) / self.peak_bankroll
        if drawdown > self.config.max_drawdown_pct:
            self._halt(f"max drawdown {drawdown:.2%}")
            return False, RejectionReason("DRAWDOWN", f"drawdown {drawdown:.2%}")

        # Daily loss gate
        daily_loss_pct = (self.daily_start_bankroll - self.bankroll) / self.daily_start_bankroll
        if daily_loss_pct > self.config.max_daily_loss_pct:
            self._halt(f"daily loss {daily_loss_pct:.2%}")
            return False, RejectionReason("DAILY_LOSS", f"daily loss {daily_loss_pct:.2%}")

        return True, None

    def record_fill(self, token_id: str, notional: float, realized_pnl: float = 0.0):
        self._open_positions[token_id] = notional
        self._last_fill_time[token_id] = time.time()
        self.bankroll += realized_pnl
        self.peak_bankroll = max(self.peak_bankroll, self.bankroll)

    def close_position(self, token_id: str, realized_pnl: float):
        self._open_positions.pop(token_id, None)
        self.bankroll += realized_pnl
        self.peak_bankroll = max(self.peak_bankroll, self.bankroll)

    def reset_daily(self):
        self.daily_start_bankroll = self.bankroll

    def status(self) -> Dict:
        return {
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "bankroll": round(self.bankroll, 2),
            "peak_bankroll": round(self.peak_bankroll, 2),
            "drawdown_pct": round(
                (self.peak_bankroll - self.bankroll) / self.peak_bankroll * 100, 2
            ),
            "open_positions": len(self._open_positions),
        }

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _halt(self, reason: str):
        self._halted = True
        self._halt_reason = reason
        logger.critical("RISK HALT: %s", reason)
