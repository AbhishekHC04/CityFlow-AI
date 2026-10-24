
import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium

from src.traffic import fetch_all_traffic
from src.weather import fetch_weather

st.set_page_config(
    page_title="CityFlow AI",
    page_icon="🚦",
    layout="wide",
)

st.title("🚦 CityFlow AI")
st.caption("Traffic intelligence dashboard | Mysuru, Karnataka")

with st.sidebar:
    st.header("Settings")
    mode = st.selectbox(
        "Traffic data source",
        ["demo", "auto", "live"],
        index=0,
    )
    refresh = st.button("Refresh data", type="primary")

if refresh or "traffic_records" not in st.session_state:
    try:
        records, warnings = fetch_all_traffic(mode)
        st.session_state.traffic_records = records
        st.session_state.traffic_warnings = warnings
        st.session_state.traffic_mode = mode
    except Exception as exc:
        st.error(f"Traffic service error: {exc}")
        st.stop()

if st.session_state.traffic_mode != mode:
    st.info("Click Refresh data to load the selected traffic mode.")

records = st.session_state.traffic_records
warnings = st.session_state.traffic_warnings

df = pd.DataFrame(records)

if df["simulated"].all():
    st.warning(
        "DEMO DATA: all traffic speeds below are simulated, "
        "not live road conditions."
    )
elif df["simulated"].any():
    st.warning(
        "MIXED DATA: some observations are simulated. "
        "Check the source column."
    )
else:
    st.success("Live traffic observations retrieved from TomTom.")

for warning in warnings:
    st.warning(warning)

live_df = df[~df["simulated"]]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Locations", len(df))
c2.metric("Live observations", len(live_df))
c3.metric(
    "High congestion",
    int((live_df["status"] == "High").sum()),
)
c4.metric(
    "Average speed reduction",
    f"{live_df['score'].mean():.1f}%"
    if not live_df.empty else "N/A",
)

st.subheader("📍 Mysuru traffic map")

traffic_map = folium.Map(
    location=[12.2958, 76.6394],
    zoom_start=13,
    tiles="OpenStreetMap",
)

colors = {
    "Low": "green",
    "Moderate": "orange",
    "High": "red",
    "Unknown": "gray",
}

for row in records:
    popup = (
        f"Location: {row['name']}<br>"
        f"Source: {row['source']}<br>"
        f"Current speed: {row['current_speed_kmh']} km/h<br>"
        f"Free-flow speed: {row['free_flow_speed_kmh']} km/h<br>"
        f"Speed reduction: {row['score']}%<br>"
        f"Status: {row['status']}<br>"
        f"Simulated: {row['simulated']}"
    )

    folium.CircleMarker(
        location=[row["lat"], row["lon"]],
        radius=9,
        color=colors[row["status"]],
        fill=True,
        fill_opacity=0.8,
        tooltip=row["name"],
        popup=folium.Popup(popup, max_width=300),
    ).add_to(traffic_map)

st_folium(
    traffic_map,
    use_container_width=True,
    height=480,
    key="cityflow_map",
)

st.subheader("📊 Traffic observations")

st.dataframe(
    df[
        [
            "name",
            "current_speed_kmh",
            "free_flow_speed_kmh",
            "score",
            "status",
            "source",
            "simulated",
        ]
    ],
    use_container_width=True,
    hide_index=True,
)

st.download_button(
    "Download traffic CSV",
    data=df.to_csv(index=False).encode("utf-8"),
    file_name="cityflow_traffic.csv",
    mime="text/csv",
)

st.subheader("🌦️ Current weather")

try:
    weather = fetch_weather()

    w1, w2, w3, w4 = st.columns(4)
    w1.metric("Temperature", f"{weather['temperature_c']} °C")
    w2.metric("Rainfall", f"{weather['precipitation_mm']} mm")
    w3.metric("Wind", f"{weather['wind_speed_kmh']} km/h")
    w4.metric("Humidity", f"{weather['humidity_percent']}%")

    st.caption(
        f"Source: {weather['source']} | "
        f"Observation time: {weather['observed_at']}"
    )
except Exception as exc:
    st.warning(f"Weather data unavailable: {exc}")

st.divider()
st.caption(
    "Map © OpenStreetMap contributors | "
    "Weather by Open-Meteo | "
    "Traffic source is displayed for every observation."
)
