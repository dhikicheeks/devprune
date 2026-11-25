# Security policy

DevPrune **deletes files permanently** and runs a local web server, so safety bugs matter.

## Reporting a vulnerability

Please do **not** open a public issue. Report privately using GitHub's "Report a vulnerability" button
(Security tab) on the repository. Include steps to reproduce and the version (`devprune --version`).

Examples of in-scope problems:

- A path outside `$HOME`, a sensitive folder (`.ssh`, `Library/Keychains`, ...) or a symlink target that can be deleted.
- A way to make the server run a command that is not in the built-in whitelist.
- A web page on another origin being able to talk to the local server (CSRF / DNS rebinding).

## Design notes

- The server binds to `127.0.0.1` only, requires a per-session token, and checks `Host` and `Origin`.
- Only items found by the latest scan (or a validated cache) can be deleted.
- Commands come from the built-in rules only. User config rules cannot define commands.
