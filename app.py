import requests
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import pandas as pd
import streamlit as st


# ---------- CONFIG ----------

CATEGORY_ID_NEWS_POLITICS = "25"  # YouTube News & Politics
REGION_CODE = "CA"
MAX_RESULTS = 50                  # API max; we’ll later slice to top 15 per section
CACHE_TTL_SECONDS = 60 * 60 * 4   # 4 hours


# ---------- UTILS ----------

def parse_iso8601_duration(duration: str) -> int:
    """
    Parse ISO 8601 duration like 'PT5M12S' to total seconds.
    Very small/simple parser good enough for YouTube durations.
    """
    if not duration or not duration.startswith("PT"):
        return 0

    duration = duration.replace("PT", "")
    total = 0
    num = ""

    for ch in duration:
        if ch.isdigit():
            num += ch
        else:
            if not num:
                continue
            value = int(num)
            if ch == "H":
                total += value * 3600
            elif ch == "M":
                total += value * 60
            elif ch == "S":
                total += value
            num = ""

    return total


def format_views(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def format_time_ago(iso_time: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    except Exception:
        return "time unknown"

    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hours ago"
    days = hours // 24
    if days < 7:
        return f"{days} days ago"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks} weeks ago"
    months = days // 30
    return f"{months} months ago"


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "live/unknown"

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


# ---------- YOUTUBE API HELPERS ----------

def fetch_trending_news_ca(api_key: str,
                           max_results: int = MAX_RESULTS
                           ) -> Tuple[List[Dict], set]:
    """
    Fetch trending News & Politics videos in Canada.
    Returns (videos_list, channel_ids_set).
    """
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,contentDetails,statistics",
        "chart": "mostPopular",
        "regionCode": REGION_CODE,
        "videoCategoryId": CATEGORY_ID_NEWS_POLITICS,
        "maxResults": max_results,
        "key": api_key,
    }

    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    videos: List[Dict] = []
    channel_ids: set = set()

    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        details = item.get("contentDetails", {})
        thumbs = snippet.get("thumbnails", {})

        # pick best thumbnail
        thumb_obj = (
            thumbs.get("high", {})
            or thumbs.get("medium", {})
            or thumbs.get("default", {})
            or {}
        )
        thumb_url = thumb_obj.get("url")
        thumb_w = thumb_obj.get("width")
        thumb_h = thumb_obj.get("height")

        # vertical detection
        is_vertical = False
        if thumb_w and thumb_h:
            aspect = thumb_w / thumb_h  # < 1 => taller than wide
            is_vertical = aspect < 0.9

        # duration
        duration_secs = parse_iso8601_duration(details.get("duration", "PT0S"))

        # text-based shorts detection
        text = (snippet.get("title", "") + " " + snippet.get("description", "")).lower()
        marked_as_shorts = ("#shorts" in text) or ("#short " in text.replace("#shorts", ""))

        # final shorts classification
        is_short = (duration_secs <= 75) or marked_as_shorts or is_vertical

        channel_id = snippet.get("channelId")

        videos.append(
            {
                "video_id": item.get("id"),
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "channel_title": snippet.get("channelTitle"),
                "channel_id": channel_id,
                "published_at": snippet.get("publishedAt"),
                "url": f"https://www.youtube.com/watch?v={item.get('id')}",
                "view_count": int(stats.get("viewCount", 0)),
                "thumbnail_url": thumb_url,
                "duration_sec": duration_secs,
                "is_short": is_short,
                "is_vertical": is_vertical,
            }
        )

        if channel_id:
            channel_ids.add(channel_id)

    return videos, channel_ids


def fetch_channel_info(api_key: str, channel_ids: set) -> Dict[str, Dict]:
    """
    Fetch channel logos + country for given channel IDs.
    Returns dict[channel_id] -> {logo_url, country, title}
    """
    if not channel_ids:
        return {}

    ids_list = list(channel_ids)
    info: Dict[str, Dict] = {}

    # YouTube API allows up to 50 IDs per request
    for i in range(0, len(ids_list), 50):
        batch = ids_list[i:i + 50]
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {
            "part": "snippet",
            "id": ",".join(batch),
            "key": api_key,
        }
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        for ch in data.get("items", []):
            cid = ch.get("id")
            snip = ch.get("snippet", {})
            thumbs = snip.get("thumbnails", {})
            logo_obj = (
                thumbs.get("default", {})
                or thumbs.get("medium", {})
                or thumbs.get("high", {})
                or {}
            )
            info[cid] = {
                "title": snip.get("title"),
                "country": snip.get("country"),  # may be None
                "logo_url": logo_obj.get("url"),
            }

    return info


# ---------- CACHED DATA LOAD ----------

@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=True)
def load_data(api_key: str):
    videos, channel_ids = fetch_trending_news_ca(api_key, max_results=MAX_RESULTS)
    channel_info = fetch_channel_info(api_key, channel_ids)
    df = pd.DataFrame(videos)
    fetched_at = datetime.now(timezone.utc)
    return df, fetched_at, channel_info


# ---------- UI HELPERS ----------

def render_video_card(row: pd.Series, channel_info: Dict[str, Dict]):
    cid = row.get("channel_id")
    ch_meta = channel_info.get(cid, {}) if cid else {}
    logo_url = ch_meta.get("logo_url")
    country = ch_meta.get("country")

    is_canadian = country == "CA"
    if is_canadian:
        origin_label = "🇨🇦 Canadian outlet"
    elif country:
        origin_label = f"🌍 {country} outlet"
    else:
        origin_label = "🌍 Country unknown"

    views = int(row.get("view_count", 0))
    duration = int(row.get("duration_sec", 0))
    views_text = f"{format_views(views)} views"
    duration_text = format_duration(duration)
    age_text = format_time_ago(row.get("published_at", ""))

    # badge for big videos
    badge = ""
    if views >= 1_000_000:
        badge = "🔥"
    elif views >= 200_000:
        badge = "⭐"

    if badge:
        views_text = f"{badge} {views_text}"

    if logo_url:
        logo_html = (
            f'<img src="{logo_url}" '
            f'style="width:20px; height:20px; border-radius:50%; '
            f'margin-right:6px; vertical-align:middle;">'
        )
    else:
        logo_html = ""

    html = f"""
<div style="
    border:1px solid #e5e5e5;
    border-radius:12px;
    padding:12px;
    margin-bottom:12px;
    display:flex;
    gap:12px;
    background-color:#fafafa;">
  <div style="flex:0 0 180px;">
    <a href="{row['url']}" target="_blank">
      <img src="{row['thumbnail_url']}" style="width:100%; border-radius:8px;">
    </a>
  </div>
  <div style="flex:1;">
    <div style="font-size:15px; font-weight:600; margin-bottom:4px;">
      <a href="{row['url']}" target="_blank" style="text-decoration:none; color:#111;">
        {row['title']}
      </a>
    </div>
    <div style="font-size:13px; color:#555; margin-bottom:4px;">
      {views_text} · {duration_text} · {age_text}
    </div>
    <div style="font-size:13px; color:#555; margin-bottom:4px;">
      {logo_html}{row['channel_title']} · {origin_label}
    </div>
  </div>
</div>
"""
    st.markdown(html, unsafe_allow_html=True)


# ---------- MAIN APP ----------

def main():
    st.set_page_config(
        page_title="YouTube News & Politics – Canada Trending Dashboard",
        layout="wide",
    )

    st.title("🇨🇦 YouTube News & Politics – Trending Dashboard")

    # --- SIMPLE PASSWORD GATE ---
    expected_pwd = st.secrets.get("DASHBOARD_PASSWORD")
    if expected_pwd:
        if "authed" not in st.session_state:
            st.session_state.authed = False

        if not st.session_state.authed:
            pwd = st.text_input("Enter dashboard password", type="password")
            if st.button("Submit"):
                if pwd == expected_pwd:
                    st.session_state.authed = True
                    st.experimental_rerun()
                else:
                    st.error("Incorrect password.")
            # stop rendering the rest of the app until authed
            return
    # ----------------------------

    st.caption(
        "Top trending News & Politics videos in Canada. "
        "Split into regular videos and Shorts. Data auto-cached for 4 hours."
    )

    api_key = st.secrets.get("YOUTUBE_API_KEY")
    if not api_key:
        st.error(
            "No `YOUTUBE_API_KEY` found in Streamlit secrets.\n\n"
            "In Streamlit Cloud, go to **App → Settings → Secrets** and add:\n\n"
            "```text\nYOUTUBE_API_KEY = \"your_key_here\"\n```"
        )
        st.stop()

    with st.spinner("Fetching trending videos from YouTube…"):
        df, fetched_at, channel_info = load_data(api_key)

    if df.empty:
        st.warning("No videos returned from the YouTube API.")
        st.stop()

    # classify regular vs shorts
    regular_df = (
        df[~df["is_short"]]
        .sort_values("view_count", ascending=False)
        .head(15)
    )

    shorts_df = (
        df[df["is_short"]]
        .sort_values("view_count", ascending=False)
        .head(15)
    )

    col_left, col_right = st.columns(2)
    with col_left:
        st.metric("Regular videos (top 15)", len(regular_df))
    with col_right:
        st.metric("Shorts (top 15)", len(shorts_df))

    st.caption(f"Data last fetched at: **{fetched_at.strftime('%Y-%m-%d %H:%M:%S UTC')}**")

    tab_regular, tab_shorts, tab_table = st.tabs(
        ["🎬 Regular videos", "📱 Shorts", "📊 Raw table"]
    )

    with tab_regular:
        st.subheader("Top 15 regular News & Politics videos in Canada")
        for _, row in regular_df.iterrows():
            render_video_card(row, channel_info)

    with tab_shorts:
        st.subheader("Top 15 News & Politics Shorts in Canada")
        for _, row in shorts_df.iterrows():
            render_video_card(row, channel_info)

    with tab_table:
        st.subheader("Full raw data")
        st.dataframe(
            df.sort_values("view_count", ascending=False),
            use_container_width=True,
            height=600,
        )


if __name__ == "__main__":
    main()
