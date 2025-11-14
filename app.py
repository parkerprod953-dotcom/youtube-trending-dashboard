import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytz
import requests
import streamlit as st
import html

# -----------------------------
# Config & helpers
# -----------------------------

API_KEY = st.secrets["YOUTUBE_API_KEY"]
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
REGION_CODE = "CA"          # Canada
CATEGORY_NEWS_POLITICS = "25"

BANNER_URL = (
    "https://github.com/parkerprod953-dotcom/youtube-trending-dashboard/"
    "raw/fb65a040fe112f308c30f24e7693af1fade31d1f/assets/banner.jpg"
)


def yt_get(endpoint, params):
    params = params.copy()
    params["key"] = API_KEY
    resp = requests.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def parse_iso8601_duration(duration_str: str) -> int:
    """Minimal ISO-8601 duration parser for YouTube strings like PT1H2M3S."""
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
    minutes = seconds // 60
    if minutes >= 1:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    return "Just now"


def short_description(desc: str, max_chars: int = 200) -> str:
    """Return HTML-escaped description truncated to max_chars with ellipsis."""
    if not desc:
        return ""
    desc = desc.strip()
    if len(desc) <= max_chars:
        return html.escape(desc)

    cutoff = desc.rfind(" ", 0, max_chars)
    if cutoff == -1:
        cutoff = max_chars
    trimmed = desc[:cutoff].rstrip()
    return html.escape(trimmed) + "…"


# -----------------------------
# Data fetching
# -----------------------------

@st.cache_data(ttl=60 * 60 * 3, show_spinner=True)
def fetch_trending_news() -> pd.DataFrame:
    # 1. trending videos for News & Politics in CA
    videos = []
    page_token = None

    while True:
        params = {
            "part": "snippet,contentDetails,statistics",
            "chart": "mostPopular",
            "regionCode": REGION_CODE,
            "videoCategoryId": CATEGORY_NEWS_POLITICS,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token

        data = yt_get("videos", params)

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})

            vid = item["id"]
            title = snippet.get("title", "")
            description = snippet.get("description", "")
            channel_title = snippet.get("channelTitle", "")
            channel_id = snippet.get("channelId")
            published_at_str = snippet.get("publishedAt")
            published_at = (
                datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
                if published_at_str
                else datetime.now(timezone.utc)
            )
            view_count = int(stats.get("viewCount", 0))
            duration_sec = parse_iso8601_duration(content.get("duration", "PT0S"))

            # Shorts detection: <=75s or tagged #shorts
            text = (title + " " + description).lower()
            is_short = "#short" in text or duration_sec <= 75

            thumbs = snippet.get("thumbnails", {}) or {}
            thumb = (
                thumbs.get("maxres")
                or thumbs.get("standard")
                or thumbs.get("high")
                or thumbs.get("medium")
                or thumbs.get("default")
                or {}
            )
            thumb_url = thumb.get("url")

            videos.append(
                {
                    "video_id": vid,
                    "title": title,
                    "description": description,
                    "channel_title": channel_title,
                    "channel_id": channel_id,
                    "published_at": published_at,
                    "view_count": view_count,
                    "duration_sec": duration_sec,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "thumbnail_url": thumb_url,
                    "is_short": is_short,
                }
            )

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    df = pd.DataFrame(videos)

    # fetch channel country (for Canadian/global outlet filter)
    ch_ids = df["channel_id"].dropna().unique().tolist()
    channel_countries = {}

    for i in range(0, len(ch_ids), 50):
        chunk = ch_ids[i : i + 50]
        ch_data = yt_get(
            "channels",
            {
                "part": "snippet",
                "id": ",".join(chunk),
                "maxResults": 50,
            },
        )
        for ch in ch_data.get("items", []):
            cid = ch["id"]
            country = ch.get("snippet", {}).get("country")
            channel_countries[cid] = country

    df["channel_country"] = df["channel_id"].map(channel_countries)
    df["is_canadian_outlet"] = df["channel_country"] == "CA"

    return df.sort_values("view_count", ascending=False).reset_index(drop=True)


# -----------------------------
# UI helpers
# -----------------------------

def apply_outlet_filter(df: pd.DataFrame, outlet_filter: str) -> pd.DataFrame:
    if outlet_filter == "ca":
        return df[df["is_canadian_outlet"]].copy()
    if outlet_filter == "global":
        return df[~df["is_canadian_outlet"]].copy()
    return df.copy()


def render_video_card(row, rank: int):
    thumb_url = row["thumbnail_url"]
    url = row["url"]
    title = row["title"]
    channel = row["channel_title"]
    desc = row["description"]
    views = row["view_count"]
    duration = row["duration_sec"]
    published_at = row["published_at"]
    is_short = row["is_short"]
    is_ca = bool(row.get("is_canadian_outlet"))

    views_str = format_views(views)
    duration_str = format_duration(duration)
    age_str = format_age(published_at)

    badge = ""
    if views >= 1_000_000:
        badge += " ⭐"
    if rank <= 3:
        badge += " 🔥"

    origin = "Canadian outlet" if is_ca else "Non-Canadian outlet"
    desc_html = short_description(desc, max_chars=200)

    card_html = f"""
    <div class="video-card">
      <div class="video-card-inner">
        <div class="video-thumb">
          <a href="{url}" target="_blank" rel="noopener noreferrer">
            <img src="{thumb_url}" alt="Thumbnail" />
          </a>
        </div>
        <div class="video-main">
          <div class="video-title-row">
            <span class="video-rank">#{rank}</span>
            <a href="{url}" target="_blank" rel="noopener noreferrer" class="video-title">
              {html.escape(title)}
            </a>
            <span class="video-badges">{badge}</span>
          </div>
          <div class="video-meta">
            <span>👁 {views_str} views</span>
            <span>⏱ {duration_str}</span>
            <span>🕒 {age_str}</span>
            {"<span>🎬 Short</span>" if is_short else ""}
          </div>
          <div class="video-channel">
            <span class="channel-name">{html.escape(channel)}</span>
            <span class="channel-origin">· {origin}</span>
          </div>
          <div class="video-desc">
            {desc_html}
          </div>
        </div>
      </div>
    </div>
    """

    st.markdown(card_html, unsafe_allow_html=True)

    # Copy button
    if st.button(
        "Copy title + details",
        key=f"copy_{row['video_id']}",
        help="Copies title, description, views & channel into a text block you can copy.",
    ):
        clip = (
            f"{title}\n\n"
            f"{desc}\n\n"
            f"Views: {views_str}\n"
            f"Duration: {duration_str}\n"
            f"Channel: {channel}\n"
            f"URL: {url}"
        )
        st.code(clip, language=None)


# -----------------------------
# Main app
# -----------------------------

st.set_page_config(
    page_title="CA YouTube News & Politics Trends",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# password gate (simple)
if "unlocked" not in st.session_state:
    st.session_state["unlocked"] = False

if not st.session_state["unlocked"]:
    st.title("YouTube News & Politics – Trending Dashboard")
    pwd = st.text_input("Enter dashboard password", type="password")
    if st.button("Unlock"):
        if pwd.strip() == st.secrets.get("DASHBOARD_PASSWORD", ""):
            st.session_state["unlocked"] = True
            st.rerun()   # <-- updated from st.experimental_rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

# Global CSS for sleek look + hover highlight
st.markdown(
    f"""
<style>
/* overall */
main .block-container {{
  padding-top: 1.5rem;
}}

/* banner */
.banner {{
  position: relative;
  width: 100%;
  margin-top: 0.5rem;
  margin-bottom: 1.75rem;
  border-radius: 18px;
  overflow: hidden;
  color: #fff;
}}
.banner-bg {{
  background-image: linear-gradient(90deg, rgba(0,0,0,0.92), rgba(0,0,0,0.35)),
                    url('{BANNER_URL}');
  background-size: cover;
  background-position: center;
  min-height: 180px;
  display:flex;
  align-items:center;
  padding: 26px 40px;
}}
.banner-title {{
  font-size: 32px;
  font-weight: 600;
  letter-spacing: 0.02em;
}}
.banner-sub {{
  margin-top: 6px;
  font-size: 15px;
  opacity: 0.88;
}}
.banner-note {{
  margin-top: 4px;
  font-size: 13px;
  opacity: 0.8;
}}
.banner-updated {{
  margin-top: 10px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  background: rgba(0,0,0,0.55);
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.18);
}}

/* video cards */
.video-card {{
  padding: 14px 16px;
  border-radius: 16px;
  border: 1px solid rgba(255,255,255,0.04);
  margin-bottom: 14px;
  transition:
    background-color 0.12s ease,
    box-shadow 0.12s ease,
    transform 0.08s ease;
  background-color: rgba(0,0,0,0.12);
}}
.video-card:hover {{
  background-color: rgba(255,255,255,0.05);
  box-shadow: 0 10px 28px rgba(0,0,0,0.6);
  transform: translateY(-1px);
}}

.video-card-inner {{
  display: flex;
  gap: 18px;
}}
.video-thumb img {{
  width: 235px;
  max-width: 235px;
  border-radius: 12px;
  object-fit: cover;
}}
.video-main {{
  flex: 1;
}}
.video-title-row {{
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}}
.video-rank {{
  font-size: 18px;
  font-weight: 600;
  color: #ffcc66;
}}
.video-title {{
  font-size: 18px;
  font-weight: 600;
  text-decoration: none;
}}
.video-title:hover {{
  text-decoration: underline;
}}
.video-badges {{
  font-size: 18px;
}}
.video-meta {{
  font-size: 13px;
  opacity: 0.85;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 4px;
}}
.video-channel {{
  font-size: 13px;
  margin-bottom: 4px;
}}
.video-channel .channel-name {{
  font-weight: 500;
}}
.video-desc {{
  font-size: 14px;
  line-height: 1.5;
  margin-top: 4px;
}}
.copy-btn > button {{
  margin-top: 4px;
}}

/* legend */
.legend-box {{
  margin-top: 8px;
  font-size: 13px;
  opacity: 0.9;
}}
.legend-pill {{
  display:inline-flex;
  align-items:center;
  gap:4px;
  padding:4px 9px;
  border-radius:999px;
  border:1px solid rgba(255,255,255,0.18);
  margin-right:6px;
}}

/* small tweaks for tabs */
[data-baseweb="tab-list"] {{
  gap: 1rem;
}}
</style>
""",
    unsafe_allow_html=True,
)

# -----------------------------
# Header banner
# -----------------------------

tz_et = pytz.timezone("America/Toronto")
now_et = datetime.now(tz_et)
now_str = now_et.strftime("%b %d, %Y • %I:%M %p ET").lstrip("0")

st.markdown(
    f"""
<div class="banner">
  <div class="banner-bg">
    <div>
      <div class="banner-title">
        YouTube News &amp; Politics – Trending Dashboard
      </div>
      <div class="banner-sub">
        Showing trending News &amp; Politics videos in Canada.
      </div>
      <div class="banner-note">
        View counts shown are global. The YouTube Data API does not expose per-country
        viewership; outlet filters are based on the channel's registered country.
      </div>
      <div class="banner-updated">
        <span>⏱</span><span>Last updated: {now_str}</span>
      </div>
    </div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

# -----------------------------
# Controls + data load
# -----------------------------

st.caption("Data auto-refreshes every ~3 hours. Use the button below to reload now.")
if st.button("🔄 Refresh data now"):
    fetch_trending_news.clear()
    st.rerun()

df_all = fetch_trending_news()

st.subheader("Outlet filter")

outlet_choice = st.radio(
    "Choose which channels to show",
    options=[
        ("All outlets", "all"),
        ("Canadian outlets only", "ca"),
        ("Global (non-Canadian) outlets", "global"),
    ],
    format_func=lambda x: x[0],
    horizontal=True,
    label_visibility="collapsed",
)
outlet_filter = outlet_choice[1]

df_filtered = apply_outlet_filter(df_all, outlet_filter)

# split regular vs shorts
df_regular = df_filtered[~df_filtered["is_short"]].reset_index(drop=True)
df_shorts = df_filtered[df_filtered["is_short"]].reset_index(drop=True)

# time-window subset (last 24h)
cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
df_last24 = df_regular[df_regular["published_at"] >= cutoff_24h].reset_index(drop=True)

st.markdown(
    """
<div class="legend-box">
  <span class="legend-pill">⭐ <span>High-view video (&gt;=1M views)</span></span>
  <span class="legend-pill">🔥 <span>Top-3 ranked video in this list</span></span>
</div>
""",
    unsafe_allow_html=True,
)

tabs = st.tabs(["Regular videos", "Shorts", "Last 24 hours", "Raw table"])

# -----------------------------
# Regular videos tab
# -----------------------------
with tabs[0]:
    st.markdown("### Top trending regular News & Politics videos in Canada")
    st.caption(
        "These are News & Politics videos in the CA trending chart that look like "
        "regular 16:9 videos (not Shorts). Ranked by current global view count."
    )

    if df_regular.empty:
        st.info("No regular videos found right now.")
    else:
        for idx, row in df_regular.iterrows():
            render_video_card(row, rank=idx + 1)

# -----------------------------
# Shorts tab
# -----------------------------
with tabs[1]:
    st.markdown("### Top trending Shorts (News & Politics)")
    st.caption(
        "These are videos detected as Shorts (≤75s or tagged #shorts) from the "
        "News & Politics category in Canada. Ranked by current global view count."
    )

    if df_shorts.empty:
        st.info("No Shorts found right now.")
    else:
        for idx, row in df_shorts.iterrows():
            render_video_card(row, rank=idx + 1)

# -----------------------------
# Last 24 hours tab
# -----------------------------
with tabs[2]:
    st.markdown("### Hot News & Politics videos from the last 24 hours")
    st.caption(
        "News & Politics videos uploaded in the last 24 hours that are currently "
        "ranking in the CA trending chart (regular 16:9, not Shorts). Ranked by "
        "current global view count."
    )

    if df_last24.empty:
        st.info("No qualifying videos found from the last 24 hours.")
    else:
        for idx, row in df_last24.iterrows():
            render_video_card(row, rank=idx + 1)

# -----------------------------
# Raw table tab
# -----------------------------
with tabs[3]:
    st.markdown("### Raw data table")
    st.caption("Full list of fetched trending videos, including Shorts.")

    show_cols = [
        "video_id",
        "title",
        "channel_title",
        "view_count",
        "duration_sec",
        "published_at",
        "is_short",
        "channel_country",
        "is_canadian_outlet",
    ]
    st.dataframe(df_filtered[show_cols])
