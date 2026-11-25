"""Command-line entry point: start the local server and open the dashboard."""
import argparse
import threading
import webbrowser

from . import __version__, cache, config, server, trash


def main(argv=None):
    ap = argparse.ArgumentParser(prog="devprune", description="Clean caches and build artifacts "
                                 "(pnpm/Next.js, Laravel, Flutter) from a local web dashboard.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = ap.parse_args(argv)

    config.load()
    cache.load_cache()
    url = f"http://127.0.0.1:{a.port}/"
    try:
        srv = server.make_server(a.port)
    except OSError:
        print(f"Already running at {url}, opening the browser...", flush=True)
        if not a.no_browser:
            webbrowser.open(url)
        return 0
    try:
        trash.reconcile()   # only the process that owns the port may repair journals left by a crash
    except Exception as e:  # noqa: BLE001 - a damaged staging folder must never stop the dashboard
        print(f"Could not check the DevPrune staging folder: {e}", flush=True)
    print(f"DevPrune is running at {url}  (Ctrl+C to stop)", flush=True)
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
