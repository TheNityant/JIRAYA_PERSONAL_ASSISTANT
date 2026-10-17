import re


def repository_parts(value: str) -> tuple[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("Choose a repository in owner/name form")
    owner, repo = value.split("/")
    if owner in {".", ".."} or repo in {".", ".."}:
        raise ValueError("Invalid repository")
    return owner, repo


def validate_branch(value: str) -> str:
    if (
        not value
        or value == "@"
        or value.startswith("-")
        or ".." in value
        or "@{" in value
        or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value)
        or value.endswith(".")
        or any(
            not part or part.startswith(".") or part.endswith(".lock") for part in value.split("/")
        )
    ):
        raise ValueError("Use a valid Git branch name")
    return value
