"""Run local acquisition monitoring without occupying the inference worker."""
from __future__ import annotations

import argparse
import time

from dotenv import load_dotenv
from filelock import FileLock, Timeout

from pipeline.config import load_config
from .engine import AcquisitionRunner
from .service import AcquisitionService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Reconcile the queue once and exit")
    parser.add_argument("--interval", type=float, default=5.0, help="Polling seconds (minimum 2, default 5)")
    args = parser.parse_args()
    if not 2 <= args.interval <= 60:
        parser.error("interval must be between 2 and 60 seconds")
    load_dotenv()
    service = AcquisitionService(load_config())
    if not service.settings.downloads_enabled:
        parser.error("Configure QBITTORRENT_URL before starting the acquisition monitor")
    try:
        with FileLock(str(service.store.root / ".monitor.lock"), timeout=0):
            runner = AcquisitionRunner(service)
            while True:
                runner.tick()
                if args.once:
                    return
                time.sleep(args.interval)
    except Timeout:
        parser.exit(1, "Another acquisition monitor owns this state directory\n")
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
