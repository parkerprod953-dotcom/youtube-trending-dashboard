import html
import re
import textwrap
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytz
import requests
import streamlit as st

# -----------------------------
# Basic config
# -----------------------------

API_KEY = st.secrets["YOUTUBE_API_KEY"]
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
REGION_CODE = "CA"
CATEGORY_NEWS_POLITICS = "25"

BANNER_URL = (
    "https://github.com/parkerprod953-dotcom/youtube-trending-dashboard/"
    "raw/fb65a040fe112f308c30f24e7693af1fade31d1f/assets/banner.jpg"
)

# -----------------------------
# Helpers
# -----------------------------

def yt_get(endpoint: str, params: dict) -> dict:
    params = {**params, "key": API_KEY}
    r = requests.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def parse_iso8601_duration(d):
    m = re.search(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    h, m_, s = m.groups(default="0")
    return int(h) * 3600 + int(m_) * 60 + int(s)


def format_duration(sec):
    if not sec:
        return "–"
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def format_views(v):
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.1f}K"
    return f"{v:,}"


def format_age(dt):
    delta = datetime.now(timezone.utc) - dt
    if delta.days >= 1:
        return f"{delta.days} days ago"
    hrs = delta.seconds // 3600
    if hrs:
        return f"{hrs} hours ago"
    mins = delta.seconds // 60
    return f"{mins} mins ago"


def truncate_description(t, max_chars=200):
    if not t:
        return ""
    return t if len(t) <= max_chars else t[: max_chars].rsplit(" ", 1)[0] + "…"


# -----------------------------
# Data fetch
# -----------------------------

@st.cache_data(ttl=60 * 60 * 4)
def fetch_trending():
    data = yt_get(
        "videos",
        {
            "part": "snippet,statistics,contentDetails",
            "chart": "mostPopular",
            "regionCode": REGION_CODE,
            "videoCategoryId": CATEGORY_NEWS_POLITICS,
            "maxResults": 50,
        },
    )

    rows, channel_ids = [], set()

    for i in data["items"]:
        s, c, stt = i["snippet"], i["contentDetails"], i["statistics"]
        pub = datetime.fromisoformat(s["publishedAt"].replace("Z", "+00:00"))
        dur = parse_iso8601_duration(c.get("duration"))
        is_short = dur <= 75 or "#short" in (s["title"] + s.get("description", "")).lower()

        rows.append(
            {
                "video_id": i["id"],
                "title": s["title"],
                "description": s.get("description", ""),
                "channel_id": s["channelId"],
                "channel": s["channelTitle"],
                "published_at": pub,
                "duration": dur,
                "views": int(stt.get("viewCount", 0)),
                "thumb": s["thumbnails"]["medium"]["url"],
                "url": f"https://www.youtube.com/watch?v={i['id']}",
                "is_short": is_short,
            }
        )
        channel_ids.add(s["channelId"])

    ch_map = {}
    for i in range(0, len(channel_ids), 50):
        ids = ",".join(list(channel_ids)[i : i + 50])
        for c in yt_get("channels", {"part": "snippet", "id": ids}).get("items", []):
            ch_map[c["id"]] = c["snippet"].get("country")

    df = pd.DataFrame(rows)
    df["country"] = df["channel_id"].map(ch_map)
    df["origin"] = df["country"].apply(
        lambda c: "Canadian outlet" if c == "CA" else "Non-Canadian outlet"
    )
    df["age_hours"] = (datetime.now(timezone.utc) - df["published_at"]).dt.total_seconds() / 3600
    df["vph"] = df["views"] / df["age_hours"].clip(lower=0.1)

    return df, datetime.now(timezone.utc)


# -----------------------------
# Video list renderer (UPDATED)
# -----------------------------

def render_video_list(df, section_key):
    for i, row in df.reset_index(drop=True).iterrows():
        rank = i + 1
        views_str = format_views(row["views"])
        age = format_age(row["published_at"])
        short_desc = truncate_description(row["description"], 200)

        st.markdown(
            f"""
<div class="video-card">
  <div style="display:flex;gap:18px;">
    <a href="{row['url']}" target="_blank">
      <img src="{row['thumb']}" style="border-radius:12px;width:260px;">
    </a>
    <div>
      <div style="opacity:.7;font-size:13px;">#{rank}</div>
      <a href="{row['url']}" target="_blank"
         style="font-size:17px;font-weight:600;color:#e5f0ff;text-decoration:none;">
        {html.escape(row['title'])}
      </a>
      <div style="margin-top:4px;font-size:13px;">
        👁 {views_str} · ⏱ {format_duration(row['duration'])} · 🕒 {age}
      </div>
      <div style="opacity:.8;font-size:13px;">
        {row['channel']} · {row['origin']}
      </div>
      <div style="margin-top:6px;font-size:13px;">
        {html.escape(short_desc)}
      </div>
    </div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

        with st.expander("Details", expanded=False):
            outlet_label = st.session_state.get("outlet_choice_ui", "All outlets")

            copy_block = f"""\
{views_str} views - {row['channel']} - Posted {age} - Trending #{rank} - {outlet_label}

Title:
{row['title']}

Description:
{short_desc}
"""

            st.code(copy_block, language=None)


# -----------------------------
# Main app
# -----------------------------

def main():
    st.set_page_config("YouTube News Dashboard", layout="wide")

    df, fetched = fetch_trending()

    st.session_state.setdefault("outlet_choice_ui", "All outlets")

    outlet = st.radio(
        "Outlet filter",
        ["All outlets", "Canadian outlets only", "Global (non-Canadian) outlets"],
        horizontal=True,
    )
    st.session_state["outlet_choice_ui"] = outlet

    if outlet == "Canadian outlets only":
        df = df[df["country"] == "CA"]
    elif outlet == "Global (non-Canadian) outlets":
        df = df[df["country"] != "CA"]

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Regular videos", "Shorts", "Last 24 hours", "🔥 Hot (8h)"]
    )

    with tab1:
        render_video_list(df[~df["is_short"]].sort_values("views", ascending=False).head(15), "reg")

    with tab2:
        render_video_list(df[df["is_short"]].sort_values("views", ascending=False).head(15), "short")

    with tab3:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        render_video_list(df[df["published_at"] >= cutoff].sort_values("views", ascending=False), "24h")

    with tab4:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=8)
        render_video_list(df[df["published_at"] >= cutoff].sort_values("vph", ascending=False), "hot")


if __name__ == "__main__":
    main()
