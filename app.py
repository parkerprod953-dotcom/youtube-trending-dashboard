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
REGION_CODE = "CA"              # Canada
CATEGORY_NEWS_POLITICS = "25"   # News & Politics

# Banner image (your uploaded asset)
BANNER_URL = (
    "https://github.com/parkerprod953-dotcom/youtube-trending-dashboard/"
    "raw/fb65a040fe112f308c30f24e7693af1fade31d1f/assets/banner.jpg"
)

# -----------------------------
# Utility helpers
# -----------------------------


def yt_get(endpoint: str, params: dict) -> dict:
    params = {**params, "key": API_KEY}
    resp = requests.get(
        f"{YOUTUBE_API_BASE}/{endpoint}", params=params, timeout=20
    )
    resp.raise_for_status()
    return resp.json()


def parse_iso8601_duration(duration_str: str) -> int:
    """Minimal ISO-8601 parser for PT#H#M#S -> seconds."""
    if not duration_str:
        return 0

    pattern = re.compile(
        r"P"
        r"(?:(?P<days>\d+)D)?"
        r"(?:T"
        r"(?:(?P<hours>\d+)H)?"
        r"(?:(?P<minutes>\d+)M)?"
        r"(?:(?P<seconds>\d+)S)?"
        r")?"
    )
    m = pattern.fullmatch(duration_str)
    if not m:
        return 0

    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    total = (((days * 24 + hours) * 60) + minutes) * 60 + seconds
    return total


def format_duration(seconds: int) -> str:
    if not seconds:
        return "–"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def format_views(views: int) -> str:
    if views is None:
        return "–"
    v = int(views)
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.1f}K"
    return f"{v:,}"


def format_age(published_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = now - published_at
    days = delta.days
    seconds = delta.seconds
    if days > 7:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''} ago"
    hours = seconds // 3600
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    minutes = (seconds % 3600) // 60
    if minutes >= 1:
        return f"{minutes} min ago"
    return "just now"


def truncate_description(text: str, max_chars: int = 200) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    # Try to cut on a word boundary
    cut = text[: max_chars + 1]
    cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


# -----------------------------
# Fetch & prepare data
# -----------------------------


@st.cache_data(ttl=60 * 60 * 4, show_spinner=True)
def fetch_trending_videos():
    """Fetch trending News & Politics videos in CA and enrich them."""
    params = {
        "part": "snippet,statistics,contentDetails",
        "chart": "mostPopular",
        "regionCode": REGION_CODE,
        "videoCategoryId": CATEGORY_NEWS_POLITICS,
        "maxResults": 50,
    }
    data = yt_get("videos", params)

    videos = []
    channel_ids = set()

    for item in data.get("items", []):
        vid = item["id"]
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        title = snippet.get("title", "")
        desc = snippet.get("description", "") or ""
        channel_id = snippet.get("channelId", "")
        channel_title = snippet.get("channelTitle", "")
        published_at_str = snippet.get("publishedAt")
        published_at = (
            datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
            if published_at_str
            else datetime.now(timezone.utc)
        )

        duration_str = content.get("duration", "")
        duration_sec = parse_iso8601_duration(duration_str)

        try:
            view_count = int(stats.get("viewCount", 0))
        except Exception:
            view_count = 0

        thumbs = snippet.get("thumbnails", {})
        thumb_obj = (
            thumbs.get("medium")
            or thumbs.get("high")
            or thumbs.get("default")
            or {}
        )
        thumb_url = thumb_obj.get("url")

        # basic shorts detection: hashtag + very short duration
        text = (title + " " + desc).lower()
        has_short_tag = "#short" in text or "#shorts" in text
        is_short = has_short_tag or duration_sec <= 75

        url = f"https://www.youtube.com/watch?v={vid}"

        videos.append(
            {
                "video_id": vid,
                "title": title,
                "description": desc,
                "channel_id": channel_id,
                "channel_title": channel_title,
                "published_at": published_at,
                "duration_sec": duration_sec,
                "view_count": view_count,
                "url": url,
                "thumbnail_url": thumb_url,
                "is_short": is_short,
            }
        )

        if channel_id:
            channel_ids.add(channel_id)

    # Fetch channel country info
    channel_info = {}
    if channel_ids:
        chunks = []
        ids = list(channel_ids)
        for i in range(0, len(ids), 50):
            chunks.append(",".join(ids[i : i + 50]))

        for chunk_ids in chunks:
            ch_data = yt_get(
                "channels",
                {"part": "snippet", "id": chunk_ids},
            )
            for ch in ch_data.get("items", []):
                cid = ch["id"]
                country = ch.get("snippet", {}).get("country")
                channel_info[cid] = {"country": country}

    now_utc = datetime.now(timezone.utc)
    df = pd.DataFrame(videos)
    if df.empty:
        return df, channel_info, now_utc

    df["channel_country"] = df["channel_id"].map(
        lambda cid: (channel_info.get(cid) or {}).get("country")
    )
    df["origin_label"] = df["channel_country"].apply(
        lambda c: "Canadian outlet" if c == "CA" else "Non-Canadian outlet"
    )

    df["age_hours"] = (now_utc - df["published_at"]).dt.total_seconds() / 3600.0
    df["views_per_hour"] = df.apply(
        lambda r: r["view_count"] / max(r["age_hours"], 1 / 60), axis=1
    )

    return df, channel_info, now_utc


# -----------------------------
# UI helpers
# -----------------------------


def render_css():
    st.markdown(
        """
<style>
/* Card hover + layout */
.video-card {
  border-radius: 14px;
  padding: 16px 18px;
  margin-bottom: 18px;
  background-color: #14161c;
  transition: background-color 0.18s ease, transform 0.18s ease, box-shadow 0.18s ease;
}
.video-card:hover {
  background-color: #1c1f27;
  box-shadow: 0 12px 24px rgba(0,0,0,0.45);
  transform: translateY(-2px);
}
.video-thumb img {
  border-radius: 10px;
  display: block;
}
.video-meta {
  font-size: 13px;
  color: #c9d3f5;
}
.video-desc {
  font-size: 13px;
  color: #e0e0e0;
}
/* Banner */
.hero {
  position: relative;
  overflow: hidden;
  border-radius: 16px;
  margin-bottom: 18px;
}
.hero-bg {
  width: 100%;
  height: 260px;
  object-fit: cover;
  filter: brightness(0.35) blur(1px);
  transform: scale(1.02);
}
.hero-overlay {
  position: absolute;
  inset: 0;
  padding: 32px 40px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.hero-title {
  font-size: 36px;
  font-weight: 650;
  letter-spacing: .03em;
}
.hero-sub {
  font-size: 14px;
  max-width: 900px;
  line-height: 1.5;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def render_banner(fetched_at_utc: datetime):
    eastern = pytz.timezone("US/Eastern")
    fetched_et = fetched_at_utc.astimezone(eastern)
    fetched_str = fetched_et.strftime("%b %d, %Y • %I:%M %p ET")

    st.markdown(
        textwrap.dedent(
            f"""
<div class="hero">
  <img src="{BANNER_URL}" class="hero-bg">
  <div class="hero-overlay">
    <div class="hero-title">YouTube News &amp; Politics – Trending Dashboard</div>
    <div class="hero-sub" style="margin-top:10px;">
      Showing trending <b>News &amp; Politics</b> videos on YouTube in Canada (region code <b>CA</b>).
      View counts shown are <b>global</b>. The YouTube Data API does not expose Canada-only
      viewership, so rankings are based on YouTube’s CA trending chart.
      <br><br>
      The <span style="color:#ffb347;">🔥 Hot (last 8 hours)</span> section looks only at videos
      uploaded in the last 8 hours and ranks them by <b>views per hour since upload</b>
      (current view count ÷ hours online). It’s a proxy for fastest-rising stories.
    </div>
    <div style="margin-top:18px;font-size:13px;color:#e9eefc;">
      <span style="padding:6px 12px;border-radius:999px;background:rgba(0,0,0,0.45);">
        ⏱ Last updated: <b>{fetched_str}</b>
      </span>
    </div>
  </div>
</div>
            """
        ),
        unsafe_allow_html=True,
    )


def filter_by_outlet(df: pd.DataFrame, outlet_filter: str) -> pd.DataFrame:
    if outlet_filter == "Canadian only":
        return df[df["channel_country"] == "CA"]
    if outlet_filter == "Global":
        return df[df["channel_country"] != "CA"]
    return df


def render_video_list(df: pd.DataFrame, section_key: str):
    """Render a list of videos with hover-highlight cards."""
    if df.empty:
        st.write("No videos found for this section right now.")
        return

    for idx, row in df.reset_index(drop=True).iterrows():
        rank = idx + 1
        title = row["title"]
        url = row["url"]
        thumb = row["thumbnail_url"]
        views = int(row["view_count"])
        duration_str = format_duration(int(row["duration_sec"]))
        age_str = format_age(row["published_at"])
        channel = row["channel_title"]
        origin = row["origin_label"]

        short_desc = truncate_description(row["description"], max_chars=200)

        star = " ⭐" if rank <= 3 else ""
        fire = " 🔥" if views >= 1_000_000 else ""
        badge = star + fire

        views_str = format_views(views)

        card_html = textwrap.dedent(
            f"""
<div class="video-card">
  <div style="display:flex;gap:18px;align-items:flex-start;">
    <div class="video-thumb" style="flex:0 0 260px;">
      <a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">
        <img src="{thumb}" alt="thumbnail">
      </a>
    </div>
    <div style="flex:1;min-width:0;">
      <div style="font-size:13px;color:#9ba4c9;margin-bottom:2px;">#{rank}</div>
      <a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer"
         style="font-size:17px;font-weight:600;color:#e5f0ff;text-decoration:none;">
        {html.escape(title)}{badge}
      </a>
      <div class="video-meta" style="margin-top:4px;">
        👁 {views_str} &nbsp; ⏱ {duration_str} &nbsp; 🕒 {age_str}
      </div>
      <div style="margin-top:3px;font-size:13px;color:#c4c9ea;">
        {html.escape(channel)} · {origin}
      </div>
      <div class="video-desc" style="margin-top:8px;">
        {html.escape(short_desc)}
      </div>
    </div>
  </div>
</div>
            """
        )
        st.markdown(card_html, unsafe_allow_html=True)

        # Copy button
        copy_text = textwrap.dedent(
            f"""\
            {title}
            Channel: {channel}
            Origin: {origin}
            Views: {views} ({views_str})
            Duration: {duration_str}
            Published: {row['published_at'].isoformat()}
            URL: {url}

            Description:
            {row['description']}
            """
        )

        if st.button(
            "Copy title + details",
            key=f"copy_{section_key}_{row['video_id']}",
        ):
            st.session_state["copied_details"] = copy_text
        st.write("")  # small gap

    if st.session_state.get("copied_details"):
        st.markdown("**Copy buffer** (select & copy):")
        st.text_area(
            "",
            st.session_state["copied_details"],
            height=160,
            key="copy_buffer_area",
        )


# -----------------------------
# Main app
# -----------------------------


def main():
    st.set_page_config(
        page_title="CA YouTube News Dashboard",
        layout="wide",
    )

    if "copied_details" not in st.session_state:
        st.session_state["copied_details"] = ""

    render_css()

    # Refresh button at very top
    top_cols = st.columns([1, 3])
    with top_cols[0]:
        if st.button("🔄 Refresh data now"):
            # cache will be re-evaluated next call
            st.cache_data.clear()
            st.rerun()
    with top_cols[1]:
        st.markdown(
            "Data auto-refreshes roughly every 4 hours, or use the button to refresh manually.",
            help="Backing off the YouTube API a bit.",
        )

    # Fetch trending data
    df, _, fetched_at_utc = fetch_trending_videos()

    render_banner(fetched_at_utc)

    # Outlet filter + legend
    st.markdown("**Outlet filter**")
    outlet_choice = st.radio(
        "",
        ["All outlets", "Canadian outlets only", "Global (non-Canadian) outlets"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if outlet_choice.startswith("All"):
        outlet_filter = "All"
    elif outlet_choice.startswith("Canadian"):
        outlet_filter = "Canadian only"
    else:
        outlet_filter = "Global"

    st.markdown(
        "🔎 **Legend:** ⭐ Top-3 within this list · 🔥 1M+ total views",
    )

    # Tabs
    tab_regular, tab_shorts, tab_24h, tab_hot, tab_raw = st.tabs(
        ["Regular videos", "Shorts", "Last 24 hours", "Hot (last 8 hours)", "Raw table"]
    )

    # Regular videos
    with tab_regular:
        st.markdown(
            "### Top trending regular News & Politics videos in Canada\n"
            "These are News & Politics videos in the CA trending chart that look like "
            "regular 16:9 videos (not Shorts). Ranked by current global view count."
        )
        dfr = df[~df["is_short"]].sort_values("view_count", ascending=False)
        dfr = filter_by_outlet(dfr, outlet_filter)
        render_video_list(dfr.head(15), section_key="regular")

    # Shorts
    with tab_shorts:
        st.markdown(
            "### Top trending News & Politics Shorts in Canada\n"
            "Likely YouTube Shorts (very short runtime or #shorts in title/description), "
            "ranked by current global view count."
        )
        dfs = df[df["is_short"]].sort_values("view_count", ascending=False)
        dfs = filter_by_outlet(dfs, outlet_filter)
        render_video_list(dfs.head(15), section_key="shorts")

    # Last 24 hours
    with tab_24h:
        st.markdown(
            "### Top News & Politics uploads from the last 24 hours\n"
            "Videos uploaded in the past 24 hours, ranked by current global view count."
        )
        cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        df24 = df[df["published_at"] >= cutoff_24h].copy()
        df24 = df24.sort_values("view_count", ascending=False)
        df24 = filter_by_outlet(df24, outlet_filter)
        render_video_list(df24.head(15), section_key="last24")

    # Hot last 8 hours
    with tab_hot:
        st.markdown(
            "### 🔥 Hot News & Politics videos (last 8 hours)\n"
            "Videos uploaded in the last 8 hours, ranked by **views per hour since upload**. "
            "This favours very fresh stories that are rising quickly."
        )
        cutoff_8h = datetime.now(timezone.utc) - timedelta(hours=8)
        df8 = df[df["published_at"] >= cutoff_8h].copy()
        df8 = filter_by_outlet(df8, outlet_filter)
        df8 = df8.sort_values("views_per_hour", ascending=False)
        render_video_list(df8.head(15), section_key="hot8")

    # Raw table
    with tab_raw:
        st.markdown("### Raw table")
        dfr = df.copy()
        dfr["published_at"] = dfr["published_at"].dt.tz_convert("US/Eastern")
        st.dataframe(
            dfr[
                [
                    "title",
                    "channel_title",
                    "origin_label",
                    "view_count",
                    "views_per_hour",
                    "duration_sec",
                    "published_at",
                    "is_short",
                    "url",
                ]
            ].rename(
                columns={
                    "origin_label": "origin",
                    "duration_sec": "duration_s",
                }
            ),
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
