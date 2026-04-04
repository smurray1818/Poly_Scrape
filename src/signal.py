"""
Signal generation: detects edge via momentum between Binance reference price
and Polymarket implied probability.
"""
import logging
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Optional

from .feed import BinancePrice, PolymarketBook
from .latency import tracker

logger = logging.getLogger(__name__)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Signal:
    side: Side
    edge: float          # raw edge in probability units (0–1)
    ref_price: float     # Binance mid
    poly_mid: float      # Polymarket mid
    timestamp: float


class MomentumSignalEngine:
    """
    Compares the Binance spot price (converted to implied prob via a user-supplied
    conversion function) against the Polymarket mid.  Fires a signal when the
    discrepancy exceeds `min_edge` and has persisted for at least `min_ticks`.

    Parameters
    ----------
    price_to_prob : callable(float) -> float
        Converts a Binance spot price to an implied probability [0, 1].
        E.g. for a BTC >$70k market: lambda p: 1.0 if p > 70_000 else 0.0
    min_edge : float
        Minimum edge (abs difference in prob) to generate a signal.
    min_ticks : int
        Minimum consecutive ticks the edge must be present before signalling.
    momentum_window : int
        Number of Binance ticks used to compute price momentum (EWM direction filter).
    """

    def __init__(
        self,
        price_to_prob,
        min_edge: float = 0.02,
        min_ticks: int = 2,
        momentum_window: int = 10,
    ):
        self._price_to_prob = price_to_prob
        self.min_edge = min_edge
        self.min_ticks = min_ticks
        self._momentum_window = momentum_window

        self._binance_prices: Deque[float] = deque(maxlen=momentum_window)
        self._consecutive: int = 0
        self._last_side: Optional[Side] = None

        self.latest_binance: Optional[BinancePrice] = None
        self.latest_poly: Optional[PolymarketBook] = None
        self.latest_signal: Optional[Signal] = None

    # ------------------------------------------------------------------ #
    # Feed callbacks                                                       #
    # ------------------------------------------------------------------ #

    def on_binance(self, price: BinancePrice):
        self.latest_binance = price
        self._binance_prices.append((price.bid + price.ask) / 2)

    def on_poly(self, book: PolymarketBook) -> Optional[Signal]:
        self.latest_poly = book
        return self._evaluate()

    # ------------------------------------------------------------------ #
    # Core logic                                                           #
    # ------------------------------------------------------------------ #

    def _momentum_ok(self, side: Side) -> bool:
        """Simple momentum filter: require the last N prices to trend in signal direction."""
        if len(self._binance_prices) < self._momentum_window:
            return True  # not enough data yet — pass through
        prices = list(self._binance_prices)
        rising = prices[-1] > prices[0]
        if side == Side.BUY:
            return rising
        return not rising

    def _evaluate(self) -> Optional[Signal]:
        with tracker.measure("signal.evaluate"):
            if self.latest_binance is None or self.latest_poly is None:
                return None

            poly_mid = self.latest_poly.mid
            if poly_mid is None:
                return None

            binance_mid = (self.latest_binance.bid + self.latest_binance.ask) / 2
            ref_prob = self._price_to_prob(binance_mid)

            edge = ref_prob - poly_mid  # positive => poly underpriced => BUY
            abs_edge = abs(edge)

            if abs_edge < self.min_edge:
                self._consecutive = 0
                self._last_side = None
                return None

            side = Side.BUY if edge > 0 else Side.SELL

            if not self._momentum_ok(side):
                logger.debug("Edge %.4f present but momentum filter blocked %s", abs_edge, side)
                self._consecutive = 0
                return None

            if side == self._last_side:
                self._consecutive += 1
            else:
                self._consecutive = 1
                self._last_side = side

            if self._consecutive < self.min_ticks:
                return None

            sig = Signal(
                side=side,
                edge=abs_edge,
                ref_price=binance_mid,
                poly_mid=poly_mid,
                timestamp=time.time(),
            )
            self.latest_signal = sig
            logger.info(
                "Signal: %s edge=%.4f ref=%.4f poly_mid=%.4f",
                side, abs_edge, binance_mid, poly_mid,
            )
            return sig
