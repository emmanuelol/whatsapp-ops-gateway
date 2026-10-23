import os
import threading

import requests
import requests.adapters

_session = None
_session_pid = None
_session_lock = threading.Lock()


def _reset_session_after_fork() -> None:
    """Reset session cache and lock in child process after fork."""
    global _session, _session_pid, _session_lock
    _session = None
    _session_pid = None
    _session_lock = threading.Lock()  # Parent lock may have been held mid-fork


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_session_after_fork)


def get_fork_safe_session() -> requests.Session:
    """
    Returns a per-process requests.Session (lazy, fork-safe, no transport retries).

    RQ/Celery worker forks share file descriptors if not isolated. This singleton
    detects PID changes and drops parent connection pools immediately upon fork.
    Transport retries are set to 0 to prevent multiplicative retry amplification
    with upstream application-level retries (e.g. Tenacity).
    """
    global _session, _session_pid
    pid = os.getpid()
    if _session is None or _session_pid != pid:
        with _session_lock:
            if _session is None or _session_pid != pid:
                session = requests.Session()
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=10,
                    pool_maxsize=10,
                    max_retries=0,
                )
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                _session, _session_pid = session, pid
    return _session
