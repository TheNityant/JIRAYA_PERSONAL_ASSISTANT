# Jiraya Workspace Checkpoint

Register local Git projects from every open, trusted VS Code window with the Jiraya local companion. Enable each project and choose its branch in Jiraya's GitHub workspace, then checkpoint all enabled open projects together.

The companion uses ordinary local Git. Existing Git Credential Manager/SSH authentication, ignore rules, commit hooks and GitHub branch protections still apply. Only saved files can be committed. Projects with reported unsaved documents, a branch mismatch, conflicts, changed origin or a remote branch ahead of the checkout need attention before they can be pushed.

## Setup

1. Install Jiraya and run `python -m app.local_companion serve` on the computer containing your projects. Keep it running while using checkpoints.
2. Install the packaged extension: `code --install-extension jiraya-workspace-0.1.0.vsix`.
3. In VS Code's Command Palette, run **Jiraya: Pair Local Companion**. Paste the token printed by the companion. The token is stored using VS Code SecretStorage.
4. Reload other project windows. Each window registers its open local workspace folders automatically. Remote SSH/WSL/container workspaces require a companion in that environment and are not covered by the desktop setup.
5. In the Jiraya GitHub page, pair the same computer, then enable the projects you want to checkpoint.

Use **Jiraya: Checkpoint Enabled Open Projects** in the Command Palette or the button in Jiraya. This command saves files in the current window first; save other windows yourself before running. Registration expires 45 seconds after a window stops reporting.

## Package from source

Run `npx --yes @vscode/vsce package --allow-missing-repository` in this extension directory. No Marketplace account is required to package or locally install the VSIX.

See the complete setup in `docs/WORKSPACE_CHECKPOINT.md` in the Jiraya repository.
