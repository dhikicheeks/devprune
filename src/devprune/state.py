"""Process-wide state shared by the scanner, cleaner and HTTP server."""
import threading

LOCK = threading.Lock()
STATE = {"items": {}}   # id -> item from the latest scan (or cache); only these can be deleted
CACHE_TS = [None]

JOB = {"state": "idle", "cancel": threading.Event(), "procs": set(), "phase": "", "done": 0, "total": 0,
       "items": [], "error": ""}
CJOB = {"state": "idle", "total": 0, "done": 0, "current": "", "results": [], "freed_kb": 0,
        "stop": threading.Event(), "dry": False, "error": "",
        # plan/started/finished/acked let a reloaded page re-attach to the job or see its outcome once
        "plan": [], "started": 0, "finished": 0, "acked": True}
