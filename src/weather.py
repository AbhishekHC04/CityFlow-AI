
import requests

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_weather(latitude=12.2958, longitude=76.6394):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,relative_humidity_2m,"
            "precipitation,wind_speed_10m"
        ),
        "timezone": "Asia/Kolkata",
    }

    response = requests.get(WEATHER_URL, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()

    current = payload.get("current")
    if not current:
        raise ValueError("Weather API returned no current observations")

    return {
        "temperature_c": current.get("temperature_2m"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "precipitation_mm": current.get("precipitation"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "observed_at": current.get("time"),
        "source": "Open-Meteo",
        "simulated": False,
    }
