import re
import time
from pathlib import Path

import folium
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_folium import st_folium

from src.traffic import fetch_all_traffic
from src.weather import fetch_weather
from src.database import init_db, save_observations, get_history, get_history_count

PROJECT_DIR = Path(__file__).resolve().parent
LOG_FILE = PROJECT_DIR / "logs" / "traffic_collector.log"
IST = "Asia/Kolkata"

st.set_page_config(
    page_title="CityFlow AI | Traffic Command Center",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()

# =========================================================
# DARK THEME
# =========================================================

st.markdown(
    """
    <style>
    :root {
        color-scheme: dark;
        --cf-bg: #080d18;
        --cf-panel: #101827;
        --cf-border: #263449;
        --cf-text: #e7eefb;
        --cf-muted: #91a2bb;
        --cf-cyan: #35d5f2;
    }

    .stApp {
        background:
            radial-gradient(
                ellipse at 15% 0%,
                rgba(30, 70, 135, .22),
                transparent 38%
            ),
            linear-gradient(180deg, #080d18 0%, #0a1120 100%);
        color: var(--cf-text);
    }

    .block-container {
        max-width: 1600px;
        padding-top: 1.25rem;
        padding-bottom: 2.5rem;
    }

    [data-testid="stSidebar"] {
        background: linear-gradient(
            180deg, #0d1524 0%, #0a101c 100%
        );
        border-right: 1px solid #263449;
    }

    [data-testid="stSidebar"] * {
        color: #dce7f8;
    }

    .cf-hero {
        border: 1px solid #263c5a;
        border-radius: 22px;
        padding: 25px 28px;
        background: linear-gradient(
            115deg,
            rgba(20, 39, 70, .98),
            rgba(14, 25, 44, .96) 65%,
            rgba(14, 49, 67, .92)
        );
        box-shadow: 0 18px 55px rgba(0,0,0,.22);
        margin-bottom: 18px;
    }

    .cf-eyebrow {
        color: #65dff5;
        letter-spacing: .16em;
        font-size: .72rem;
        font-weight: 800;
        text-transform: uppercase;
    }

    .cf-hero h1 {
        margin: 7px 0 5px 0;
        font-size: clamp(1.8rem, 3vw, 2.6rem);
        color: #f3f8ff;
    }

    .cf-hero p {
        color: #a9bad3;
        margin: 0;
        font-size: .98rem;
    }

    .cf-pill {
        display: inline-block;
        border: 1px solid #24576b;
        background: rgba(17, 111, 133, .18);
        color: #7ce7f6;
        border-radius: 999px;
        padding: 5px 10px;
        font-size: .78rem;
        margin-top: 13px;
    }

    div[data-testid="stMetric"] {
        background: linear-gradient(
            145deg,
            rgba(19, 30, 49, .98),
            rgba(13, 22, 37, .98)
        );
        border: 1px solid #27364b;
        padding: 15px 17px;
        border-radius: 16px;
        box-shadow: 0 8px 25px rgba(0,0,0,.12);
    }

    div[data-testid="stMetricLabel"] {
        color: #9eb0ca;
    }

    div[data-testid="stMetricValue"] {
        color: #eff6ff;
        font-weight: 750;
    }

    div.stButton > button,
    div.stDownloadButton > button {
        border-radius: 11px;
        border: 1px solid #334760;
        min-height: 42px;
        font-weight: 650;
    }

    div.stButton > button[kind="primary"] {
        background: linear-gradient(90deg, #2463eb, #0ea5c6);
        border: 0;
    }

    div[data-testid="stDataFrame"] {
        border: 1px solid #263449;
        border-radius: 13px;
        overflow: hidden;
    }

    div[data-testid="stAlert"] {
        border-radius: 12px;
    }

    hr {
        border-color: #263449;
    }

    footer {
        visibility: hidden;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# HELPERS
# =========================================================

def status_color(status):
    return {
        "Low": "#22c55e",
        "Moderate": "#f59e0b",
        "High": "#ef4444",
    }.get(str(status), "#94a3b8")


def safe_float(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@st.cache_data(ttl=300, show_spinner=False)
def load_weather():
    return fetch_weather()


def read_collector_status():
    result = {
        "exists": LOG_FILE.exists(),
        "success_count": 0,
        "error_count": 0,
        "last_success": None,
        "recent_errors": [],
        "age_minutes": None,
    }

    if not result["exists"]:
        return result

    try:
        with LOG_FILE.open(
            "r", encoding="utf-8", errors="replace"
        ) as f:
            lines = f.readlines()[-2500:]

        success_lines = [
            line.strip()
            for line in lines
            if "Collection complete:" in line
        ]

        errors = [
            line.strip()
            for line in lines
            if "| ERROR |" in line
            or "Collection failed" in line
        ]

        result["success_count"] = len(success_lines)
        result["error_count"] = len(errors)
        result["last_success"] = (
            success_lines[-1] if success_lines else None
        )
        result["recent_errors"] = errors[-10:]

        if result["last_success"]:
            match = re.match(
                r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
                result["last_success"],
            )

            if match:
                timestamp = pd.to_datetime(
                    match.group(1), errors="coerce"
                )

                if pd.notna(timestamp):
                    now = pd.Timestamp.now()
                    result["age_minutes"] = max(
                        0.0,
                        (now - timestamp).total_seconds() / 60,
                    )

    except OSError:
        pass

    return result


def get_latest_by_location(history_df):
    if history_df.empty:
        return history_df.copy()

    data = history_df.copy()

    data["observed_at_utc"] = pd.to_datetime(
        data["observed_at_utc"],
        utc=True,
        errors="coerce",
    )

    data = data.dropna(subset=["observed_at_utc"])
    data = data.sort_values(["observed_at_utc", "id"])

    return (
        data.groupby(
            "location_name",
            as_index=False,
            sort=False,
        )
        .tail(1)
        .sort_values("location_name")
    )


def build_traffic_map(df, selected_location=None):
    traffic_map = folium.Map(
        location=[12.2958, 76.6394],
        zoom_start=12,
        tiles="CartoDB dark_matter",
        control_scale=True,
    )

    if df.empty:
        return traffic_map

    for _, row in df.iterrows():
        lat = safe_float(row.get("latitude"))
        lon = safe_float(row.get("longitude"))

        if lat is None or lon is None:
            continue

        status = row.get("congestion_status", "Unknown")
        color = {
            "Low": "#22c55e",
            "Moderate": "#f59e0b",
            "High": "#ef4444",
        }.get(str(status), "#94a3b8")

        name = str(row.get("location_name", "Unknown location"))
        score = safe_float(row.get("congestion_score"))
        speed = safe_float(row.get("current_speed_kmh"))
        free_flow = safe_float(row.get("free_flow_speed_kmh"))

        selected = selected_location == name

        popup_html = f"""
        <div style="font-family:Arial;min-width:210px;
                    color:#e8eef8;background:#101827">
          <h4 style="margin:0 0 8px;color:{color}">{name}</h4>
          <b>Status:</b> {status}<br>
          <b>Congestion:</b>
          {f'{score:.1f}%' if score is not None else 'N/A'}<br>
          <b>Current speed:</b>
          {f'{speed:.1f} km/h' if speed is not None else 'N/A'}<br>
          <b>Free-flow speed:</b>
          {f'{free_flow:.1f} km/h' if free_flow is not None else 'N/A'}<br>
          <b>Source:</b> {row.get('source', 'Unknown')}<br>
          <b>Observed (IST):</b>
          {row.get('observed_at_local', 'Unknown')}
        </div>
        """

        folium.CircleMarker(
            location=[lat, lon],
            radius=13 if selected else 9,
            color=color,
            weight=3 if selected else 2,
            fill=True,
            fill_color=color,
            fill_opacity=0.88,
            tooltip=(
                f"{name} · {status} · "
                f"{f'{score:.1f}%' if score is not None else 'N/A'}"
            ),
            popup=folium.Popup(popup_html, max_width=300),
        ).add_to(traffic_map)

    return traffic_map


def apply_plotly_dark(fig, title=None):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#dce7f8"),
        margin=dict(
            l=12,
            r=12,
            t=55 if title else 25,
            b=12,
        ),
        legend_title_text="Location",
        hovermode="x unified",
    )

    if title:
        fig.update_layout(title=title)

    fig.update_xaxes(
        showgrid=True,
        gridcolor="rgba(100,125,160,.15)",
        zeroline=False,
    )

    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(100,125,160,.15)",
        zeroline=False,
    )

    return fig


def fetch_current(mode):
    with st.spinner("Fetching current traffic readings..."):
        records, warnings = fetch_all_traffic(mode)

    st.session_state["traffic_records"] = records
    st.session_state["traffic_warnings"] = warnings
    st.session_state["loaded_mode"] = mode
    st.session_state["traffic_error"] = None
    st.session_state["last_fetch_epoch"] = time.time()

    # Save readings using the existing database schema.
    st.session_state["last_saved_count"] = save_observations(
        records
    )


# =========================================================
# MAIN DASHBOARD — REFRESH VIEW EVERY 30 SECONDS
# =========================================================

@st.fragment(run_every="30s")
def render_dashboard():

    # -------------------- SIDEBAR --------------------

    with st.sidebar:
        st.markdown("## 🚦 CityFlow AI")
        st.caption("TRAFFIC INTELLIGENCE · MYSURU")
        st.divider()

        st.markdown("### Controls")

        mode = st.selectbox(
            "Traffic data source",
            options=["live", "auto", "demo"],
            index=0,
            format_func=lambda value: {
                "demo": "Demo · simulated",
                "auto": "Automatic · live if available",
                "live": "Live only · TomTom",
            }[value],
            help=(
                "Live-only is recommended when you want actual "
                "traffic readings."
            ),
            key="cf_data_mode",
        )

        if "traffic_records" not in st.session_state:
            st.session_state["traffic_records"] = []
            st.session_state["traffic_warnings"] = []
            st.session_state["loaded_mode"] = None
            st.session_state["traffic_error"] = None
            st.session_state["last_saved_count"] = 0
            st.session_state["last_fetch_epoch"] = 0.0

        fetch_clicked = st.button(
            "↻ Fetch traffic now",
            type="primary",
            width="stretch",
        )

        if (
            fetch_clicked
            or st.session_state.get("loaded_mode") != mode
        ):
            try:
                fetch_current(mode)
            except Exception as exc:
                st.session_state["traffic_error"] = str(exc)

        st.caption(
            "Dashboard refreshes every 30 seconds. Automatic "
            "refresh reads saved history; it does not call TomTom "
            "every 30 seconds."
        )

        st.divider()
        st.markdown("### Map & analytics")

        map_theme = st.selectbox(
            "Map style",
            ["Dark map", "OpenStreetMap"],
            index=0,
            key="cf_map_theme",
        )

        chart_window = st.selectbox(
            "Chart time window",
            [
                "Last 1 hour",
                "Last 6 hours",
                "Last 24 hours",
                "All available",
            ],
            index=2,
            key="cf_chart_window",
        )

        st.divider()
        st.markdown("### Data notes")
        st.caption(
            "Live observations are snapshots from six monitored "
            "points, not complete citywide coverage."
        )
        st.caption("Weather refreshes at most every 5 minutes.")

    # -------------------- HEADER --------------------

    now_local = pd.Timestamp.now(tz=IST)

    st.markdown(
        f"""
        <div class="cf-hero">
          <div class="cf-eyebrow">
            SMART CITY · LIVE OPERATIONS
          </div>
          <h1>
            CityFlow
            <span style="color:#35d5f2">AI</span>
            Traffic Command Center
          </h1>
          <p>
            Real-time road conditions, congestion analytics and
            historical traffic intelligence for Mysuru, Karnataka.
          </p>
          <span class="cf-pill">
            ● DASHBOARD AUTO-REFRESH · 30 SECONDS
          </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    header_a, header_b = st.columns([3, 1])

    with header_a:
        st.caption(
            "Dashboard view updated: "
            + now_local.strftime("%d %b %Y · %I:%M:%S %p IST")
        )

    with header_b:
        st.caption("Source: TomTom · Open-Meteo · SQLite")

    traffic_error = st.session_state.get("traffic_error")
    warnings = st.session_state.get("traffic_warnings", [])

    if traffic_error:
        st.error(
            f"Traffic data could not be loaded: {traffic_error}"
        )

    for warning in warnings:
        st.warning(warning)

    # -------------------- LOAD SAVED HISTORY --------------------

    history = get_history(
        limit=100000,
        include_simulated=True,
    )

    if not history.empty:
        history["observed_at_utc"] = pd.to_datetime(
            history["observed_at_utc"],
            utc=True,
            errors="coerce",
        )

        history = history.dropna(
            subset=["observed_at_utc"]
        ).copy()

        history["observed_at_local"] = (
            history["observed_at_utc"].dt.tz_convert(IST)
        )

    else:
        history["observed_at_local"] = pd.Series(
            dtype="datetime64[ns, Asia/Kolkata]"
        )

    latest_all = get_latest_by_location(history)

    if not latest_all.empty:
        latest_live = latest_all[
            latest_all["simulated"].fillna(0).astype(int) == 0
        ].copy()
    else:
        latest_live = latest_all.copy()

    records = st.session_state.get("traffic_records", [])
    latest_view = (
        latest_live if not latest_live.empty else latest_all
    )

    # -------------------- KPI CARDS --------------------

    total_saved_live = get_history_count(
        include_simulated=False
    )

    high_count = (
        int(
            (latest_live["congestion_status"] == "High").sum()
        )
        if not latest_live.empty
        else 0
    )

    avg_score = (
        latest_live["congestion_score"].dropna().mean()
        if not latest_live.empty
        else None
    )

    avg_speed = (
        latest_live["current_speed_kmh"].dropna().mean()
        if not latest_live.empty
        else None
    )

    k1, k2, k3, k4, k5 = st.columns(5)

    k1.metric(
        "Monitored points",
        f"{len(latest_view):,}",
        help="Latest stored reading per monitored location.",
    )

    k2.metric(
        "Saved live readings",
        f"{total_saved_live:,}",
    )

    k3.metric(
        "High congestion points",
        f"{high_count:,}",
    )

    k4.metric(
        "Average congestion",
        f"{avg_score:.1f}%"
        if pd.notna(avg_score)
        else "N/A",
    )

    k5.metric(
        "Average speed",
        f"{avg_speed:.1f} km/h"
        if pd.notna(avg_speed)
        else "N/A",
    )

    if not history.empty:
        most_recent = history["observed_at_local"].max()

        age_seconds = max(
            0,
            (
                pd.Timestamp.now(tz=IST) - most_recent
            ).total_seconds(),
        )

        if age_seconds <= 8 * 60:
            st.success(
                "Traffic history is updating · latest stored "
                f"observation {age_seconds / 60:.1f} minutes ago."
            )
        else:
            st.warning(
                "Latest stored observation is "
                f"{age_seconds / 60:.1f} minutes old. "
                "Check the collector terminal if unexpected."
            )

    elif not records:
        st.info(
            "No traffic observations found yet. Click "
            "**Fetch traffic now** or start the collector "
            "in another terminal."
        )

    tabs = st.tabs(
        [
            "🗺️ Live map",
            "📊 Analytics",
            "📋 Readings & export",
            "🌦️ Weather",
            "⚙️ Collector",
        ]
    )

    # =====================================================
    # TAB 1 — LIVE MAP
    # =====================================================

    with tabs[0]:
        left, right = st.columns([2.2, 1])

        with right:
            location_names = (
                sorted(
                    latest_view["location_name"]
                    .dropna()
                    .unique()
                    .tolist()
                )
                if not latest_view.empty
                else []
            )

            selected_location = st.selectbox(
                "Inspect location",
                ["All locations"] + location_names,
                key="cf_map_location",
            )

            status_filter = st.multiselect(
                "Show congestion levels",
                ["Low", "Moderate", "High", "Unknown"],
                default=["Low", "Moderate", "High", "Unknown"],
                key="cf_status_filter",
            )

            st.markdown("#### Congestion legend")
            st.markdown(
                "🟢 **Low** · 🟠 **Moderate** · "
                "🔴 **High** · ⚪ **Unknown**"
            )

            if (
                selected_location != "All locations"
                and not latest_view.empty
            ):
                selected_rows = latest_view[
                    latest_view["location_name"] == selected_location
                ]

                if not selected_rows.empty:
                    row = selected_rows.iloc[0]

                    st.markdown("---")
                    st.markdown(f"#### {selected_location}")

                    score = safe_float(
                        row.get("congestion_score")
                    )
                    speed = safe_float(
                        row.get("current_speed_kmh")
                    )
                    free = safe_float(
                        row.get("free_flow_speed_kmh")
                    )

                    st.metric(
                        "Congestion score",
                        f"{score:.1f}%"
                        if score is not None
                        else "N/A",
                    )

                    st.metric(
                        "Current speed",
                        f"{speed:.1f} km/h"
                        if speed is not None
                        else "N/A",
                    )

                    st.caption(
                        f"Free-flow speed: {free:.1f} km/h"
                        if free is not None
                        else "Free-flow speed unavailable"
                    )

                    st.caption(
                        f"Status: {row.get('congestion_status', 'Unknown')}"
                    )

                    st.caption(
                        f"Observed: {row.get('observed_at_local', 'Unknown')}"
                    )

        with left:
            map_data = latest_view.copy()

            if (
                not map_data.empty
                and selected_location != "All locations"
            ):
                map_data = map_data[
                    map_data["location_name"] == selected_location
                ]

            if not map_data.empty:
                map_data = map_data[
                    map_data["congestion_status"]
                    .fillna("Unknown")
                    .isin(status_filter)
                ]

            if map_theme == "OpenStreetMap":
                traffic_map = folium.Map(
                    location=[12.2958, 76.6394],
                    zoom_start=12,
                    tiles="OpenStreetMap",
                    control_scale=True,
                )

                for _, row in map_data.iterrows():
                    lat = safe_float(row.get("latitude"))
                    lon = safe_float(row.get("longitude"))

                    if lat is None or lon is None:
                        continue

                    color = {
                        "Low": "green",
                        "Moderate": "orange",
                        "High": "red",
                    }.get(
                        str(row.get("congestion_status")),
                        "gray",
                    )

                    folium.CircleMarker(
                        [lat, lon],
                        radius=10,
                        color=color,
                        fill=True,
                        fill_opacity=.85,
                        tooltip=str(row.get("location_name")),
                        popup=(
                            f"{row.get('location_name')} · "
                            f"{row.get('congestion_status')} · "
                            f"{row.get('congestion_score')}%"
                        ),
                    ).add_to(traffic_map)

            else:
                traffic_map = build_traffic_map(
                    map_data,
                    (
                        selected_location
                        if selected_location != "All locations"
                        else None
                    ),
                )

            st_folium(
                traffic_map,
                height=520,
                width=None,
                key="cityflow_dark_map",
                returned_objects=[],
            )

            st.caption(
                "Select a location in the panel to focus the map "
                "and inspect its latest stored reading."
            )

        if not latest_view.empty:
            cards = latest_view.sort_values(
                "congestion_score",
                ascending=False,
                na_position="last",
            )

            st.markdown("#### Location status board")

            card_cols = st.columns(
                min(3, max(1, len(cards)))
            )

            for idx, (_, row) in enumerate(cards.iterrows()):
                with card_cols[idx % len(card_cols)]:
                    status = str(
                        row.get("congestion_status", "Unknown")
                    )
                    score = safe_float(
                        row.get("congestion_score")
                    )
                    speed = safe_float(
                        row.get("current_speed_kmh")
                    )

                    st.markdown(
                        f"""
                        <div style="
                            background:#101827;
                            border:1px solid #263449;
                            border-radius:14px;
                            padding:14px;
                            margin-bottom:10px
                        ">
                          <div style="
                              font-size:.78rem;
                              color:#91a2bb
                          ">
                              {row.get('location_name', 'Unknown')}
                          </div>
                          <div style="
                              font-size:1.25rem;
                              font-weight:750;
                              color:{status_color(status)}
                          ">
                              {status.upper()}
                          </div>
                          <div style="
                              font-size:1.5rem;
                              font-weight:750;
                              color:#edf5ff
                          ">
                              {f'{score:.1f}%' if score is not None else 'N/A'}
                          </div>
                          <div style="
                              font-size:.82rem;
                              color:#91a2bb
                          ">
                              {f'{speed:.1f} km/h' if speed is not None else 'Speed N/A'}
                          </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

    # =====================================================
    # TAB 2 — ANALYTICS
    # =====================================================

    with tabs[1]:
        st.markdown("### Historical traffic analytics")

        st.caption(
            "Charts use stored observations and show timestamps "
            "in India Standard Time (IST)."
        )

        if history.empty:
            st.info(
                "Historical analytics will appear after the "
                "collector saves traffic readings."
            )

        else:
            now = pd.Timestamp.now(tz=IST)

            if chart_window == "Last 1 hour":
                start_ts = now - pd.Timedelta(hours=1)
            elif chart_window == "Last 6 hours":
                start_ts = now - pd.Timedelta(hours=6)
            elif chart_window == "Last 24 hours":
                start_ts = now - pd.Timedelta(hours=24)
            else:
                start_ts = history["observed_at_local"].min()

            filtered = history[
                (history["observed_at_local"] >= start_ts)
                & (
                    history["simulated"]
                    .fillna(0)
                    .astype(int)
                    == 0
                )
            ].copy()

            all_locations = sorted(
                filtered["location_name"]
                .dropna()
                .unique()
                .tolist()
            )

            chosen_locations = st.multiselect(
                "Locations to compare",
                all_locations,
                default=all_locations,
                key="cf_analytics_locations",
            )

            filtered = filtered[
                filtered["location_name"].isin(chosen_locations)
            ]

            a1, a2 = st.columns(2)
            a1.metric("Observations in window", f"{len(filtered):,}")
            a2.metric("Locations selected", f"{len(chosen_locations):,}")

            if filtered.empty:
                st.info(
                    "No live observations match the current "
                    "time and location filters."
                )

            else:
                speed_data = filtered.dropna(
                    subset=["current_speed_kmh"]
                ).copy()

                if not speed_data.empty:
                    fig_speed = px.line(
                        speed_data.sort_values("observed_at_local"),
                        x="observed_at_local",
                        y="current_speed_kmh",
                        color="location_name",
                        markers=True,
                        labels={
                            "observed_at_local": "Time (IST)",
                            "current_speed_kmh": "Speed (km/h)",
                            "location_name": "Location",
                        },
                    )

                    apply_plotly_dark(
                        fig_speed,
                        "Traffic speed over time",
                    )

                    st.plotly_chart(
                        fig_speed,
                        width="stretch",
                        theme=None,
                    )

                congestion = filtered.dropna(
                    subset=["congestion_score"]
                ).copy()

                congestion = congestion[
                    congestion["congestion_score"].between(0, 100)
                ]

                if not congestion.empty:
                    fig_congestion = px.line(
                        congestion.sort_values("observed_at_local"),
                        x="observed_at_local",
                        y="congestion_score",
                        color="location_name",
                        markers=True,
                        labels={
                            "observed_at_local": "Time (IST)",
                            "congestion_score": "Congestion (%)",
                            "location_name": "Location",
                        },
                    )

                    fig_congestion.update_yaxes(
                        range=[0, 100],
                        dtick=10,
                    )

                    apply_plotly_dark(
                        fig_congestion,
                        "Congestion score over time",
                    )

                    st.plotly_chart(
                        fig_congestion,
                        width="stretch",
                        theme=None,
                    )

                    summary = (
                        congestion.groupby(
                            "location_name",
                            as_index=False,
                        )
                        .agg(
                            observations=("id", "count"),
                            average_speed_kmh=(
                                "current_speed_kmh", "mean"
                            ),
                            average_congestion=(
                                "congestion_score", "mean"
                            ),
                            peak_congestion=(
                                "congestion_score", "max"
                            ),
                        )
                        .round(2)
                        .sort_values(
                            "average_congestion",
                            ascending=False,
                        )
                    )

                    fig_bar = px.bar(
                        summary,
                        x="location_name",
                        y="average_congestion",
                        color="average_congestion",
                        color_continuous_scale=[
                            "#22c55e",
                            "#f59e0b",
                            "#ef4444",
                        ],
                        range_color=[0, 100],
                        labels={
                            "location_name": "Location",
                            "average_congestion": (
                                "Average congestion (%)"
                            ),
                        },
                    )

                    fig_bar.update_yaxes(
                        range=[0, 100],
                        dtick=10,
                    )

                    apply_plotly_dark(
                        fig_bar,
                        "Average congestion by location",
                    )

                    st.plotly_chart(
                        fig_bar,
                        width="stretch",
                        theme=None,
                    )

                    st.dataframe(
                        summary.rename(
                            columns={
                                "location_name": "Location",
                                "observations": "Observations",
                                "average_speed_kmh": (
                                    "Average speed (km/h)"
                                ),
                                "average_congestion": (
                                    "Average congestion (%)"
                                ),
                                "peak_congestion": (
                                    "Peak congestion (%)"
                                ),
                            }
                        ),
                        width="stretch",
                        hide_index=True,
                    )

                else:
                    st.info(
                        "No valid congestion scores in the selected window."
                    )

    # =====================================================
    # TAB 3 — READINGS AND CSV EXPORT
    # =====================================================

    with tabs[2]:
        st.markdown("### Traffic readings")

        if latest_view.empty:
            st.info("No observations available yet.")

        else:
            table = latest_view.copy()

            table["observed_at_local"] = (
                table["observed_at_local"].dt.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            table = table.rename(
                columns={
                    "location_name": "Location",
                    "current_speed_kmh": "Speed (km/h)",
                    "free_flow_speed_kmh": (
                        "Free-flow speed (km/h)"
                    ),
                    "congestion_score": "Congestion (%)",
                    "congestion_status": "Status",
                    "source": "Source",
                    "simulated": "Simulated",
                    "observed_at_local": "Observed at (IST)",
                    "confidence": "Confidence",
                }
            )

            search = st.text_input(
                "Search locations",
                placeholder="Type a location name...",
                key="cf_readings_search",
            )

            if search:
                table = table[
                    table["Location"].astype(str).str.contains(
                        search,
                        case=False,
                        na=False,
                    )
                ]

            visible_cols = [
                "Location",
                "Status",
                "Speed (km/h)",
                "Free-flow speed (km/h)",
                "Congestion (%)",
                "Observed at (IST)",
                "Source",
                "Simulated",
                "Confidence",
            ]

            st.dataframe(
                table[
                    [c for c in visible_cols if c in table.columns]
                ],
                width="stretch",
                hide_index=True,
            )

            export_history = history.copy()

            export_history["observed_at_local"] = (
                export_history["observed_at_local"].dt.strftime(
                    "%Y-%m-%d %H:%M:%S %Z"
                )
            )

            st.download_button(
                "⬇️ Download complete history as CSV",
                data=export_history.to_csv(
                    index=False
                ).encode("utf-8"),
                file_name="cityflow_traffic_history.csv",
                mime="text/csv",
                width="stretch",
            )

            current_df = pd.DataFrame(records)

            if not current_df.empty:
                st.download_button(
                    "⬇️ Download latest API response as CSV",
                    data=current_df.to_csv(
                        index=False
                    ).encode("utf-8"),
                    file_name="cityflow_current_traffic.csv",
                    mime="text/csv",
                    width="stretch",
                )

    # =====================================================
    # TAB 4 — WEATHER
    # =====================================================

    with tabs[3]:
        st.markdown("### Current weather · Mysuru")

        try:
            weather = load_weather()

            w1, w2, w3, w4 = st.columns(4)

            temperature = weather.get("temperature_c")
            humidity = weather.get("humidity_percent")
            precipitation = weather.get("precipitation_mm")
            wind = weather.get("wind_speed_kmh")

            w1.metric(
                "Temperature",
                f"{temperature} °C"
                if temperature is not None
                else "N/A",
            )

            w2.metric(
                "Humidity",
                f"{humidity}%"
                if humidity is not None
                else "N/A",
            )

            w3.metric(
                "Precipitation",
                f"{precipitation} mm"
                if precipitation is not None
                else "N/A",
            )

            w4.metric(
                "Wind speed",
                f"{wind} km/h"
                if wind is not None
                else "N/A",
            )

            st.caption(
                f"Source: {weather.get('source', 'Open-Meteo')} · "
                f"Observed at: {weather.get('observed_at', 'Unknown')}"
            )

            st.info(
                "Weather is cached for up to five minutes to avoid "
                "unnecessary requests. The dashboard view itself "
                "refreshes every 30 seconds."
            )

        except Exception as exc:
            st.warning(
                f"Weather data is temporarily unavailable: {exc}"
            )

    # =====================================================
    # TAB 5 — COLLECTOR STATUS
    # =====================================================

    with tabs[4]:
        st.markdown("### Automatic collector status")

        st.caption(
            "Status is inferred from the log file; it does not "
            "independently verify the collector process."
        )

        collector = read_collector_status()

        if not collector["exists"]:
            st.warning(
                "Collector log not found. In another terminal run: "
                "python -u .\\collect_traffic.py"
            )

        else:
            s1, s2, s3 = st.columns(3)

            s1.metric(
                "Successful logged cycles",
                collector["success_count"],
            )

            s2.metric(
                "Logged errors",
                collector["error_count"],
            )

            age = collector["age_minutes"]

            s3.metric(
                "Last success age",
                f"{age:.1f} min"
                if age is not None
                else "Unknown",
            )

            if collector["last_success"]:
                st.success("Latest successful collection entry")
                st.code(collector["last_success"])

                if age is not None:
                    if age <= 8:
                        st.success("Collector log looks recent.")
                    else:
                        st.warning(
                            "Latest logged collection is over "
                            "8 minutes old. Check the collector terminal."
                        )

            else:
                st.warning("No successful collection entry found.")

            if collector["recent_errors"]:
                with st.expander("Recent collector errors"):
                    st.code(
                        "\n".join(collector["recent_errors"])
                    )

    st.divider()

    st.caption(
        "CityFlow AI · Mysuru traffic intelligence · Live data "
        "depends on upstream API availability. Six monitored "
        "points do not represent complete citywide coverage."
    )


render_dashboard()