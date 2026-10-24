"""Run a chronological 15-minute congestion forecast backtest."""

import json

from src.database import init_db, get_history
from src.forecasting import backtest_forecasts


def main():
    init_db()

    history = get_history(
        limit=250000,
        include_simulated=False,
    )

    if history.empty:
        print(
            "No stored traffic history found. "
            "Start collect_traffic.py first."
        )
        return

    result = backtest_forecasts(
        history,
        horizon_minutes=15,
        lookback_minutes=60,
        min_points=4,
        target_tolerance_minutes=5,
        min_targets=12,
    )

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()