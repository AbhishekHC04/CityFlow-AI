"""15-minute congestion forecasting and chronological validation."""

import math
from typing import Any

import numpy as np
import pandas as pd


def _status(score: float) -> str:
    if score >= 60:
        return "High"
    if score >= 25:
        return "Moderate"
    return "Low"


def _prepare(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame()

    data = history.copy()
    required = {
        "location_name",
        "observed_at_utc",
        "congestion_score",
    }

    if not required.issubset(data.columns):
        return pd.DataFrame()

    data["observed_at_utc"] = pd.to_datetime(
        data["observed_at_utc"], utc=True, errors="coerce"
    )
    data["congestion_score"] = pd.to_numeric(
        data["congestion_score"], errors="coerce"
    )

    if "simulated" in data.columns:
        data = data[data["simulated"].fillna(0).astype(int) == 0]

    data = data.dropna(
        subset=[
            "location_name",
            "observed_at_utc",
            "congestion_score",
        ]
    )
    data = data[data["congestion_score"].between(0, 100)]

    return data.sort_values("observed_at_utc")


def _predict_at(
    location_history: pd.DataFrame,
    cutoff: pd.Timestamp,
    horizon_minutes: int = 15,
    lookback_minutes: int = 60,
    min_points: int = 4,
) -> dict[str, Any] | None:
    past = location_history[
        location_history["observed_at_utc"] <= cutoff
    ].copy()

    if past.empty:
        return None

    latest = past.iloc[-1]
    current_score = float(latest["congestion_score"])

    window_start = cutoff - pd.Timedelta(minutes=lookback_minutes)
    window = past[
        past["observed_at_utc"] >= window_start
    ].copy()

    window = window.drop_duplicates(
        "observed_at_utc", keep="last"
    )

    if len(window) < min_points:
        return {
            "current_congestion_pct": current_score,
            "predicted_congestion_pct": np.nan,
            "predicted_status": "Insufficient history",
            "trend_per_15min": np.nan,
            "history_points": len(window),
            "forecast_method": "Not available",
            "error": (
                f"Need {min_points} readings in the last "
                f"{lookback_minutes} minutes."
            ),
        }

    x = (
        window["observed_at_utc"]
        - window["observed_at_utc"].min()
    ).dt.total_seconds().to_numpy(dtype=float) / 60.0

    y = window["congestion_score"].to_numpy(dtype=float)

    if len(np.unique(x)) < min_points or float(np.ptp(x)) < 10:
        return {
            "current_congestion_pct": current_score,
            "predicted_congestion_pct": np.nan,
            "predicted_status": "Insufficient time span",
            "trend_per_15min": np.nan,
            "history_points": len(window),
            "forecast_method": "Not available",
            "error": "Readings must span at least 10 minutes.",
        }

    slope, intercept = np.polyfit(x, y, 1)

    last_x = (
        cutoff - window["observed_at_utc"].min()
    ).total_seconds() / 60.0

    raw_prediction = intercept + slope * (
        last_x + horizon_minutes
    )

    prediction = float(np.clip(raw_prediction, 0.0, 100.0))

    return {
        "current_congestion_pct": current_score,
        "predicted_congestion_pct": prediction,
        "predicted_status": _status(prediction),
        "trend_per_15min": prediction - current_score,
        "history_points": len(window),
        "forecast_method": "Linear trend baseline",
        "error": None,
    }


def forecast_congestion(
    history: pd.DataFrame,
    horizon_minutes: int = 15,
    lookback_minutes: int = 60,
    min_points: int = 4,
) -> pd.DataFrame:
    """Forecast each location using only observations available so far."""

    data = _prepare(history)

    if data.empty:
        return pd.DataFrame()

    latest_time = data["observed_at_utc"].max()
    results = []

    for location, group in data.groupby(
        "location_name", sort=True
    ):
        result = _predict_at(
            group,
            latest_time,
            horizon_minutes=horizon_minutes,
            lookback_minutes=lookback_minutes,
            min_points=min_points,
        )

        if result is None:
            continue

        result["location_name"] = location
        result["forecast_for_utc"] = (
            latest_time + pd.Timedelta(minutes=horizon_minutes)
        )
        results.append(result)

    return pd.DataFrame(results)


def _metrics(
    actual: np.ndarray,
    predicted: np.ndarray,
) -> dict[str, float]:
    errors = predicted - actual

    return {
        "MAE (percentage points)": round(
            float(np.mean(np.abs(errors))), 3
        ),
        "RMSE (percentage points)": round(
            float(math.sqrt(np.mean(errors ** 2))), 3
        ),
        "Mean error (percentage points)": round(
            float(np.mean(errors)), 3
        ),
    }


def backtest_forecasts(
    history: pd.DataFrame,
    horizon_minutes: int = 15,
    lookback_minutes: int = 60,
    min_points: int = 4,
    target_tolerance_minutes: int = 5,
    min_targets: int = 12,
) -> dict[str, Any]:
    """
    Evaluate forecasts chronologically, comparing the trend baseline
    with persistence. Random shuffling is not used.
    """

    data = _prepare(history)

    if data.empty:
        return {
            "status": "insufficient",
            "message": "No valid live history is available.",
        }

    unique_times = pd.Series(
        data["observed_at_utc"]
        .drop_duplicates()
        .sort_values()
    )

    if len(unique_times) < 8:
        return {
            "status": "insufficient",
            "message": (
                "Collect more readings across time before validation."
            ),
        }

    split_index = max(
        1,
        min(
            len(unique_times) - 1,
            int(len(unique_times) * 0.70),
        ),
    )

    cutoff = pd.Timestamp(unique_times.iloc[split_index])
    prediction_rows = []

    tolerance = pd.Timedelta(
        minutes=target_tolerance_minutes
    )
    horizon = pd.Timedelta(minutes=horizon_minutes)

    for location, group in data.groupby(
        "location_name", sort=True
    ):
        group = (
            group.sort_values("observed_at_utc")
            .drop_duplicates("observed_at_utc", keep="last")
        )

        targets = group[
            group["observed_at_utc"] >= cutoff
        ]

        for _, target in targets.iterrows():
            target_time = target["observed_at_utc"]
            origin = target_time - horizon

            past = group[
                group["observed_at_utc"] <= origin
            ]

            if past.empty:
                continue

            if abs(target_time - (origin + horizon)) > tolerance:
                continue

            prediction = _predict_at(
                group,
                origin,
                horizon_minutes=horizon_minutes,
                lookback_minutes=lookback_minutes,
                min_points=min_points,
            )

            if (
                not prediction
                or pd.isna(
                    prediction.get("predicted_congestion_pct")
                )
            ):
                continue

            prediction_rows.append(
                {
                    "location_name": location,
                    "target_time": target_time,
                    "actual": float(target["congestion_score"]),
                    "trend_prediction": float(
                        prediction["predicted_congestion_pct"]
                    ),
                    "persistence_prediction": float(
                        prediction["current_congestion_pct"]
                    ),
                }
            )

    if len(prediction_rows) < min_targets:
        return {
            "status": "insufficient",
            "message": (
                f"Only {len(prediction_rows)} valid 15-minute test "
                f"targets were found; at least {min_targets} are "
                "required. Keep the collector running across more "
                "time periods and days."
            ),
            "n_targets": len(prediction_rows),
            "cutoff": cutoff.isoformat(),
        }

    result_df = pd.DataFrame(prediction_rows)

    actual = result_df["actual"].to_numpy(dtype=float)
    trend = result_df["trend_prediction"].to_numpy(dtype=float)
    persistence = result_df[
        "persistence_prediction"
    ].to_numpy(dtype=float)

    metrics = [
        {
            "Model": "Linear trend baseline",
            **_metrics(actual, trend),
        },
        {
            "Model": "Persistence baseline",
            **_metrics(actual, persistence),
        },
    ]

    result_df["target_day"] = (
        pd.to_datetime(result_df["target_time"], utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.strftime("%Y-%m-%d")
    )

    by_day = []

    for day, day_df in result_df.groupby(
        "target_day", sort=True
    ):
        a = day_df["actual"].to_numpy(dtype=float)
        t = day_df["trend_prediction"].to_numpy(dtype=float)
        p = day_df[
            "persistence_prediction"
        ].to_numpy(dtype=float)

        by_day.append(
            {
                "Target day (IST)": day,
                "Targets": len(day_df),
                "Trend MAE": round(
                    float(np.mean(np.abs(t - a))), 3
                ),
                "Trend RMSE": round(
                    float(math.sqrt(np.mean((t - a) ** 2))), 3
                ),
                "Persistence MAE": round(
                    float(np.mean(np.abs(p - a))), 3
                ),
                "Persistence RMSE": round(
                    float(math.sqrt(np.mean((p - a) ** 2))), 3
                ),
            }
        )

    return {
        "status": "ok",
        "metrics": metrics,
        "by_day": by_day,
        "n_targets": len(result_df),
        "n_locations": int(
            result_df["location_name"].nunique()
        ),
        "n_days": int(result_df["target_day"].nunique()),
        "cutoff": cutoff.isoformat(),
    }