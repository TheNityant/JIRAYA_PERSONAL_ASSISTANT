"""Run ordinary local Git, using the developer's existing credentials and hooks."""

import hashlib
import os
import re
import subprocess
from pathlib import Path

from app.integrations.github.validation import validate_branch


class GitProblem(RuntimeError):
    pass


def git(root: Path, *args: str, check: bool = True) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            timeout=180,
            env=env,
            shell=False,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitProblem(
            "Git is unavailable or timed out. Check your Git installation and credentials."
        ) from exc
    if check and result.returncode:
        error = result.stderr.decode("utf-8", errors="replace").lower()
        if "non-fast-forward" in error or "fetch first" in error or "rejected" in error:
            raise GitProblem(
                "Git rejected the push. The remote may have advanced or a branch rule may block direct pushes. Review it locally."
            )
        if any(
            word in error
            for word in [
                "authentication",
                "permission denied",
                "could not read username",
                "terminal prompts disabled",
            ]
        ):
            raise GitProblem(
                "Local Git authentication failed. Sign in with Git Credential Manager or configure your SSH key, then retry."
            )
        if "tell me who you are" in error or "unable to auto-detect email" in error:
            raise GitProblem(
                "Configure Git user.name and user.email for this repository before creating a checkpoint."
            )
        raise GitProblem(
            f"Git {args[0]} failed. Check this repository's local Git status, hooks and access."
        )
    return result.stdout.decode("utf-8", errors="surrogateescape").strip("\r\n")


def repository_root(folder: str) -> Path:
    root = Path(folder).expanduser().resolve(strict=True)
    top = git(root, "rev-parse", "--show-toplevel", check=False)
    if not top:
        raise GitProblem("Git could not identify this folder as a repository. Open the repository folder itself and check local Git access.")
    return Path(top).resolve(strict=True)


def repo_id(root: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()[:24]


def github_repository(url: str) -> str:
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?",
        url,
    )
    if not match:
        raise GitProblem(
            "Use an origin remote on github.com over HTTPS or SSH, without credentials embedded in the URL."
        )
    return match[1]


def remote(root: Path) -> tuple[str, str]:
    fetch = git(root, "remote", "get-url", "--all", "origin").splitlines()
    push = git(root, "remote", "get-url", "--push", "--all", "origin").splitlines()
    if len(fetch) != 1 or len(push) != 1 or fetch != push:
        raise GitProblem(
            "Configure one matching fetch/push URL for origin before enabling checkpoints."
        )
    return fetch[0], github_repository(fetch[0])


def changed_files(root: Path) -> list[str]:
    paths = git(root, "diff", "--name-only", "-z", "HEAD", "--").split("\0")
    paths += git(root, "diff", "--name-only", "-z", "--cached", "HEAD", "--").split("\0")
    paths += git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    return sorted({path for path in paths if path})


def inspect(root: Path) -> dict:
    _, repository = remote(root)
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if not branch:
        raise GitProblem(
            "This checkout has a detached HEAD. Check out a local branch before enabling checkpoints."
        )
    if not git(root, "rev-parse", "--verify", "HEAD", check=False):
        raise GitProblem("Create the first commit in this repository before enabling checkpoints.")
    return {
        "id": repo_id(root),
        "path": str(root),
        "repository": repository,
        "branch": branch,
        "files": changed_files(root),
    }


def sensitive(path: str) -> bool:
    name = Path(path).name.lower()
    return (
        name in {".env", "credentials.json", "token.json", "id_rsa", "id_ed25519"}
        or (
            name.startswith(".env.")
            and name not in {".env.example", ".env.sample", ".env.template"}
        )
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
    )


def checkpoint(root: Path, configuration: dict, message: str) -> dict:
    before = inspect(root)
    branch = validate_branch(configuration["branch"])
    if before["branch"] != branch:
        raise GitProblem(
            f"Current branch is {before['branch']}; configured branch is {branch}. Switch branches locally, then retry."
        )
    url, repository = remote(root)
    if repository.lower() != configuration["repository"].lower() or url != configuration["remote"]:
        raise GitProblem(
            "The origin remote changed after this project was enabled. Review and enable it again."
        )
    for state in [
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "rebase-merge",
        "rebase-apply",
        "sequencer",
        "BISECT_LOG",
    ]:
        location = Path(git(root, "rev-parse", "--git-path", state))
        if not location.is_absolute():
            location = root / location
        if location.exists():
            raise GitProblem(
                "Finish the active Git merge, rebase, cherry-pick or bisect before checkpointing."
            )
    if git(root, "ls-files", "--unmerged"):
        raise GitProblem("Resolve merge conflicts before checkpointing.")
    if any(sensitive(path) for path in before["files"]):
        raise GitProblem(
            "A credential or environment file is among the changes. Handle it manually; check .gitignore before retrying."
        )
    for path in before["files"]:
        file = root / path
        if (
            file.exists()
            and not file.is_symlink()
            and file.is_file()
            and file.stat().st_size > 10 * 1024 * 1024
        ):
            raise GitProblem(
                "A changed file exceeds 10 MiB. Review it or use Git LFS before checkpointing."
            )
    head = git(root, "rev-parse", "HEAD")
    # Check the exact configured target, independent of the local upstream setting.
    remote_tip = git(root, "ls-remote", "--heads", "--", url, f"refs/heads/{branch}")
    if remote_tip:
        git(root, "fetch", "--no-tags", "--", url, f"refs/heads/{branch}")
        target = git(root, "rev-parse", "FETCH_HEAD")
        ancestors = git(root, "merge-base", head, target, check=False)
        if ancestors != target:
            raise GitProblem(
                "This branch is behind or diverged from GitHub. Pull/reconcile it locally, then retry."
            )
    else:
        target = None
    if (
        git(root, "rev-parse", "HEAD") != head
        or inspect(root)["branch"] != branch
        or remote(root)[0] != url
    ):
        raise GitProblem(
            "Repository state changed during preparation. Retry when editing/Git operations have settled."
        )
    # Reinspect after the network round trip so newly saved sensitive files cannot slip in.
    files = changed_files(root)
    if files != before["files"] or any(sensitive(path) for path in files):
        raise GitProblem(
            "The set of changed files changed during preparation. Retry once your files are saved."
        )
    committed = False
    if files:
        git(root, "add", "-A", "--", ".")
        if git(root, "diff", "--name-only", "--"):
            raise GitProblem(
                "Files changed while staging. Changes remain local; retry once editing has settled."
            )
        if git(root, "diff", "--cached", "--name-only", "HEAD", "--"):
            git(root, "commit", "-m", message)
            committed = True
    current = git(root, "rev-parse", "HEAD")
    if target == current:
        return {"status": "UP_TO_DATE", "commit": current, "files": 0}
    if git(root, "symbolic-ref", "--short", "HEAD") != branch or remote(root)[0] != url:
        raise GitProblem(
            "Branch or remote changed before pushing. The commit remains local; review it before retrying."
        )
    git(root, "push", "--porcelain", "--", url, f"{current}:refs/heads/{branch}")
    observed = git(root, "ls-remote", "--heads", "--", url, f"refs/heads/{branch}")
    if not observed or observed.split()[0] != current:
        return {
            "status": "PUSH_UNCONFIRMED",
            "commit": current,
            "files": len(files),
            "detail": "Push returned, but the remote tip differs or could not be confirmed. Check GitHub before retrying.",
        }
    return {
        "status": "PUSHED",
        "commit": current,
        "files": len(files),
        "committed": committed,
        "remaining_files": len(changed_files(root)),
        "remaining_commits": int(git(root, "rev-list", "--count", f"{current}..HEAD")),
    }
