
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium

from src.traffic import fetch_all_traffic
from src.weather import fetch_weather
from src.database import (
    init_db,
    save_observations,
    get_history,
    get_history_count,
)

st.set_page_config(
    page_title="CityFlow AI",
    page_icon="🚦",
    layout="wide",
)

init_db()

st.title("🚦 CityFlow AI")
st.caption("Traffic intelligence dashboard | Mysuru, Karnataka")

# ---------------- Sidebar ----------------

st.sidebar.header("Settings")

mode = st.sidebar.selectbox(
    "Traffic data source",
    options=["demo", "auto", "live"],
    index=1,
    format_func=lambda value: {
        "demo": "Demo (simulated)",
        "auto": "Automatic (live if available)",
        "live": "Live only",
    }[value],
)

refresh_clicked = st.sidebar.button(
    "Refresh data",
    type="primary",
    use_container_width=True,
)

# ---------------- Fetch and save traffic ----------------

if (
    "traffic_records" not in st.session_state
    or st.session_state.get("loaded_mode") != mode
    or refresh_clicked
):
    try:
        with st.spinner("Retrieving traffic observations..."):
            records, warnings = fetch_all_traffic(mode)

        st.session_state["traffic_records"] = records
        st.session_state["traffic_warnings"] = warnings
        st.session_state["loaded_mode"] = mode
        st.session_state["traffic_error"] = None

        # Every successful collection is saved to SQLite.
        saved_count = save_observations(records)
        st.session_state["last_saved_count"] = saved_count

    except Exception as exc:
        st.session_state["traffic_error"] = str(exc)
        st.session_state["traffic_records"] = []
        st.session_state["traffic_warnings"] = []
        st.session_state["last_saved_count"] = 0

records = st.session_state.get("traffic_records", [])
warnings = st.session_state.get("traffic_warnings", [])
traffic_error = st.session_state.get("traffic_error")

if traffic_error:
    st.error(f"Traffic data could not be loaded: {traffic_error}")
    st.info(
        "For live mode, verify your TomTom API key, API access, "
        "network connection, quota, and road coverage."
    )

if warnings:
    for warning in warnings:
        st.warning(warning)

if records:
    is_simulated = all(item.get("simulated", False) for item in records)

    if is_simulated:
        st.warning(
            "DEMO DATA: all displayed traffic speeds are simulated, "
            "not live road conditions."
        )
    else:
        live_count = sum(
            not item.get("simulated", False) for item in records
        )
        st.success(
            f"Traffic data retrieved. Live observations: {live_count}. "
            "Demo records, if any, are identified separately."
        )

    st.caption(
        f"Saved {st.session_state.get('last_saved_count', 0)} "
        "observations from the most recent collection to SQLite."
    )

# ---------------- Traffic metrics ----------------

st.subheader("Traffic overview")

if records:
    df = pd.DataFrame(records)

    live_df = df[~df["simulated"].astype(bool)].copy()

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Locations", len(df))
    col2.metric("Live observations", len(live_df))

    high_count = int(
        (live_df["status"] == "High").sum()
    ) if not live_df.empty else 0

    col3.metric("High congestion", high_count)

    if not live_df.empty:
        valid_scores = live_df["score"].dropna()
        avg_reduction = (
            f"{valid_scores.mean():.1f}%"
            if not valid_scores.empty
            else "N/A"
        )
    else:
        avg_reduction = "N/A"

    col4.metric("Average speed reduction", avg_reduction)

    # ---------------- Map ----------------

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

    for item in records:
        simulated_text = (
            "SIMULATED" if item.get("simulated", False) else "LIVE"
        )

        score = item.get("score")
        score_text = f"{score}%" if score is not None else "N/A"

        popup_html = f"""
        <b>{item['name']}</b><br>
        Source: {item.get('source', 'Unknown')}<br>
        Data type: {simulated_text}<br>
        Current speed: {item.get('current_speed_kmh', 'N/A')} km/h<br>
        Free-flow speed: {item.get('free_flow_speed_kmh', 'N/A')} km/h<br>
        Congestion score: {score_text}<br>
        Status: {item.get('status', 'Unknown')}
        """

        folium.CircleMarker(
            location=[item["lat"], item["lon"]],
            radius=9,
            color=colors.get(item.get("status"), "gray"),
            fill=True,
            fill_opacity=0.8,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=item["name"],
        ).add_to(traffic_map)

    st_folium(
        traffic_map,
        use_container_width=True,
        height=480,
        key="cityflow_traffic_map",
    )

    # ---------------- Current observations ----------------

    st.subheader("Current traffic observations")

    display_columns = [
        "name",
        "current_speed_kmh",
        "free_flow_speed_kmh",
        "score",
        "status",
        "source",
        "simulated",
    ]

    current_table = df[
        [column for column in display_columns if column in df.columns]
    ].copy()

    current_table = current_table.rename(columns={
        "name": "Location",
        "current_speed_kmh": "Current speed (km/h)",
        "free_flow_speed_kmh": "Free-flow speed (km/h)",
        "score": "Congestion score (%)",
        "status": "Congestion status",
        "source": "Data source",
        "simulated": "Simulated data",
    })

    st.dataframe(current_table, use_container_width=True)

    st.download_button(
        "Download current observations as CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="cityflow_current_traffic.csv",
        mime="text/csv",
    )

else:
    st.info("No traffic observations are currently available.")

# ---------------- Weather ----------------

st.subheader("🌦️ Current weather")

try:
    weather = fetch_weather()

    weather_col1, weather_col2, weather_col3, weather_col4 = st.columns(4)

    weather_col1.metric(
        "Temperature",
        f"{weather['temperature_c']} °C"
        if weather.get("temperature_c") is not None else "N/A",
    )
    weather_col2.metric(
        "Humidity",
        f"{weather['humidity_percent']}%"
        if weather.get("humidity_percent") is not None else "N/A",
    )
    weather_col3.metric(
        "Precipitation",
        f"{weather['precipitation_mm']} mm"
        if weather.get("precipitation_mm") is not None else "N/A",
    )
    weather_col4.metric(
        "Wind speed",
        f"{weather['wind_speed_kmh']} km/h"
        if weather.get("wind_speed_kmh") is not None else "N/A",
    )

    st.caption(
        f"Source: {weather.get('source', 'Weather API')} | "
        f"Observed at: {weather.get('observed_at', 'Unknown')} "
        "(local time)"
    )

except Exception as exc:
    st.warning(f"Weather data is temporarily unavailable: {exc}")

# ---------------- SQLite history ----------------

st.divider()
st.header("🗄️ Traffic history (SQLite)")

history_count = get_history_count(include_simulated=False)
st.metric("Saved live observations", history_count)

history = get_history(
    limit=5000,
    include_simulated=False,
)

if history.empty:
    st.info(
        "No live history has been saved yet. Select Live mode and click "
        "'Refresh data' to collect and save your first live observations."
    )
else:
    history["observed_at_utc"] = pd.to_datetime(
        history["observed_at_utc"],
        utc=True,
        errors="coerce",
    )

    st.caption(
        "The history table below contains saved live observations only. "
        "Times are shown in your local timezone (Asia/Kolkata)."
    )

    history_display = history.copy()
    history_display["observed_at_utc"] = (
        history_display["observed_at_utc"]
        .dt.tz_convert("Asia/Kolkata")
        .dt.strftime("%Y-%m-%d %H:%M:%S")
    )

    st.dataframe(
        history_display.sort_values(
            "observed_at_utc", ascending=False
        ),
        use_container_width=True,
        hide_index=True,
    )

    chart_history = history.dropna(
        subset=["observed_at_utc", "current_speed_kmh"]
    ).copy()

    if not chart_history.empty:
        chart_history = chart_history.sort_values("observed_at_utc")

        st.subheader("Historical traffic speeds")

        st.line_chart(
            chart_history,
            x="observed_at_utc",
            y="current_speed_kmh",
            color="location_name",
        )

    st.download_button(
        "Download live history as CSV",
        data=history.to_csv(index=False).encode("utf-8"),
        file_name="cityflow_traffic_history.csv",
        mime="text/csv",
    )

st.caption(
    "CityFlow AI | Traffic history is stored locally in data/cityflow.db. "
    "Live readings are snapshots collected when data is refreshed."
)
