# Changelog

## 0.1.0 (unreleased)

- Local web dashboard (`devprune` / `dp`) grouped by framework, then project, then item, with sizes and checkboxes.
- Rules as data: Laravel, Flutter, Next.js, Node.js project rules and global caches (pnpm, npm, Composer, Dart, Xcode, Gradle, Homebrew, ...).
- Filter box, collapse/expand all, and a "safe" amount per project.
- Stacked overview bar of space per framework, with the selected share highlighted.
- Reloading the page during a scan or cleaning reconnects to it; an outcome that finished while the page was closed is shown once.
- Confirmation dialog before deleting, live per-item progress, and a result summary (cleaned, failed with reasons, not processed) instead of browser alerts; error toasts.
- Cancelable scan, cached results for instant start, quick vs. full scan.
- Cleaning with progress, stop button, dry run and official tool commands where available.
- Safety checks: only paths inside `$HOME`, sensitive folders refused, symlinks refused, loopback-only server with token, CSP.
- Choose your own terminal command name from the dashboard (launcher in `~/.local/bin`).
- Optional user config: extra scan roots, excluded paths, custom project rules.
