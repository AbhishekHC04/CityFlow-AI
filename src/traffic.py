
import os
import random
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

TOMTOM_URL = (
    "https://api.tomtom.com/traffic/services/4/"
    "flowSegmentData/absolute/10/json"
)

# Approximate sample points around Mysuru.
LOCATIONS = [
    {"name": "Mysuru Palace area", "lat": 12.3052, "lon": 76.6552},
    {"name": "K R Circle area", "lat": 12.3030, "lon": 76.6500},
    {"name": "Vijayanagar area", "lat": 12.3350, "lon": 76.6200},
    {"name": "Hebbal area", "lat": 12.3500, "lon": 76.6250},
    {"name": "Nazarbad area", "lat": 12.3070, "lon": 76.6650},
    {"name": "Siddhartha Layout area", "lat": 12.2850, "lon": 76.6750},
]


def congestion_score(current_speed, free_flow_speed):
    """Calculate speed reduction compared with free-flow speed."""
    if free_flow_speed is None or free_flow_speed <= 0:
        return None

    score = 100 * (1 - current_speed / free_flow_speed)
    return round(max(0, min(100, score)), 1)


def congestion_label(score):
    if score is None:
        return "Unknown"
    if score < 20:
        return "Low"
    if score < 45:
        return "Moderate"
    return "High"


def fetch_live_segment(location, api_key):
    params = {
        "key": api_key,
        "point": f"{location['lat']},{location['lon']}",
        "unit": "kmph",
    }

    response = requests.get(TOMTOM_URL, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()

    flow = payload.get("flowSegmentData")
    if not flow:
        raise ValueError("No flowSegmentData in API response")

    current = float(flow["currentSpeed"])
    free_flow = float(flow["freeFlowSpeed"])
    score = congestion_score(current, free_flow)

    return {
        **location,
        "current_speed_kmh": current,
        "free_flow_speed_kmh": free_flow,
        "travel_time_seconds": flow.get("currentTravelTime"),
        "free_flow_time_seconds": flow.get("freeFlowTravelTime"),
        "confidence": flow.get("confidence"),
        "road_closed": flow.get("roadClosure", False),
        "score": score,
        "status": congestion_label(score),
        "source": "TomTom Traffic API",
        "simulated": False,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def fetch_demo_segment(location, seed=42):
    """Generate reproducible sample data for testing only."""
    rng = random.Random(seed + int(location["lat"] * 10000))
    free_flow = rng.randint(40, 60)
    current = rng.randint(10, free_flow)
    score = congestion_score(current, free_flow)

    return {
        **location,
        "current_speed_kmh": current,
        "free_flow_speed_kmh": free_flow,
        "travel_time_seconds": None,
        "free_flow_time_seconds": None,
        "confidence": None,
        "road_closed": False,
        "score": score,
        "status": congestion_label(score),
        "source": "SIMULATED DEMO DATA",
        "simulated": True,
        "retrieved_at_utc": None,
    }


def fetch_all_traffic(mode="auto"):
    """
    mode='live': require actual API observations.
    mode='demo': use generated observations only.
    mode='auto': try live data; fall back to demo if none succeeds.
    """
    api_key = os.getenv("TOMTOM_API_KEY", "").strip()

    if mode == "demo":
        return [fetch_demo_segment(p) for p in LOCATIONS], [
            "Demo mode: every traffic speed is simulated."
        ]

    if not api_key:
        if mode == "live":
            raise RuntimeError(
                "TOMTOM_API_KEY is missing from your .env file."
            )
        return [fetch_demo_segment(p) for p in LOCATIONS], [
            "No TomTom API key configured; displaying simulated traffic."
        ]

    records = []
    warnings = []

    for location in LOCATIONS:
        try:
            records.append(fetch_live_segment(location, api_key))
        except (
            requests.RequestException,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            warnings.append(f"{location['name']}: {exc}")

    if not records and mode == "live":
        raise RuntimeError(
            "No live traffic readings retrieved. Check your API key, "
            "network, product access, quota, and road coverage. "
            + "; ".join(warnings)
        )

    if not records:
        return [fetch_demo_segment(p) for p in LOCATIONS], [
            "Live traffic unavailable; displaying SIMULATED data.",
            *warnings,
        ]

    return records, warnings
