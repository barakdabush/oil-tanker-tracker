"""Minimal GitHub Issues REST client used by the incident detector.

Uses a fine-grained PAT that should be scoped to this repository with
``Issues: read & write`` only, so the server can never push code.
"""

from datetime import datetime
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubIssuesClient:
    def __init__(self, token: str, repo: str, timeout: float = 20.0):
        self.repo = repo
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "oil-tanker-tracker-incident-bot",
        }
        self._timeout = timeout

    async def create_issue(self, title: str, body: str, labels: list[str]) -> Optional[int]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            resp = await client.post(
                f"{GITHUB_API}/repos/{self.repo}/issues",
                json={"title": title[:250], "body": body, "labels": labels},
            )
            if resp.status_code >= 300:
                logger.warning(f"GitHub create_issue failed: HTTP {resp.status_code}")
                return None
            return int(resp.json()["number"])

    async def get_issue_state(self, number: int) -> Optional[str]:
        status = await self.get_issue_status(number)
        return status[0] if status else None

    async def get_issue_status(self, number: int) -> Optional[tuple[str, Optional[datetime]]]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            resp = await client.get(f"{GITHUB_API}/repos/{self.repo}/issues/{number}")
            if resp.status_code >= 300:
                return None
            data = resp.json()
            closed_at_raw = data.get("closed_at")
            closed_at = (
                datetime.fromisoformat(closed_at_raw.replace("Z", "+00:00"))
                if closed_at_raw else None
            )
            return data.get("state"), closed_at

    async def add_comment(self, number: int, body: str) -> bool:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            resp = await client.post(
                f"{GITHUB_API}/repos/{self.repo}/issues/{number}/comments",
                json={"body": body},
            )
            if resp.status_code >= 300:
                logger.warning(f"GitHub add_comment failed: HTTP {resp.status_code}")
                return False
            return True
