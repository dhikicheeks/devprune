"""Ctrl+C / SIGTERM / SIGHUP handling (D-19): a running cleanup is never cut off without a warning.

- Ctrl+C with a cleanup running: warn in the terminal; a second Ctrl+C within WINDOW seconds stops the
  cleanup after the current item and quits; a third quits immediately. Without a cleanup: quit at once.
- SIGTERM/SIGHUP (terminal tab closed, `kill`): nobody can be asked, so stop after the current item
  (at most GRACE_TERM seconds) and quit.
Items are moved one at a time with a journal (trash.py), so even a hard stop leaves a consistent state.
"""
import signal
import threading
import time

from . import state

WINDOW = 5.0        # a second Ctrl+C within this many seconds means "stop"
GRACE_INT = 10.0    # after a confirmed Ctrl+C: how long to wait for the current item
GRACE_TERM = 5.0    # after SIGTERM/SIGHUP: the same, with nobody to ask

KINDS = {"clean": "cleanup", "empty": "emptying", "restore": "restore"}


def protected():
    """A job whose interruption matters: a real clean/empty/restore, not a simulation."""
    j = state.CJOB
    return j["state"] == "running" and not j.get("dry")


class Guard:
    def __init__(self, srv, say=print, clock=time.monotonic):
        self.srv, self.say, self.clock = srv, say, clock
        self.exit_code = 0
        self._first = None
        self._stopping = False
        self._old = {}

    # ---- terminal text
    def _talk(self, text):
        try:
            self.say(text, flush=True)
        except TypeError:
            self.say(text)
        except (OSError, ValueError):
            pass    # the terminal is gone (SIGHUP): nothing to tell

    def describe(self):
        j = state.CJOB
        kind = j.get("kind", "clean")
        n = min(j["done"] + 1, j["total"])
        cur = f' ({j["current"]})' if j.get("current") else ""
        head = f"A {KINDS.get(kind, 'cleanup')} is running: item {n} of {j['total']}{cur}."
        if kind == "clean" and not j.get("permanent", True):
            tail = "Items already moved aside stay in DevPrune and can be restored next time."
        elif kind == "empty":
            tail = "Items already emptied are gone; the rest stay in DevPrune."
        elif kind == "restore":
            tail = "Items already restored are back in place; the rest stay in DevPrune."
        else:
            tail = "Items already deleted cannot be restored."
        return head + " " + tail

    # ---- handlers (run in the main thread, between two bytecodes of serve_forever)
    def on_int(self, signum, frame):
        if not protected():
            raise KeyboardInterrupt
        if self._stopping:
            self._talk("Quitting now.")
            raise KeyboardInterrupt
        now = self.clock()
        if self._first is not None and now - self._first <= WINDOW:
            self._talk("Stopping after the current item, then quitting. Press Ctrl+C again to quit right now.")
            self._begin_stop(GRACE_INT)
            return
        self._first = now
        self._talk(self.describe() + f"\nPress Ctrl+C again within {int(WINDOW)} s to stop after the current "
                   "item and quit, or wait for it to finish.")

    def on_term(self, signum, frame):
        self.exit_code = 128 + signum
        if not protected():
            raise KeyboardInterrupt
        self._talk(f"Received signal {signum}: stopping after the current item, then quitting.")
        self._begin_stop(GRACE_TERM)

    # ---- stopping without deadlocking serve_forever
    def _begin_stop(self, grace):
        if self._stopping:
            return
        self._stopping = True
        state.CJOB["stop"].set()
        threading.Thread(target=self._wait_then_shutdown, args=(grace,), daemon=True).start()

    def _wait_then_shutdown(self, grace):
        end = time.monotonic() + grace
        while state.CJOB["state"] == "running" and time.monotonic() < end:
            time.sleep(0.05)
        self.srv.shutdown()     # safe here: this is not the thread that runs serve_forever

    # ---- install / restore
    def install(self):
        for name, handler in (("SIGINT", self.on_int), ("SIGTERM", self.on_term), ("SIGHUP", self.on_term)):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                self._old[sig] = signal.signal(sig, handler)
            except ValueError:      # not the main thread: leave the defaults
                pass

    def restore(self):
        for sig, old in self._old.items():
            try:
                signal.signal(sig, old)
            except (ValueError, TypeError):
                pass
        self._old = {}
