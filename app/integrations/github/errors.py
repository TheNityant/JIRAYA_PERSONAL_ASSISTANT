"""Actionable GitHub diagnostics without exposing tokens or provider payloads."""

import httpx


def github_error(exc: Exception, *, operation: str = "read") -> tuple[int, str]:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code
        if status == 401:
            # This is provider authorization, not an expired Jiraya login session.
            return 409, "GitHub authorization expired or was revoked. Reconnect GitHub."
        if status == 403:
            if response.headers.get("x-ratelimit-remaining") == "0":
                return 429, "GitHub's rate limit was reached. Wait before retrying."
            return 403, "GitHub denied this operation. Check the connected account's repository permissions and organization access."
        if status == 429:
            return 429, "GitHub is limiting requests. Wait before retrying."
        if status == 404:
            if operation == "source_branch":
                return 404, "Source branch not found or not accessible. Select an existing branch and check repository access."
            return 404, "Repository not found or not accessible to the connected GitHub account. Check the repository and account access."
        if status == 422:
            if operation == "create_branch":
                try:
                    payload = response.json()
                    already_exists = "already exists" in str(payload.get("message", "")).lower() or "already_exists" in str(payload.get("errors", "")).lower()
                except (ValueError, AttributeError):
                    already_exists = False
                if already_exists:
                    return 409, "That branch already exists. Choose a different new branch name."
                return 422, "GitHub rejected the branch. Check its name, existing branches and repository branch rules."
            return 422, "GitHub rejected the request. Check the repository and operation settings."
        if status == 409:
            return 409, "GitHub reported a repository conflict. Check that the repository has an initial commit and that the requested branch can be created."
        return 502, "GitHub could not complete the request. Try again later."
    if isinstance(exc, httpx.RequestError):
        return 503, "Could not reach GitHub. Check connectivity and try again."
    if isinstance(exc, ValueError):
        return 422, "Choose a repository in owner/name form and valid branch names."
    if isinstance(exc, RuntimeError):
        # Do not forward arbitrary exception text, even for provider/credential errors.
        return 409, "GitHub connection is unavailable. Reconnect GitHub and try again."
    return 503, "GitHub operation failed. Check the connection and try again."
