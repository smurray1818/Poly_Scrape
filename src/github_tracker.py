"""
Posts rolling performance and latency stats to a GitHub Issue every 30 minutes.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from .latency import tracker as latency_tracker

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubTracker:
    """
    Appends a timestamped stats comment to a GitHub Issue on a fixed interval.

    Required env vars (or constructor args):
        GITHUB_TOKEN     - personal access token with repo scope
        GITHUB_REPO      - owner/repo  (e.g. "alice/polymarket-bot")
        GITHUB_ISSUE     - issue number (integer)
    """

    def __init__(
        self,
        token: str = None,
        repo: str = None,
        issue: int = None,
        interval_seconds: float = 1800,  # 30 minutes
        risk_manager=None,
        executor=None,
    ):
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self.repo = repo or os.getenv("GITHUB_REPO", "")
        self.issue = issue or int(os.getenv("GITHUB_ISSUE", "0"))
        self.interval = interval_seconds
        self.risk_manager = risk_manager
        self.executor = executor
        self._running = False
        self._post_count = 0

    async def start(self):
        if not self.token or not self.repo or not self.issue:
            logger.warning("GitHubTracker: missing config — tracker disabled")
            return
        self._running = True
        logger.info("GitHubTracker: posting to %s#%d every %.0fs", self.repo, self.issue, self.interval)
        while self._running:
            await asyncio.sleep(self.interval)
            if self._running:
                await self._post_stats()

    async def stop(self):
        self._running = False

    async def post_now(self):
        """Force an immediate post (useful for startup / shutdown summaries)."""
        await self._post_stats()

    async def _post_stats(self):
        body = self._build_body()
        url = f"{GITHUB_API}/repos/{self.repo}/issues/{self.issue}/comments"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json={"body": body}, headers=headers)
                resp.raise_for_status()
            self._post_count += 1
            logger.info("GitHubTracker: posted stats (#%d)", self._post_count)
        except Exception as e:
            logger.error("GitHubTracker: failed to post — %s", e)

    def _build_body(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [f"## Bot Stats — {now}", ""]

        # Risk / portfolio summary
        if self.risk_manager:
            s = self.risk_manager.status()
            lines += [
                "### Portfolio",
                f"- Bankroll: **${s['bankroll']:,.2f}**",
                f"- Peak: ${s['peak_bankroll']:,.2f}",
                f"- Drawdown: {s['drawdown_pct']:.2f}%",
                f"- Open positions: {s['open_positions']}",
                f"- Halted: {'⚠️ YES — ' + s['halt_reason'] if s['halted'] else 'No'}",
                "",
            ]

        # Fill stats
        if self.executor:
            fills = self.executor.fill_count()
            recent = self.executor.recent_fills(5)
            lines += [
                "### Execution",
                f"- Total fills: {fills}",
                "",
                "**Recent fills:**",
            ]
            if recent:
                lines.append("| Time | Side | Size | Price | Latency |")
                lines.append("|------|------|------|-------|---------|")
                for f in reversed(recent):
                    ts = datetime.fromtimestamp(f.elapsed_ms / 1000, tz=timezone.utc).strftime("%H:%M:%S") if f.elapsed_ms else "—"
                    lines.append(
                        f"| — | {f.side} | {f.size} | {f.price} | {f.elapsed_ms:.1f}ms |"
                    )
            lines.append("")

        # Latency table
        lines += ["### Latency", latency_tracker.summary_table(), ""]

        return "\n".join(lines)
