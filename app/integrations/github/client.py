from typing import Any

import httpx


class GitHubClient:
    def __init__(self, *, access_token: str, api_base_url: str = "https://api.github.com"):
        if not access_token:
            raise RuntimeError("GitHub access token is missing from the stored connection.")
        self.api_base_url = api_base_url.rstrip("/")
        self.client = httpx.Client(
            base_url=self.api_base_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Jiraya-Personal-Assistant",
            },
            timeout=30.0,
        )

    def _request(self, method: str, path: str, **kwargs) -> Any:
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get_user(self) -> dict[str, Any]:
        return self._request("GET", "/user")

    def list_repositories(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return list(
            self._request(
                "GET",
                "/user/repos",
                params={
                    "per_page": max(1, min(limit, 100)),
                    "sort": "updated",
                    "direction": "desc",
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            or []
        )

    def list_workflow_runs(
        self,
        *,
        owner: str,
        repo: str,
        limit: int = 20,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": max(1, min(limit, 100))}
        if status:
            params["status"] = status
        payload = self._request(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs",
            params=params,
        )
        return list((payload or {}).get("workflow_runs", []) or [])

    def list_issues(
        self,
        *,
        owner: str,
        repo: str,
        limit: int = 30,
        state: str = "open",
    ) -> list[dict[str, Any]]:
        return list(
            self._request(
                "GET",
                f"/repos/{owner}/{repo}/issues",
                params={"per_page": max(1, min(limit, 100)), "state": state},
            )
            or []
        )

    def create_issue(self, *, owner: str, repo: str, title: str, body: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            json={"title": title, "body": body},
        )

    def create_branch(
        self,
        *,
        owner: str,
        repo: str,
        new_branch: str,
        from_ref: str,
    ) -> dict[str, Any]:
        source = self._request(
            "GET",
            f"/repos/{owner}/{repo}/git/ref/heads/{from_ref}",
        )
        sha = str(((source or {}).get("object") or {}).get("sha") or "")
        if not sha:
            raise RuntimeError(f"Could not resolve GitHub source branch {from_ref!r}.")
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{new_branch}", "sha": sha},
        )

    def dispatch_workflow(
        self,
        *,
        owner: str,
        repo: str,
        workflow_id: str,
        ref: str,
        inputs: dict[str, str] | None = None,
    ) -> None:
        self._request(
            "POST",
            f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
            json={"ref": ref, "inputs": inputs or {}},
        )
