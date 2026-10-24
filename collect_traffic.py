
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from src.traffic import fetch_all_traffic
from src.database import init_db, save_observations

PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "traffic_collector.log"
COLLECTION_INTERVAL_SECONDS = 300  # 5 minutes


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger("cityflow_collector")


def collect_once():
    """Fetch and store one batch of live traffic observations."""
    started_at = datetime.now(timezone.utc).isoformat()
    logger.info("Starting traffic collection at %s", started_at)

    try:
        records, warnings = fetch_all_traffic("live")

        # Never save simulated data in this automatic live collector.
        live_records = [
            record for record in records
            if not record.get("simulated", True)
        ]

        if live_records:
            saved = save_observations(live_records)
            logger.info(
                "Collection complete: fetched=%d, live=%d, saved=%d",
                len(records),
                len(live_records),
                saved,
            )
        else:
            logger.warning(
                "No live observations were returned; nothing was saved."
            )

        for warning in warnings:
            logger.warning("Traffic API warning: %s", warning)

    except Exception:
        logger.exception("Traffic collection failed.")


def main():
    init_db()
    logger.info("CityFlow AI automatic traffic collector started.")
    logger.info(
        "Collection interval: %d seconds",
        COLLECTION_INTERVAL_SECONDS,
    )
    logger.info("Log file: %s", LOG_FILE)

    try:
        while True:
            # Collect immediately, then wait five minutes.
            collect_once()
            logger.info("Waiting 5 minutes for the next collection.")
            time.sleep(COLLECTION_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("Collector stopped by user.")


if __name__ == "__main__":
    main()
