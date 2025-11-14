# app.py

import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

REGION_CODE = "CA"          # Canada
NEWS_CATEGORY_ID = "25"     # YouTube "News & Politics"
MAX_RESULTS = 50            # per YouTube trending request

BANNER_URL = (
    "https://github.com/parkerprod953-dotcom/"
    "youtube-trending-dashboard/raw/"
    "fb65a040fe112f308c30f24e7693af1fade31d1f/assets/banner.jpg"
)

# ---------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------


def parse_iso8601_duration(s: str) -> int:
    """Convert ISO 8601 duration (e.g. PT1H2M5S) to seconds."""
    if not s:
        return 0
    pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    m = re.match(pattern, s)
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    secs = int(m.group(3) or 0)
    return hours * 3600 + mins * 60 + secs


def format_duration(seconds: int) -> str:
    """Return 1:23 or 1:02:33 style strings."""
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def format_views(n: int) -> str:
    """Pretty view counts: 1.2K, 3.4M, 1.2B."""
    n = int(n or 0)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def humanize_timedelta(td) -> str:
    """Return '3 hours ago', '2 days ago', etc."""
    total_sec = int(td.total_seconds())
    if total_sec < 60:
        return "just now"
    mins = total_sec // 60
    if mins < 60:
        return f"{mins} min ago" if mins == 1 else f"{mins} mins ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
    days = hours // 24
    if days < 7:
        return f"{days} day ago" if days == 1 else f"{days} days ago"
    weeks = days // 7
    return f"{weeks} week ago" if weeks == 1 else f"{weeks} weeks ago"


def description_snippet(text: str, max_chars: int = 260) -> str:
    if not text:
        return ""
    text = " ".join(text.strip().split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------------
# YouTube API calls
# ---------------------------------------------------------------------


def fetch_trending_news_ca(api_key: str, max_results: int = MAX_RESULTS):
    """Fetch News & Politics trending videos in Canada + basic channel info."""

    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,statistics,contentDetails",
        "chart": "mostPopular",
        "regionCode": REGION_CODE,
        "videoCategoryId": NEWS_CATEGORY_ID,
        "maxResults": max_results,
        "key": api_key,
    }

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    videos = []
    channel_ids = set()

    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        details = item.get("contentDetails", {})
        thumbs = snippet.get("thumbnails", {})

        thumb_obj = (
            thumbs.get("medium")
            or thumbs.get("high")
            or thumbs.get("standard")
            or thumbs.get("default")
            or {}
        )
        thumb_url = thumb_obj.get("url")
        thumb_w = thumb_obj.get("width")
        thumb_h = thumb_obj.get("height")

        # Vertical detection – tall thumbnails likely Shorts
        is_vertical = False
        if thumb_w and thumb_h:
            aspect = thumb_w / thumb_h
            # < 0.9 ≈ taller than wide, with a bit of tolerance
            is_vertical = aspect < 0.9

        duration_sec = parse_iso8601_duration(details.get("duration", "PT0S"))

        # Short detection: hashtag + duration + vertical
        text = (
            (snippet.get("title", "") + " " + snippet.get("description", ""))
            .lower()
            .replace("#shorts", " #shorts ")
        )
        marked_as_shorts = (
            "#shorts" in text or " #short " in text or "#short " in text or " #shorts " in text
        )

        is_short = (duration_sec <= 75) or marked_as_shorts or is_vertical

        video_id = item.get("id")
        channel_id = snippet.get("channelId")

        videos.append(
            {
                "video_id": video_id,
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "channel_id": channel_id,
                "channel_title": snippet.get("channelTitle", ""),
                "published_at": snippet.get("publishedAt"),
                "thumbnail_url": thumb_url,
                "duration_sec": duration_sec,
                "is_short": is_short,
                "is_vertical": is_vertical,
                "view_count": int(stats.get("viewCount", 0)),
            }
        )

        if channel_id:
            channel_ids.add(channel_id)

    channel_info = fetch_channel_info(api_key, list(channel_ids))

    return videos, channel_info


def fetch_channel_info(api_key: str, channel_ids):
    """Fetch channel country + small logo for outlet filter."""
    if not channel_ids:
        return {}

    info = {}
    url = "https://www.googleapis.com/youtube/v3/channels"

    # YouTube allows up to 50 IDs per request
    for i in range(0, len(channel_ids), 50):
        chunk = channel_ids[i : i + 50]
        params = {
            "part": "snippet",
            "id": ",".join(chunk),
            "key": api_key,
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            cid = item.get("id")
            snip = item.get("snippet", {})
            country = snip.get("country")
            logo = (snip.get("thumbnails", {}).get("default") or {}).get("url")
            info[cid] = {"country": country, "logo": logo}

    return info


# ---------------------------------------------------------------------
# Data loading / transformation
# ---------------------------------------------------------------------


@st.cache_data(ttl=60 * 60 * 4, show_spinner=True)
def load_data():
    """Fetch + prepare trending data. Cached for ~4 hours."""
    api_key = st.secrets["YOUTUBE_API_KEY"]
    fetched_at = datetime.now(timezone.utc)

    videos, channel_info = fetch_trending_news_ca(api_key, MAX_RESULTS)
    df = pd.DataFrame(videos)

    if df.empty:
        return df, channel_info, fetched_at

    df["url"] = "https://www.youtube.com/watch?v=" + df["video_id"]
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)

    now = fetched_at
    df["age_timedelta"] = now - df["published_at"]
    df["age_hours"] = df["age_timedelta"].dt.total_seconds() / 3600.0
    df["age_str"] = df["age_timedelta"].apply(humanize_timedelta)
    df["views_str"] = df["view_count"].apply(format_views)
    df["duration_str"] = df["duration_sec"].apply(format_duration)

    df["description_snippet"] = df["description"].apply(description_snippet)

    # Channel metadata
    df["channel_country"] = df["channel_id"].map(
        lambda cid: (channel_info.get(cid) or {}).get("country")
    )
    df["channel_logo"] = df["channel_id"].map(
        lambda cid: (channel_info.get(cid) or {}).get("logo")
    )

    def origin_label(code):
        if code == "CA":
            return "CA outlet"
        if not code:
            return "Origin unknown"
        return "Non-Canadian outlet"

    df["origin_label"] = df["channel_country"].map(origin_label)

    # "Regular" = non-Short
    df["is_regular"] = ~df["is_short"]

    return df, channel_info, fetched_at


def filter_by_outlet(df, outlet_filter: str):
    """Apply All / CA-only / Global (non-CA) filter."""
    if outlet_filter == "Canadian outlets only":
        return df[df["channel_country"] == "CA"]
    if outlet_filter == "Global (non-Canadian) outlets":
        return df[df["channel_country"].notna() & (df["channel_country"] != "CA")]
    return df


# ---------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------


def badges_for_row(row, rank=None):
    badges = []
    if rank is not None and rank <= 3:
        badges.append("⭐")
    if row["view_count"] >= 1_000_000:
        badges.append("🔥")
    return " ".join(badges)


def render_video_list(df, label_metric=None, label_metric_title=None):
    """Render list of videos as sleek cards."""
    if df.empty:
        st.info("No videos to show for this filter.")
        return

    for idx, row in df.reset_index(drop=True).iterrows():
        rank = idx + 1

        cols = st.columns([1.1, 3])

        with cols[0]:
            st.image(
                row["thumbnail_url"],
                use_column_width=True,
                caption=None,
            )

        with cols[1]:
            badges = badges_for_row(row, rank)
            title_line = f"#{rank}  {row['title']}"
            if badges:
                title_line += f"  {badges}"

            st.markdown(
                f"<div style='font-size:1.05rem; font-weight:600; margin-bottom:0.25rem;'>"
                f"<a href='{row['url']}' target='_blank' style='text-decoration:none; color:#0f6ddf;'>"
                f"{title_line}</a></div>",
                unsafe_allow_html=True,
            )

            # Main line: views · duration · age
            st.markdown(
                f"<div style='font-size:0.95rem; color:#374151; margin-bottom:0.15rem;'>"
                f"👁️ {row['views_str']} views · ⏱️ {row['duration_str']} · ⌛ {row['age_str']}"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Channel + origin
            st.markdown(
                f"<div style='font-size:0.9rem; color:#4b5563; margin-bottom:0.25rem;'>"
                f"<strong>{row['channel_title']}</strong> · {row['origin_label']}"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Extra metric (e.g. views/hr for Hot tab)
            if label_metric and label_metric_title and label_metric in row:
                st.markdown(
                    f"<div style='font-size:0.85rem; color:#6b7280; margin-bottom:0.25rem;'>"
                    f"{label_metric_title}: <strong>{row[label_metric]}</strong>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            if row.get("description_snippet"):
                st.markdown(
                    f"<div style='font-size:0.9rem; color:#111827; margin-bottom:0.35rem;'>"
                    f"{row['description_snippet']}"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            # Copy details helper
            copy_text = (
                f"Title: {row['title']}\n"
                f"Channel: {row['channel_title']}\n"
                f"Views: {row['views_str']}\n"
                f"Duration: {row['duration_str']}\n"
                f"Link: {row['url']}\n\n"
                f"Description:\n{row['description'] or ''}"
            )
            with st.expander("Copy title + details"):
                st.code(copy_text, language="text")


# ---------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------


def main():
    st.set_page_config(
        page_title="CA YouTube News & Politics – Trending Dashboard",
        layout="wide",
        page_icon="📺",
    )

    # Top bar: refresh + note
    top_l, top_r = st.columns([1, 4])
    with top_l:
        if st.button("🔄 Refresh data now"):
            load_data.clear()
            st.experimental_rerun()
    with top_r:
        st.markdown(
            "<div style='margin-top:0.3rem; font-size:0.9rem; color:#6b7280;'>"
            "Data auto-refreshes roughly every 4 hours, or use the button to refresh manually."
            "</div>",
            unsafe_allow_html=True,
        )

    # Load data
    df, channel_info, fetched_at = load_data()

    # --- Header / banner ---
    st.markdown(
        f"""
<div style="
    position:relative;
    margin-top:0.75rem;
    margin-bottom:1.25rem;
    padding:3.2rem 4rem;
    border-radius:18px;
    background-image:
        linear-gradient(to bottom, rgba(0,0,0,0.82), rgba(0,0,0,0.90)),
        url('{BANNER_URL}');
    background-size:cover;
    background-position:center;
    color:#f9fafb;
">
  <h1 style="margin:0 0 .5rem 0;
             font-size:2.5rem;
             font-weight:650;
             letter-spacing:.04em;
             text-shadow:0 16px 40px rgba(0,0,0,0.8);">
    YouTube News &amp; Politics – Trending Dashboard
  </h1>
  <p style="margin:0 0 .25rem 0; font-size:.98rem; opacity:.9;">
    Showing trending <strong>News &amp; Politics</strong> videos on YouTube in Canada
    (region code CA).
  </p>
  <p style="margin:0 0 .3rem 0; font-size:.9rem; opacity:.8;">
    View counts shown are <strong>global</strong>. The YouTube Data API does not expose
    Canada-only viewership, so rankings are based on YouTube’s CA trending chart.
  </p>
  <p style="margin:0; font-size:.86rem; opacity:.82;">
    The <strong>🔥 Hot (last 8 hours)</strong> section looks only at videos uploaded in the last
    8 hours and ranks them by <strong>views per hour since upload</strong>
    (current view count ÷ hours online). It’s a proxy for fastest-rising stories.
  </p>
</div>
""",
        unsafe_allow_html=True,
    )

    # Last updated pill (ET)
    et_tz = ZoneInfo("America/Toronto")
    fetched_et = fetched_at.astimezone(et_tz)
    last_updated_str = fetched_et.strftime("%b %d, %Y • %I:%M %p ET")

    st.markdown(
        f"""
<div style="display:inline-flex;
            align-items:center;
            padding:.35rem .8rem;
            border-radius:999px;
            background-color:#eff6ff;
            color:#1d4ed8;
            font-size:.9rem;
            font-weight:500;
            margin-bottom:.85rem;">
  <span style="margin-right:.35rem;">⏱️ Last updated:</span>
  <span>{last_updated_str}</span>
</div>
""",
        unsafe_allow_html=True,
    )

    # Outlet filter
    st.markdown("**Outlet filter**")
    outlet_filter = st.radio(
        "Outlet filter",
        ["All outlets", "Canadian outlets only", "Global (non-Canadian) outlets"],
        horizontal=True,
        label_visibility="collapsed",
    )

    st.markdown(
        "🔍 **Legend:** ⭐ Top-3 within this list &nbsp;&nbsp;·&nbsp;&nbsp; "
        "🔥 1M+ total views"
    )

    # If no data, bail out
    if df.empty:
        st.warning("No trending News & Politics videos found right now.")
        return

    # Apply outlet filter on demand in each section
    df_regular_base = df[df["is_regular"]]
    df_shorts_base = df[df["is_short"]]

    # Tabs
    tab_reg, tab_shorts, tab_24h, tab_hot, tab_raw = st.tabs(
        ["Regular videos", "Shorts", "Last 24 hours", "🔥 Hot (last 8 hours)", "Raw table"]
    )

    # ------------------ Regular videos ------------------
    with tab_reg:
        df_reg = filter_by_outlet(df_regular_base, outlet_filter)
        df_reg = df_reg.sort_values("view_count", ascending=False)

        st.subheader("Top trending regular News & Politics videos in Canada")
        st.caption(
            "These are News & Politics videos in the CA trending chart that look like regular "
            "16:9 videos (not Shorts). Ranked by current global view count."
        )

        render_video_list(df_reg)

    # ------------------ Shorts ------------------
    with tab_shorts:
        df_sh = filter_by_outlet(df_shorts_base, outlet_filter)
        df_sh = df_sh.sort_values("view_count", ascending=False)

        st.subheader("Top trending News & Politics Shorts in Canada")
        st.caption(
            "Videos detected as vertical / hashtagged #shorts / under ~75 seconds, ranked by "
            "current global view count in the CA trending chart."
        )

        render_video_list(df_sh)

    # ------------------ Last 24 hours ------------------
    with tab_24h:
        df_24 = df[df["age_hours"] <= 24]
        df_24 = filter_by_outlet(df_24, outlet_filter)
        df_24 = df_24.sort_values("view_count", ascending=False)

        st.subheader("Top News & Politics videos posted in the last 24 hours")
        st.caption(
            "News & Politics videos in the CA trending chart that were uploaded within the last "
            "24 hours, ranked by current global view count."
        )

        render_video_list(df_24)

    # ------------------ Hot last 8 hours ------------------
    with tab_hot:
        df_hot = df[(df["age_hours"] <= 8) & df["is_regular"]]
        df_hot = filter_by_outlet(df_hot, outlet_filter).copy()

        # views/hour = current views / hours online (with a tiny lower bound)
        df_hot["views_per_hour"] = df_hot["view_count"] / df_hot["age_hours"].clip(lower=0.25)
        df_hot["views_per_hour_str"] = df_hot["views_per_hour"].apply(
            lambda v: f"{v:,.0f} views/hr"
        )
        df_hot = df_hot.sort_values("views_per_hour", ascending=False)

        st.subheader("🔥 Hottest News & Politics uploads – last 8 hours")
        st.caption(
            "Regular (non-Short) News & Politics uploads in the CA trending chart that are less "
            "than 8 hours old, ranked by **views per hour since upload**. "
            "Higher views/hour ≈ faster-rising story."
        )

        render_video_list(
            df_hot,
            label_metric="views_per_hour_str",
            label_metric_title="Views per hour",
        )

    # ------------------ Raw table ------------------
    with tab_raw:
        st.subheader("Raw data table")
        st.caption(
            "Full snapshot of the current CA News & Politics trending feed with the derived "
            "fields used above."
        )
        st.dataframe(
            df[
                [
                    "title",
                    "channel_title",
                    "channel_country",
                    "origin_label",
                    "view_count",
                    "views_str",
                    "duration_str",
                    "age_str",
                    "is_short",
                    "is_vertical",
                    "url",
                ]
            ],
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
