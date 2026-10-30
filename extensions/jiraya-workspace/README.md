# Jiraya Workspace Checkpoint

Register local Git projects from every open, trusted VS Code window with the Jiraya local companion. Enable each project and choose its branch in Jiraya's GitHub workspace, then checkpoint all enabled open projects together.

The companion uses ordinary local Git. Existing Git Credential Manager/SSH authentication, ignore rules, commit hooks and GitHub branch protections still apply. Only saved files can be committed. Projects with reported unsaved documents, a branch mismatch, conflicts, changed origin or a remote branch ahead of the checkout need attention before they can be pushed.

## Setup

1. Install Python 3.12+ and Git on this computer. You do not need the Jiraya backend or a project checkout to run the packaged helper.
2. Install `jiraya-workspace-0.4.0.vsix` using Extensions → ⋯ → **Install from VSIX**, then reload your project windows.
3. In the Jiraya website, open GitHub and choose **Connect with VS Code**. Allow the browser to open VS Code. Confirm the website shown by VS Code and choose **Set up helper**. The extension uses your Python interpreter and installs helper dependencies in an isolated environment if needed. Allow local-network access in your browser, then return to the original tab. No token copying is required.
4. Open your local projects in trusted VS Code windows. Run **Jiraya: Add Git Projects** to select multiple existing repository folders. Adding folders does not enable a policy or push changes.
5. Enable each project and choose its branch in Jiraya. Pairing is remembered for this Jiraya account and browser origin, even after sign-out. Project policies and the device token persist in your local app data; the IDE token uses VS Code SecretStorage. Subsequent VS Code startups restart the helper automatically.

Upgrading from a manually started older helper: wait for active checkpoints to finish, stop it with Ctrl+C in its original terminal, and install 0.4.0. Pair once using the new website flow. A new browser profile, frontend domain, computer, or cleared browser storage requires pairing again. Remote SSH/WSL/container workspaces are outside this desktop release.

**Jiraya: Set Up Local Companion** can reselect Python. **Jiraya: Stop Local Companion** stops only a helper started by that window and refuses while a checkpoint is running. Closing VS Code does not forcibly interrupt the helper. Restarting the computer stops it until VS Code starts again.

Manual token pairing remains available under the website's **Manual pairing / custom port** details and **Jiraya: Pair Local Companion**.
Use **Jiraya: Checkpoint Enabled Open Projects** in the Command Palette or the button in Jiraya. This command saves files in the current window first; save other windows yourself before running. Registration expires 45 seconds after a window stops reporting.

## Package from source

Run `python extensions/jiraya-workspace/build_vsix.py` from the repository root. The build includes only the minimal local companion sources and extension files; no cloud configuration or credentials. No Marketplace account is required to package or locally install the VSIX.

See the complete setup in `docs/WORKSPACE_CHECKPOINT.md` in the Jiraya repository.

## Diagnose a missing project

Click the Jiraya status bar item or run **Jiraya: Show Workspace Diagnostics**. Output → Jiraya Workspace shows connection errors, rejected folders and project branches without printing tokens. A window with no local folder or no Git repository is reported as such; successful device pairing alone is not reported as successful project registration. Invalid replacement tokens preserve the working token.

Update and restart the Python companion alongside version 0.4.0 to receive detailed folder results. In the browser, **Registration details** expands automatically when no projects are found. A parent workspace is recognized as a container when its nested repositories are explicitly reported by the editor. Undetected repositories are not found through an arbitrary recursive disk scan: select them with **Jiraya: Add Git Projects**. Unsaved documents inside nested repositories still block their checkpoints. Git ownership failures are reported distinctly and no trust settings are changed automatically.
