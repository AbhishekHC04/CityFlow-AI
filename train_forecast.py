
from pathlib import Path
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from src.database import get_history


PROJECT_DIR = Path(__file__).resolve().parent
ARTIFACT_DIR = PROJECT_DIR / "artifacts"
MODEL_PATH = ARTIFACT_DIR / "traffic_forecast.joblib"
METRICS_PATH = ARTIFACT_DIR / "traffic_forecast_metrics.json"

FORECAST_MINUTES = 15
MIN_TARGET_MINUTES = 12
MAX_TARGET_MINUTES = 18

MINIMUM_TRAIN_ROWS = 60
MINIMUM_TEST_ROWS = 12

FEATURES = [
    "current_score",
    "current_speed",
    "free_flow_speed",
    "previous_score",
    "previous_speed",
    "score_change",
    "speed_change",
    "hour",
    "minute",
    "day_of_week",
]


def prepare_training_data(history):
    """Create examples by matching readings to actual future observations."""

    if history.empty:
        return pd.DataFrame()

    data = history.copy()

    data["observed_at_utc"] = pd.to_datetime(
        data["observed_at_utc"],
        utc=True,
        errors="coerce",
    )

    required = [
        "observed_at_utc",
        "location_name",
        "current_speed_kmh",
        "free_flow_speed_kmh",
        "congestion_score",
    ]

    data = data.dropna(subset=required).copy()

    data = data[
        data["congestion_score"].between(0, 100)
        & (data["current_speed_kmh"] >= 0)
        & (data["free_flow_speed_kmh"] > 0)
    ].copy()

    if data.empty:
        return pd.DataFrame()

    data = data.sort_values(
        ["location_name", "observed_at_utc"]
    )

    examples = []

    for location, group in data.groupby("location_name"):
        group = group.sort_values("observed_at_utc").reset_index(drop=True)

        times = group["observed_at_utc"]

        for i in range(len(group)):
            current = group.iloc[i]

            # Need a previous observation to calculate changes.
            if i == 0:
                continue

            previous = group.iloc[i - 1]

            current_time = current["observed_at_utc"]

            target_time = current_time + pd.Timedelta(
                minutes=FORECAST_MINUTES
            )

            lower_time = current_time + pd.Timedelta(
                minutes=MIN_TARGET_MINUTES
            )

            upper_time = current_time + pd.Timedelta(
                minutes=MAX_TARGET_MINUTES
            )

            future_positions = np.flatnonzero(
                (
                    (times >= lower_time)
                    & (times <= upper_time)
                ).to_numpy()
            )

            if len(future_positions) == 0:
                continue

            # Select the actual reading closest to t + 15 minutes.
            future_position = min(
                future_positions,
                key=lambda position: abs(
                    (
                        times.iloc[position] - target_time
                    ).total_seconds()
                ),
            )

            future = group.iloc[future_position]

            # Never use the future row's features as inputs.
            examples.append({
                "location_name": location,
                "observed_at_utc": current_time,
                "target_observed_at_utc": future["observed_at_utc"],
                "current_score": float(current["congestion_score"]),
                "current_speed": float(current["current_speed_kmh"]),
                "free_flow_speed": float(current["free_flow_speed_kmh"]),
                "previous_score": float(previous["congestion_score"]),
                "previous_speed": float(previous["current_speed_kmh"]),
                "score_change": float(
                    current["congestion_score"]
                    - previous["congestion_score"]
                ),
                "speed_change": float(
                    current["current_speed_kmh"]
                    - previous["current_speed_kmh"]
                ),
                "hour": int(current_time.hour),
                "minute": int(current_time.minute),
                "day_of_week": int(current_time.day_of_week),
                "target_score_15m": float(future["congestion_score"]),
            })

    if not examples:
        return pd.DataFrame()

    result = pd.DataFrame(examples)
    result = result.replace([np.inf, -np.inf], np.nan)
    result = result.dropna(
        subset=FEATURES + ["target_score_15m"]
    )

    return result.sort_values(
        "observed_at_utc"
    ).reset_index(drop=True)


def main():
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    history = get_history(
        limit=100000,
        include_simulated=False,
    )

    print("=" * 60)
    print("CITYFLOW AI - 15-MINUTE CONGESTION FORECAST")
    print("=" * 60)
    print(f"Saved live observations: {len(history)}")

    if history.empty:
        print("No live traffic history found. Start the collector first.")
        return

    history["observed_at_utc"] = pd.to_datetime(
        history["observed_at_utc"],
        utc=True,
        errors="coerce",
    )

    valid_times = history["observed_at_utc"].dropna()

    if not valid_times.empty:
        duration = (
            valid_times.max() - valid_times.min()
        ).total_seconds() / 60
        print(f"Historical time span: {duration:.1f} minutes")

    print("\nObservations per location:")
    print(history.groupby("location_name").size().to_string())

    data = prepare_training_data(history)

    print(f"\nUsable supervised examples: {len(data)}")

    if data.empty:
        print(
            "\nNo matching observations were found 12-18 minutes "
            "after earlier readings."
        )
        print(
            "Continue collecting live traffic. The model needs actual "
            "future readings; it will not fabricate target values."
        )
        return

    print(
        "Example time range:",
        data["observed_at_utc"].min(),
        "to",
        data["observed_at_utc"].max(),
    )

    print(
        "Locations represented:",
        data["location_name"].nunique(),
    )

    if len(data) < MINIMUM_TRAIN_ROWS + MINIMUM_TEST_ROWS:
        print("\nNot enough examples for a defensible evaluation.")
        print(f"Required training examples: {MINIMUM_TRAIN_ROWS}")
        print(f"Required test examples: {MINIMUM_TEST_ROWS}")
        print("Continue collecting data and run this script later.")
        return

    # Use a chronological holdout, not a random split.
    unique_times = np.sort(
        data["observed_at_utc"].unique()
    )

    if len(unique_times) < 10:
        print("Too few distinct timestamps for evaluation.")
        return

    cutoff = unique_times[
        max(1, min(int(len(unique_times) * 0.8), len(unique_times) - 1))
    ]

    train = data[data["observed_at_utc"] < cutoff].copy()
    test = data[data["observed_at_utc"] >= cutoff].copy()

    if (
        len(train) < MINIMUM_TRAIN_ROWS
        or len(test) < MINIMUM_TEST_ROWS
    ):
        print(
            f"Chronological split too small: "
            f"training={len(train)}, test={len(test)}."
        )
        print("Collect more observations before training.")
        return

    model = RandomForestRegressor(
        n_estimators=200,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )

    model.fit(
        train[FEATURES],
        train["target_score_15m"],
    )

    predictions = np.clip(
        model.predict(test[FEATURES]),
        0,
        100,
    )

    actual = test["target_score_15m"].to_numpy()

    mae = float(mean_absolute_error(actual, predictions))
    rmse = float(np.sqrt(mean_squared_error(actual, predictions)))

    # Persistence baseline: predict that congestion stays unchanged.
    baseline_predictions = test["current_score"].to_numpy()

    baseline_mae = float(
        mean_absolute_error(actual, baseline_predictions)
    )

    metrics = {
        "model": "RandomForestRegressor",
        "forecast_minutes": FORECAST_MINUTES,
        "target_window_minutes": [
            MIN_TARGET_MINUTES,
            MAX_TARGET_MINUTES,
        ],
        "training_rows": int(len(train)),
        "test_rows": int(len(test)),
        "locations": sorted(
            data["location_name"].unique().tolist()
        ),
        "training_start_utc": str(train["observed_at_utc"].min()),
        "training_end_utc": str(train["observed_at_utc"].max()),
        "test_start_utc": str(test["observed_at_utc"].min()),
        "test_end_utc": str(test["observed_at_utc"].max()),
        "mae_percentage_points": mae,
        "rmse_percentage_points": rmse,
        "persistence_baseline_mae": baseline_mae,
        "beats_persistence_baseline": bool(mae < baseline_mae),
        "note": (
            "Chronological holdout evaluation. Targets are actual "
            "readings 12-18 minutes after each input observation. "
            "Results are preliminary and do not guarantee future accuracy."
        ),
    }

    joblib.dump(
        {
            "model": model,
            "features": FEATURES,
            "forecast_minutes": FORECAST_MINUTES,
        },
        MODEL_PATH,
    )

    METRICS_PATH.write_text(
        json.dumps(metrics, indent=2),
        encoding="utf-8",
    )

    print("\nEVALUATION RESULTS")
    print("-" * 60)
    print(f"Training examples: {len(train)}")
    print(f"Test examples: {len(test)}")
    print(f"MAE: {mae:.2f} congestion percentage points")
    print(f"RMSE: {rmse:.2f} congestion percentage points")
    print(f"Persistence baseline MAE: {baseline_mae:.2f}")
    print(f"Beats persistence baseline: {mae < baseline_mae}")
    print(f"Model saved: {MODEL_PATH}")
    print(f"Metrics saved: {METRICS_PATH}")

    if mae >= baseline_mae:
        print(
            "\nWARNING: The model did not beat the persistence baseline "
            "on the held-out data."
        )


if __name__ == "__main__":
    main()
