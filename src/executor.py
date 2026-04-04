"""
Order execution via py-clob-client.
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, MarketOrderArgs

from .latency import tracker
from .signal import Side

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    token_id: str
    side: str
    size: float
    price: float
    error: Optional[str] = None
    elapsed_ms: float = 0.0


class Executor:
    """
    Submits market orders to Polymarket CLOB and tracks fills.

    Uses py-clob-client's MarketOrderArgs for immediate-fill semantics.
    All calls are wrapped with latency instrumentation.
    """

    def __init__(
        self,
        clob_host: str = None,
        clob_key: str = None,
        clob_secret: str = None,
        clob_passphrase: str = None,
        dry_run: bool = False,
    ):
        host = clob_host or os.getenv("CLOB_HOST", "https://clob.polymarket.com")
        key = clob_key or os.getenv("CLOB_API_KEY", "")
        secret = clob_secret or os.getenv("CLOB_SECRET", "")
        passphrase = clob_passphrase or os.getenv("CLOB_PASSPHRASE", "")

        creds = ApiCreds(api_key=key, api_secret=secret, api_passphrase=passphrase)
        self.clob = ClobClient(host, creds=creds)
        self.dry_run = dry_run
        self._fills: list[OrderResult] = []

    async def submit(
        self,
        token_id: str,
        side: Side,
        size: float,
        price: float,
    ) -> OrderResult:
        """
        Submit a market order asynchronously.

        Parameters
        ----------
        token_id : str
            Polymarket outcome token ID.
        side : Side
            BUY or SELL.
        size : float
            Number of contracts (shares).
        price : float
            Limit price (used as taker price for market orders).
        """
        t0 = time.perf_counter()

        if self.dry_run:
            elapsed = (time.perf_counter() - t0) * 1000
            tracker.record("executor.submit", elapsed)
            result = OrderResult(
                success=True,
                order_id=f"DRY-{int(time.time()*1000)}",
                token_id=token_id,
                side=side.value,
                size=size,
                price=price,
                elapsed_ms=elapsed,
            )
            logger.info("[DRY RUN] Order: %s", result)
            self._fills.append(result)
            return result

        try:
            args = MarketOrderArgs(
                token_id=token_id,
                amount=size,
                price=price,
                side=side.value,
            )
            with tracker.measure("executor.submit"):
                resp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.clob.create_market_order(args)
                )

            elapsed = (time.perf_counter() - t0) * 1000
            result = OrderResult(
                success=True,
                order_id=resp.get("orderID") or resp.get("id"),
                token_id=token_id,
                side=side.value,
                size=size,
                price=price,
                elapsed_ms=elapsed,
            )
            logger.info("Order filled: %s in %.1fms", result.order_id, elapsed)
            self._fills.append(result)
            return result

        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            tracker.record("executor.submit", elapsed)
            logger.error("Order failed: %s", e)
            result = OrderResult(
                success=False,
                order_id=None,
                token_id=token_id,
                side=side.value,
                size=size,
                price=price,
                error=str(e),
                elapsed_ms=elapsed,
            )
            return result

    def fill_count(self) -> int:
        return sum(1 for f in self._fills if f.success)

    def recent_fills(self, n: int = 20) -> list[OrderResult]:
        return self._fills[-n:]
