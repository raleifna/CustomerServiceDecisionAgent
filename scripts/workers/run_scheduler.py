"""Periodic housekeeping jobs (see src/backend/services/scheduled_jobs.py)."""

import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.services.scheduled_jobs import run_all


def main() -> int:
    parser = argparse.ArgumentParser(description="Run time-based after-sales housekeeping jobs")
    parser.add_argument("--once", action="store_true", help="Run every job once and exit")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        results = run_all()
        changed = {name: ids for name, ids in results.items() if ids}
        if changed:
            logging.info("scheduler changes: %s", changed)
        if args.once:
            return 0
        time.sleep(max(args.interval_seconds, 5.0))


if __name__ == "__main__":
    raise SystemExit(main())
