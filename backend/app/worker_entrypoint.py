"""Existing verdict workers plus one independent bounded presentation lane."""
import os
import signal
import socket
from threading import Event, Thread

from .config import get_settings
from .database import build_engine, build_session_factory
from .services.crypto import CryptoService
from .services.worker_concurrency import serve as serve_analyses
from .services.evidence_display_jobs import serve as serve_display


def main():
    settings = get_settings()
    sessions = build_session_factory(build_engine(settings.database_url))
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stop = Event()
    previous = {value: signal.signal(value, lambda *_: stop.set()) for value in (signal.SIGINT, signal.SIGTERM)}
    lane = None
    if settings.worker_role in {"analysis", "both"}:
        lane = Thread(target=serve_display, args=(sessions, crypto, settings, worker_id, stop), name="waf-evidence-display")
        lane.start()
    try:
        serve_analyses(sessions, crypto, settings, worker_id, stop=stop, install_signals=False)
    finally:
        stop.set()
        if lane:
            lane.join()
        for value, handler in previous.items():
            signal.signal(value, handler)


if __name__ == "__main__":
    main()
