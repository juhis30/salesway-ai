"""Runs queued jobs (app/services/jobs.py).

On the free hosting plan the worker is a thread inside the API process (RUN_WORKER=true, the
default). With more traffic, run it as its own process instead and set RUN_WORKER=false on the API:

    python -m app.worker

Several workers can run at once; each job is still run by only one of them.
"""

import logging
import threading
import time

from app.core.database import system_session
from app.services import jobs

log = logging.getLogger(__name__)

POLL_SECONDS = 5.0
MAINTENANCE_EVERY = 15 * 60  # seconds


class Worker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_maintenance = 0.0

    def run_once(self) -> int:
        """Run every job that is due, and housekeeping when it's time. Returns jobs run."""
        db = system_session()
        try:
            if time.monotonic() - self._last_maintenance >= MAINTENANCE_EVERY:
                self._last_maintenance = time.monotonic()
                counts = jobs.maintenance(db)
                if any(counts.values()):
                    log.info("maintenance", extra={"fields": counts})
            return jobs.run_pending(db)
        finally:
            db.close()

    def loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - e.g. the database is briefly unreachable; try again
                log.exception("worker loop failed; retrying")
                self._stop.wait(POLL_SECONDS)
            jobs.wait_for_work(POLL_SECONDS)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.loop, name="job-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        jobs.wake()  # interrupt the loop's wait so it sees the stop at once
        if self._thread:
            self._thread.join(timeout)


if __name__ == "__main__":
    from app.core.observability import configure_logging

    configure_logging()
    log.info("worker started")
    Worker().loop()
