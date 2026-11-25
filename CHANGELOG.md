# Changelog

## 0.1.0 - 2026-11-25

- Local web dashboard (`devprune` / `dp`) grouped by framework, then project, then item, with sizes and checkboxes.
- Rules as data: Laravel, Flutter, Next.js, Node.js project rules and global caches (pnpm, npm, Composer, Dart, Xcode, Gradle, Homebrew, ...).
- Filter box, collapse/expand all, and a "safe" amount per project.
- Stacked overview bar of space per framework, with the selected share highlighted.
- Reloading the page during a scan or cleaning reconnects to it; an outcome that finished while the page was closed is shown once.
- Confirmation dialog before deleting, live per-item progress, and a result summary (cleaned, failed with reasons, not processed) instead of browser alerts; error toasts.
- Cancelable scan, cached results for instant start, quick vs. full scan.
- Cleaning with progress, stop button, dry run and official tool commands where available.
- Ctrl+C during a cleanup warns first (a second press stops after the current item, a third quits at once); SIGTERM/SIGHUP
  stop after the current item. On exit DevPrune reminds you of items it is still holding.
- Undo: cleaned folders are moved into a DevPrune holding area (`~/.cache/devprune/trash`) instead of being deleted;
  a bar shows what is held, with Review, Restore all and Empty. Tool-command items and the macOS Trash are still deleted
  for real, and "Delete permanently" skips the holding area.
- Safety checks: only paths inside `$HOME`, sensitive folders refused, symlinks refused, loopback-only server with token, CSP.
- Choose your own terminal command name from the dashboard (launcher in `~/.local/bin`).
- Optional user config: extra scan roots, excluded paths, custom project rules.
