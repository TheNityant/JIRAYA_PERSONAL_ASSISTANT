"""Collect a bounded GitHub metadata snapshot; no provider writes or tool dispatch."""

from datetime import UTC, datetime

import httpx

from app.integrations.github.errors import github_error
from app.integrations.github.validation import repository_parts


def text(value, limit=240):
    return str(value or "")[:limit]


def collect_snapshot(client, repositories: list[str]) -> dict:
    sources, coverage = [], []
    for repository in repositories:
        owner, repo = repository_parts(repository)
        for kind, limit, fetch, options in [
            ("pull_request", 6, client.list_pulls, {"limit": 6}),
            ("issue", 6, client.list_issues, {"state": "all", "limit": 20}),
            ("workflow_run", 4, client.list_workflow_runs, {"limit": 4}),
        ]:
            try:
                rows = fetch(owner=owner, repo=repo, **options)
                if kind == "issue":
                    rows = [row for row in rows if "pull_request" not in row]
                rows = rows[:limit]
                coverage.append(
                    {"repository": repository, "kind": kind, "loaded": len(rows), "limit": limit}
                )
                for row in rows:
                    number = row.get("number") if kind != "workflow_run" else row.get("id")
                    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
                        continue
                    segment = {
                        "pull_request": "pull",
                        "issue": "issues",
                        "workflow_run": "actions/runs",
                    }[kind]
                    sources.append(
                        {
                            "id": f"S{len(sources) + 1}",
                            "repository": repository,
                            "kind": kind,
                            "title": text(row.get("title") or row.get("name") or "Workflow run"),
                            "number": number,
                            "state": text(row.get("state"), 40),
                            "status": text(row.get("status"), 40),
                            "conclusion": text(row.get("conclusion"), 40),
                            "updated_at": text(row.get("updated_at"), 50),
                            "url": f"https://github.com/{repository}/{segment}/{number}",
                        }
                    )
            except (httpx.HTTPError, RuntimeError, ValueError, TypeError) as exc:
                _, detail = github_error(exc)
                coverage.append({"repository": repository, "kind": kind, "error": detail})
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "repositories": repositories,
        "scope": "Recent metadata only: at most 6 PRs, 6 issues and 4 workflow runs per repository. No code, bodies, comments, emails or calendar data.",
        "sources": sources,
        "coverage": coverage,
    }
