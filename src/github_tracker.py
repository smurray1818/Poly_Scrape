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

    The target issue is resolved by label: on first use it searches for an open
    issue tagged with GITHUB_LABEL and creates one if none exists.

    Required env vars (or constructor args):
        GITHUB_TOKEN     - personal access token with repo:issues scope
        GITHUB_REPO      - owner/repo  (e.g. "smurray1818/Poly_Up_Down")
        GITHUB_LABEL     - label name used to find/create the tracking issue
    """

    def __init__(
        self,
        token: str = None,
        repo: str = None,
        label: str = None,
        interval_seconds: float = 1800,  # 30 minutes
        risk_manager=None,
        executor=None,
    ):
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self.repo = repo or os.getenv("GITHUB_REPO", "")
        self.label = label or os.getenv("GITHUB_LABEL", "bot-stats")
        self.interval = interval_seconds
        self.risk_manager = risk_manager
        self.executor = executor
        self._issue: Optional[int] = None  # resolved lazily
        self._running = False
        self._post_count = 0

    async def _resolve_issue(self) -> Optional[int]:
        """Find the open issue with self.label, or create it if none exists."""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            # Search for existing open issue with the label
            resp = await client.get(
                f"{GITHUB_API}/repos/{self.repo}/issues",
                params={"labels": self.label, "state": "open", "per_page": 1},
                headers=headers,
            )
            resp.raise_for_status()
            issues = resp.json()

            if issues:
                number = issues[0]["number"]
                logger.info("GitHubTracker: found existing issue #%d (label=%s)", number, self.label)
                return number

            # Ensure the label exists first (create if missing)
            await self._ensure_label(client, headers)

            # Create a new tracking issue
            resp = await client.post(
                f"{GITHUB_API}/repos/{self.repo}/issues",
                json={
                    "title": "Bot Stats — latency & performance tracking",
                    "body": "Auto-created by the Polymarket arbitrage bot. Stats are appended as comments every 30 minutes.",
                    "labels": [self.label],
                },
                headers=headers,
            )
            resp.raise_for_status()
            number = resp.json()["number"]
            logger.info("GitHubTracker: created new issue #%d (label=%s)", number, self.label)
            return number

    async def _ensure_label(self, client: httpx.AsyncClient, headers: dict):
        """Create the label if it doesn't exist in the repo."""
        resp = await client.get(
            f"{GITHUB_API}/repos/{self.repo}/labels/{self.label}",
            headers=headers,
        )
        if resp.status_code == 404:
            await client.post(
                f"{GITHUB_API}/repos/{self.repo}/labels",
                json={"name": self.label, "color": "0075ca"},
                headers=headers,
            )

    async def start(self):
        if not self.token or not self.repo:
            logger.warning("GitHubTracker: missing config — tracker disabled")
            return
        self._running = True
        try:
            self._issue = await self._resolve_issue()
        except Exception as e:
            logger.error("GitHubTracker: could not resolve issue — %s", e)
            return
        logger.info("GitHubTracker: posting to %s#%d every %.0fs", self.repo, self._issue, self.interval)
        while self._running:
            await asyncio.sleep(self.interval)
            if self._running:
                await self._post_stats()

    async def stop(self):
        self._running = False

    async def post_now(self):
        """Force an immediate post (useful for startup / shutdown summaries)."""
        if self._issue is None:
            try:
                self._issue = await self._resolve_issue()
            except Exception as e:
                logger.error("GitHubTracker: could not resolve issue — %s", e)
                return
        await self._post_stats()

    async def _post_stats(self):
        if not self._issue:
            return
        # Snapshot latency metrics to CSV for the dashboard sync script
        import os
        from pathlib import Path
        csv_path = Path(os.getenv("LATENCY_CSV_PATH",
                        Path(__file__).parent.parent / "logs" / "latency_snapshot.csv"))
        try:
            latency_tracker.append_csv_snapshot(csv_path)
        except Exception as e:
            logger.warning("Failed to write latency snapshot: %s", e)

        body = self._build_body()
        url = f"{GITHUB_API}/repos/{self.repo}/issues/{self._issue}/comments"
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
            logger.info("GitHubTracker: posted stats to #%d (post #%d)", self._issue, self._post_count)
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
