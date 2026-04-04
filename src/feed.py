"""
Market data feed: Binance WebSocket (reference price) + Polymarket CLOB (orderbook).
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

import websockets
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderBookSummary

from .latency import tracker

logger = logging.getLogger(__name__)

BINANCE_WS_URL = "wss://stream.binance.us:9443/ws"


@dataclass
class BinancePrice:
    symbol: str
    bid: float
    ask: float
    timestamp: float


@dataclass
class PolymarketBook:
    token_id: str
    bids: list  # [(price, size), ...]
    asks: list
    timestamp: float

    @property
    def best_bid(self) -> Optional[float]:
        return float(self.bids[0][0]) if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return float(self.asks[0][0]) if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        b, a = self.best_bid, self.best_ask
        return (b + a) / 2 if b and a else None


class FeedManager:
    """
    Manages both the Binance reference-price WebSocket and Polymarket CLOB polling.
    Calls registered callbacks whenever a new data point arrives.
    """

    def __init__(
        self,
        binance_symbol: str,
        poly_token_id: str,
        clob_host: str = None,
        clob_key: str = None,
        clob_secret: str = None,
        clob_passphrase: str = None,
        poly_poll_interval: float = 0.5,
    ):
        self.binance_symbol = binance_symbol.lower()
        self.poly_token_id = poly_token_id
        self.poly_poll_interval = poly_poll_interval

        host = clob_host or os.getenv("CLOB_HOST", "https://clob.polymarket.com")
        key = clob_key or os.getenv("CLOB_API_KEY", "")
        secret = clob_secret or os.getenv("CLOB_SECRET", "")
        passphrase = clob_passphrase or os.getenv("CLOB_PASSPHRASE", "")

        creds = ApiCreds(api_key=key, api_secret=secret, api_passphrase=passphrase)
        self.clob = ClobClient(host, creds=creds)

        self._binance_callbacks: list[Callable] = []
        self._poly_callbacks: list[Callable] = []

        self.latest_binance: Optional[BinancePrice] = None
        self.latest_poly: Optional[PolymarketBook] = None
        self._running = False

    def on_binance(self, cb: Callable[[BinancePrice], None]):
        self._binance_callbacks.append(cb)

    def on_poly(self, cb: Callable[[PolymarketBook], None]):
        self._poly_callbacks.append(cb)

    async def _run_binance(self):
        stream = f"{self.binance_symbol}@bookTicker"
        url = f"{BINANCE_WS_URL}/{stream}"
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    logger.info("Binance WS connected: %s", stream)
                    async for raw in ws:
                        if not self._running:
                            break
                        with tracker.measure("feed.binance.parse"):
                            data = json.loads(raw)
                            price = BinancePrice(
                                symbol=data["s"],
                                bid=float(data["b"]),
                                ask=float(data["a"]),
                                timestamp=time.time(),
                            )
                        self.latest_binance = price
                        for cb in self._binance_callbacks:
                            with tracker.measure("feed.binance.callback"):
                                await _maybe_await(cb, price)
            except Exception as e:
                if self._running:
                    logger.warning("Binance WS error, reconnecting in 1s: %s", e)
                    await asyncio.sleep(1)

    async def _run_poly(self):
        while self._running:
            t0 = time.perf_counter()
            try:
                with tracker.measure("feed.poly.fetch"):
                    raw: OrderBookSummary = self.clob.get_order_book(self.poly_token_id)
                book = PolymarketBook(
                    token_id=self.poly_token_id,
                    bids=[(b.price, b.size) for b in (raw.bids or [])],
                    asks=[(a.price, a.size) for a in (raw.asks or [])],
                    timestamp=time.time(),
                )
                self.latest_poly = book
                for cb in self._poly_callbacks:
                    with tracker.measure("feed.poly.callback"):
                        await _maybe_await(cb, book)
            except Exception as e:
                logger.warning("Polymarket CLOB fetch error: %s", e)

            elapsed = time.perf_counter() - t0
            await asyncio.sleep(max(0, self.poly_poll_interval - elapsed))

    async def start(self):
        self._running = True
        await asyncio.gather(
            self._run_binance(),
            self._run_poly(),
        )

    async def stop(self):
        self._running = False


async def _maybe_await(cb, *args):
    result = cb(*args)
    if asyncio.iscoroutine(result):
        await result
