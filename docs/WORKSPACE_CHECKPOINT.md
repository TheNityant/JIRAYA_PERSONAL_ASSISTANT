# Local workspace checkpoints

Jiraya can checkpoint several enabled, open Git projects from one frontend button or one VS Code command. This is a device-local automation, separate from the cloud GitHub issue/PR/workflow watchers.

A normal hosted frontend cannot execute Git in arbitrary desktop folders. A small companion process runs on the computer holding the repositories. VS Code windows register their workspace folders with it, and Jiraya's frontend talks directly to that loopback service. File contents and Git credentials are not sent to Jiraya's cloud API. Git itself pushes to the configured GitHub origin using the developer's existing Git Credential Manager/SSH setup.

## Install once on your development computer

From the Jiraya checkout, with Python 3.12+ and Git installed:

```powershell
python -m pip install -e .
python -m app.local_companion serve
```

Keep this terminal running. It prints a pairing token and starts on `127.0.0.1:8765`. It does not change your `.env` or start the cloud backend. The usual frontend/backend can run alongside it.

Install the included VS Code extension from the repository root:

```powershell
code --install-extension extensions/jiraya-workspace/jiraya-workspace-0.3.0.vsix --force
```

Run **Jiraya: Pair Local Companion** in VS Code's Command Palette and paste the companion token. Open/reload your other project windows. Each trusted local window registers independently; multi-root workspaces are supported. Version 0.3.0 also reports nested repositories already detected by VS Code's Git integration. Run **Jiraya: Add Git Projects** to choose multiple existing repository folders with a native folder picker and add them to the workspace. The extension uses VS Code SecretStorage for the token. Remote SSH/WSL/container environments need a companion in that environment; this release targets local desktop folders.

In Jiraya's **GitHub → Local workspace checkpoint** panel, enter the same token and connect. The token is remembered in that browser profile, on this frontend origin. Select the branch for each project and click **Enable checkpoint**. The local checkout must already be on that branch. Disable the policy before changing its target branch.

The companion remembers enabled projects in the current user's local app data. Windows uses `%LOCALAPPDATA%/Jiraya/companion`; other systems use `~/.local/share/Jiraya/companion`. `--config-dir` can select another location. Pair only the frontend you use. To revoke an old pairing, stop the companion, remove `companion.json` from that directory and restart; this also resets local policies/history.

## Daily use

1. Open your projects in paired VS Code windows and save the files you want recorded.
2. In Jiraya, review the open-project list, selected branches and changed filenames.
3. Click **Checkpoint all enabled open projects**. Alternatively, run **Jiraya: Checkpoint Enabled Open Projects** in VS Code. The VS Code command saves the current window first; save files in other windows before running it.
4. Review the per-project results: **PUSHED**, **UP TO DATE**, **SKIPPED** or **NEEDS ATTENTION**. A commit link points to the recorded GitHub commit. One project's failure does not stop the remaining projects.

This release is one-click execution. It does not run automatic timers or shutdown hooks. Keep the companion running until the job completes; shutting down or closing it can interrupt network operations. Opening an IDE by itself does not enable a project or push anything.

## What happens for each project

- Check that an enabled repository is still registered by an open IDE. Registration expires after 45 seconds without a heartbeat; normal reports arrive every 15 seconds. The extension also reports edits/saves and window closure.
- Check the current branch and the exact origin URL against the saved policy. HTTPS and SSH remotes on `github.com` are supported. Fetch/push origin URLs must match and must not embed credentials.
- Respect `.gitignore`, then inspect saved tracked/untracked changes. Modified, added and deleted files are included. Paths matching common credential/environment filenames need manual handling; this is a filename safeguard, not a complete secret scanner. Files above 10 MiB require manual review/Git LFS.
- Check the configured remote branch. A checkout that is behind/diverged, in a merge/rebase/cherry-pick/bisect or has conflicts needs local attention.
- Stage saved changes, create a normal Git commit with the chosen message, then push to that exact branch. Existing local commits are also pushed, even when no new file changes exist. An empty checkpoint creates no extra commit. Existing commit/push hooks remain active.
- Check the remote branch tip after pushing. New edits made during execution remain local and are reported when observable.

The companion never force-pushes, changes your checkout, resolves conflicts or pulls/merges for you. If a push fails after committing, the checkpoint commit remains local. Fix credentials, hooks, branch protection or divergence locally and retry. The retry pushes the existing commit without creating a duplicate empty commit. Authentication must already work in local Git; the cloud GitHub OAuth connection does not install local Git credentials.

Only saved disk contents can be committed. Reported unsaved documents cause that project to be skipped; the last IDE heartbeat is not an instantaneous snapshot of every editor. Save all windows before checkpointing. Nested repositories must either be detected by the editor's Git integration or be opened explicitly using **Jiraya: Add Git Projects**; the companion does not recursively scan your disk. Each registered repository needs at least one initial commit.

## Registration diagnostics

After updating, restart the companion, install `jiraya-workspace-0.3.0.vsix`, and run **Developer: Reload Window** in your VS Code project windows. The browser and extension use the same companion token; no `.env` edit is needed.

The extension status bar reports registered Git project counts or an actionable connection error. Click it or run **Jiraya: Show Workspace Diagnostics** to open **Output → Jiraya Workspace** and refresh registration. Diagnostics show the configured port, workspace trust, local folders, registration failures and project branches; they do not print pairing tokens or file contents. A failed replacement token is not saved over working credentials. Token changes automatically refresh other active windows using the same VS Code secret storage.

The browser shows how many editor/terminal sessions are reporting, plus expandable registration details:

- **No sessions:** check the extension is installed and enabled in the intended desktop VS Code profile, open the repository folder, trust it if appropriate, and check the configured companion port.
- **Sessions but no repositories:** pairing has succeeded, but Git has not confirmed a repository. Run **Jiraya: Add Git Projects** and choose the existing checkout folders. Nested repositories already detected in VS Code's Source Control are reported automatically. Ownership failures are shown distinctly from non-repository folders; review Git trust locally, without disabling ownership checks globally.
- **Repository shown with an error:** check its Git installation/access, initial commit and supported origin. Registration alone does not make it ready to push.
- **Projects disappear:** keep the editor or `watch` terminal open. Its registration expires after 45 seconds without a heartbeat.

An older companion still works with the extension, but must be updated and restarted to return detailed registration counts. Remote SSH/WSL/container folders remain outside this desktop release.

## Other local IDEs

Run a persistent registration command in that IDE's terminal:

```powershell
python -m app.local_companion watch "D:/projects/Jiraya" "D:/projects/LLMNotebook"
```

While this command remains running, those folders count as open. It cannot observe unsaved buffers in another editor, so save them first. Ctrl+C removes the registration. This is explicit registration, not automatic detection of every installed IDE.

## Hosted frontend

Start the companion with the exact deployed frontend origin:

```powershell
python -m app.local_companion serve --origin https://your-jiraya.example
```

Repeat `--origin` to allow additional origins. Permit the browser's local-network/loopback access prompt when shown. Browser policies can restrict HTTPS-to-loopback requests; if blocked, use the VS Code command to execute checkpoints and the local frontend to review results. The cloud server never runs Git against a desktop filesystem. A local process and native Git authentication are required even after Jiraya is deployed.

## Packaging and verification

The included VSIX is built from `extensions/jiraya-workspace` with `npx --yes @vscode/vsce package --allow-missing-repository`. No Marketplace publication is required for local installation.

`tests/test_workspace_checkpoint.py` exercises real temporary local repositories and bare remotes, including saved changes, unpushed commits, branch targeting, failed pushes/retry, diverged remotes, multiple projects, credential filename exclusions, open-window registration and loopback API pairing. The pairing regression test uses a real isolated companion and Chromium, including password-manager changes that bypass React state. Run it from `web` with `node tests/workspace-pairing.cjs` after installing Playwright and its Chromium browser (`npm install --no-save playwright`, then `npx playwright install chromium`). It requires the project Python dependencies. It temporarily creates a test route, uses ports 3117 and 8876, and removes its route and test configuration when finished; it never performs a checkpoint or push. Other browser checks use controlled companion responses. These tests do not establish that a particular Windows Git credential setup or browser policy is configured correctly.

## Companion port already in use

Only one companion may listen on `127.0.0.1:8765`. Re-running the command does not replace the existing process. The launcher now reserves its socket before creating state or printing a token, then passes the reserved socket to Uvicorn. An occupied/unavailable port exits with a clear diagnostic without printing a new pairing token or terminating another process.

Keep using the existing companion, or wait for any active checkpoint to finish and stop it with Ctrl+C in its original terminal before restarting updated code. To identify the listener in PowerShell:

```powershell
$listenerPid = Get-NetTCPConnection -LocalPort 8765 -State Listen |
    Select-Object -First 1 -ExpandProperty OwningProcess
Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" |
    Select-Object ProcessId, Name, CommandLine
```

If its command line confirms it is the Jiraya companion and its original terminal is unavailable, `Stop-Process -Id $listenerPid` stops that identified process. Do not stop an unidentified process. If another application needs this port, use `python -m app.local_companion serve --port 8766` and set the same port in the frontend, VS Code's `jiraya.companionPort` setting and any `watch --port 8766` command. The main backend uses port 8000; the frontend normally uses 3000.
