"""
Paper trading engine.

Simulates fills at the signal price, resolves positions at window rollover
using the last observed CLOB mid as a proxy for the binary outcome, and
records every trade to logs/paper_trades.csv.
"""
import csv
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CSV_HEADERS = [
    "timestamp", "asset", "token_id", "side",
    "size", "entry_price", "exit_price", "pnl",
    "running_pnl", "win",
    "total_trades", "wins", "win_rate_pct", "cumulative_return_pct",
]


@dataclass
class PaperPosition:
    asset: str
    token_id: str
    side: str          # "BUY" or "SELL"
    size: float
    entry_price: float
    opened_at: float   # epoch
    last_mid: float    # updated on every book tick


class PaperTrader:
    """
    Tracks simulated fills and computes P&L when each 15-minute window closes.

    P&L model (binary $0/$1 contract):
        BUY  at p → cost p*size; exit mid e → pnl = (e - p) * size
        SELL at p → receive p*size; exit mid e → pnl = (p - e) * size

    A trade is a WIN when pnl > 0.
    At window rollover the last observed mid is used as the exit price —
    if the market has resolved, mid will be near 0 or 1.
    """

    def __init__(self, starting_bankroll: float, csv_path: str):
        self.starting_bankroll = starting_bankroll
        self.bankroll = starting_bankroll
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        self._open: dict[str, PaperPosition] = {}  # token_id → position
        self._total_trades = 0
        self._wins = 0
        self._running_pnl = 0.0

        self._init_csv()

    # ------------------------------------------------------------------ #
    # Feed callbacks                                                       #
    # ------------------------------------------------------------------ #

    def update_mid(self, token_id: str, mid: float):
        """Call on every CLOB book update to keep last_mid current."""
        if token_id in self._open:
            self._open[token_id].last_mid = mid

    def record_fill(self, asset: str, token_id: str, side: str, size: float, entry_price: float):
        """Open a new paper position. One position per token (last write wins if called twice)."""
        if token_id in self._open:
            logger.debug("Paper: already holding %s — skipping duplicate fill", token_id[-8:])
            return
        self._open[token_id] = PaperPosition(
            asset=asset,
            token_id=token_id,
            side=side,
            size=size,
            entry_price=entry_price,
            opened_at=time.time(),
            last_mid=entry_price,
        )
        logger.info(
            "📄 Paper fill  | %s %s %s  size=%.2f  @%.4f",
            asset, side, token_id[-8:], size, entry_price,
        )

    def close_position(self, token_id: str):
        """
        Close an open paper position using its last_mid as exit price.
        Called when the 15-minute window rolls over.
        """
        pos = self._open.pop(token_id, None)
        if pos is None:
            return

        exit_price = pos.last_mid
        if pos.side == "BUY":
            pnl = (exit_price - pos.entry_price) * pos.size
        else:  # SELL
            pnl = (pos.entry_price - exit_price) * pos.size

        win = pnl > 0
        self._total_trades += 1
        if win:
            self._wins += 1
        self._running_pnl += pnl
        self.bankroll = self.starting_bankroll + self._running_pnl

        win_rate = self._wins / self._total_trades * 100
        cumulative_return = self._running_pnl / self.starting_bankroll * 100

        row = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "asset": pos.asset,
            "token_id": pos.token_id,
            "side": pos.side,
            "size": round(pos.size, 2),
            "entry_price": round(pos.entry_price, 4),
            "exit_price": round(exit_price, 4),
            "pnl": round(pnl, 4),
            "running_pnl": round(self._running_pnl, 4),
            "win": int(win),
            "total_trades": self._total_trades,
            "wins": self._wins,
            "win_rate_pct": round(win_rate, 1),
            "cumulative_return_pct": round(cumulative_return, 2),
        }
        self._write_row(row)
        self._print_summary(row, pos)

    def close_all(self):
        """Force-close all open positions (called on shutdown)."""
        for token_id in list(self._open):
            self.close_position(token_id)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _init_csv(self):
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=CSV_HEADERS).writeheader()

    def _write_row(self, row: dict):
        with open(self.csv_path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_HEADERS).writerow(row)

    def _print_summary(self, row: dict, pos: PaperPosition):
        result = "WIN ✓" if row["win"] else "LOSS ✗"
        sign = "+" if row["pnl"] >= 0 else ""
        logger.info(
            "\n"
            "┌─── Paper Trade Closed ─────────────────────────────┐\n"
            "│  Asset       : %s\n"
            "│  Side        : %s\n"
            "│  Size        : %.2f contracts\n"
            "│  Entry       : %.4f\n"
            "│  Exit        : %.4f\n"
            "│  P&L         : %s$%.4f  %s\n"
            "│  Running P&L : %s$%.4f\n"
            "├─── Portfolio ──────────────────────────────────────┤\n"
            "│  Trades      : %d  (W:%d / L:%d)\n"
            "│  Win rate    : %.1f%%\n"
            "│  Cum. return : %+.2f%%\n"
            "│  Bankroll    : $%.2f\n"
            "└────────────────────────────────────────────────────┘",
            pos.asset,
            pos.side,
            pos.size,
            pos.entry_price,
            row["exit_price"],
            sign, abs(row["pnl"]), result,
            "+" if row["running_pnl"] >= 0 else "", abs(row["running_pnl"]),
            row["total_trades"], row["wins"], row["total_trades"] - row["wins"],
            row["win_rate_pct"],
            row["cumulative_return_pct"],
            self.bankroll,
        )
