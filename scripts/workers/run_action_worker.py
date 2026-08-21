import argparse
import logging
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.services.action_execution_service import process_one_action


def main() -> int:
    parser = argparse.ArgumentParser(description="Process reliable after-sales action tasks")
    parser.add_argument("--once", action="store_true", help="Process at most one task")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    while True:
        result = process_one_action()
        if result:
            logging.info("action result: %s", result)
        if args.once:
            return 0
        if result is None:
            time.sleep(max(args.poll_seconds, 0.1))


if __name__ == "__main__":
    raise SystemExit(main())
