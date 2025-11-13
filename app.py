import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import re


# ---------- AUTH: SIMPLE PASSWORD GATE ----------

def check_password():
    """Simple password gate using Streamlit secrets."""

    def password_entered():
        """Check whether the password is correct."""
        if st.session_state["password"] == st.secrets["DASHBOARD_PASSWORD"]:
            st.session_state["password_correct"] = True
            # Don't store the actual password
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input(
            "Enter dashboard password",
            type="password",
            on_change=password_entered,
            key="password",
        )
        st.stop()

    if not st.session_state["password_correct"]:
        st.error("😕 Incorrect password")
        st.text_input(
            "Enter dashboard password",
            type="password",
            on_change=password_entered,
            key="password",
        )
        st.stop()

    return True


# ---------- YOUTUBE FETCH LOGIC ----------

API_KEY = st.secrets["YOUTUBE_API_KEY"]  # set this in Streamlit Secrets


def _parse_iso8601_duration(duration: str) -> int:
    """Convert YouTube ISO 8601 duration like PT5M10S to total seconds."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


@st.cache_data(ttl=4 * 60 * 60)  # cache results for 4 hours
def fetch_trending_news_ca(max_results: int = 50):
    """
    Fetch trending News & Politics videos in Canada.
    Returns (df, fetched_at_datetime).
    """
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,statistics,contentDetails",
        "chart": "mostPopular",
        "regionCode": "CA",
        "videoCategoryId": "25",  # News & Politics
        "maxResults": max_results,
        "key": API_KEY,
    }

    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    videos = []
    channel_ids = set()

    for item in data.get("items", []):
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    details = item.get("contentDetails", {})
    thumbs = snippet.get("thumbnails", {})

    # Pick best thumbnail + get its dimensions
    thumb_obj = (
        thumbs.get("medium", {})
        or thumbs.get("high", {})
        or thumbs.get("default", {})
    )
    thumb_url = thumb_obj.get("url")
    thumb_w = thumb_obj.get("width")
    thumb_h = thumb_obj.get("height")

    # Vertical detection – treat clearly tall thumbnails as vertical
    if thumb_w and thumb_h:
        aspect = thumb_w / thumb_h
        is_vertical = aspect < 0.9   # <1.0 = taller than wide; 0.9 for a bit of tolerance
    else:
        is_vertical = False

    # Duration
    duration_secs = _parse_iso8601_duration(details.get("duration", "PT0S"))
# --- improved Shorts detection ---
# Combine text for easier keyword scanning
text = (snippet.get("title", "") + " " + snippet.get("description", "")).lower()

# Detect #short / #shorts literally
marked_as_shorts = (
    "#shorts" in text
    or "#short " in text.replace("#shorts", "")
)

# Vertical detection — tall thumbnails = likely Shorts
is_vertical = False
if thumb_w and thumb_h:
    aspect = thumb_w / thumb_h
    is_vertical = aspect < 0.9  # clearly vertical

# FINAL Shorts classification
is_short = (
    duration_secs <= 75
    or marked_as_shorts
    or is_vertical
)
    })

    videos.append({
        "video_id": item["id"],
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "channel_title": snippet.get("channelTitle"),
        "published_at": snippet.get("publishedAt"),
        "url": f"https://www.youtube.com/watch?v={item['id']}",
        "view_count": int(stats.get("viewCount", 0)),
        "thumbnail_url": thumb_url,
        "duration_sec": duration_secs,
        "is_short": is_short,
    })

    })

        channel_id = snippet.get("channelId")
        if channel_id:
            channel_ids.add(channel_id)

        videos.append(
            {
                "video_id": item["id"],
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "channel_title": snippet.get("channelTitle"),
                "channel_id": channel_id,
                "published_at": snippet.get("publishedAt"),
                "url": f"https://www.youtube.com/watch?v={item['id']}",
                "view_count": int(stats.get("viewCount", 0)),
                "thumbnail_url": thumb_url,
                "duration_seconds": duration_secs,
                "is_short": is_short,
            }
        )

    # Fetch channel logos + country
    channel_info = {}
    if channel_ids:
        ch_url = "https://www.googleapis.com/youtube/v3/channels"
        ch_params = {
            "part": "snippet",
            "id": ",".join(channel_ids),
            "key": API_KEY,
        }
        ch_resp = requests.get(ch_url, params=ch_params)
        ch_resp.raise_for_status()
        ch_data = ch_resp.json()
        for ch in ch_data.get("items", []):
            cid = ch["id"]
            s = ch["snippet"]
            cthumbs = s.get("thumbnails", {})
            logo = (
                cthumbs.get("default", {}).get("url")
                or cthumbs.get("medium", {}).get("url")
                or cthumbs.get("high", {}).get("url")
            )
            country = s.get("country")  # 'CA', 'US', etc., sometimes None
            channel_info[cid] = {"logo": logo, "country": country}

    for v in videos:
        info = channel_info.get(v["channel_id"], {})
        v["channel_logo"] = info.get("logo")
        v["channel_country"] = info.get("country")

    fetched_at = datetime.now(timezone.utc)
    df = pd.DataFrame(videos)
    df["fetched_at"] = fetched_at
    return df, fetched_at


# ---------- DISPLAY HELPERS ----------


def nice_age(published_at_str: str, ref_dt: datetime) -> str:
    dt = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
    delta = ref_dt - dt
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins} min ago"
    hours = int(secs // 3600)
    if hours < 48:
        return f"{hours} hours ago"
    days = int(secs // 86400)
    return f"{days} days ago"


def views_badge(views: int) -> str:
    if views >= 2_000_000:
        return "🔥"
    elif views >= 1_000_000:
        return "⭐"
    return ""


def origin_label(country: str | None) -> str:
    if country == "CA":
        return "🇨🇦 Canadian outlet"
    elif country:
        return f"🌎 {country} outlet"
    return "🌐 Country unknown"


# ---------- STREAMLIT PAGE LAYOUT ----------

st.set_page_config(
    page_title="CA YouTube News & Politics Trending",
    layout="wide",
)

# Password gate
if not check_password():
    st.stop()

st.title("YouTube Trending – News & Politics (Canada)")

# Fetch data
df, fetched_at = fetch_trending_news_ca()
fetched_str = fetched_at.strftime("%Y-%m-%d %H:%M UTC")
st.caption(f"Data fetched at {fetched_str} · Auto-refreshes every 4 hours")

# Split into regular + shorts and keep top 15 each
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

tab1, tab2 = st.tabs(["📺 Regular Videos (Top 15)", "📱 Shorts (Top 15)"])


def render_section(subdf: pd.DataFrame):
    if subdf.empty:
        st.info("No videos found in this category.")
        return

    for rank, (_, row) in enumerate(subdf.iterrows(), start=1):
        cols = st.columns([1.3, 3])

        with cols[0]:
            if row["thumbnail_url"]:
                st.image(row["thumbnail_url"], use_column_width=True)

        with cols[1]:
            badge = views_badge(row["view_count"])
            kind = "Short" if row["is_short"] else "Video"
            age = nice_age(row["published_at"], fetched_at)
            origin = origin_label(row.get("channel_country"))

            st.markdown(
                f"**{rank}. [{row['title']}]({row['url']}) {badge}**",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"Views: {row['view_count']:,} · {kind} · {age}"
            )

            logo = row.get("channel_logo")
            logo_col, text_col = st.columns([0.25, 3.75])
            with logo_col:
                if logo:
                    st.image(logo, width=32)
            with text_col:
                st.markdown(
                    f"**{row['channel_title']}**  \n{origin}"
                )

            st.markdown("---")


with tab1:
    render_section(regular_df)

with tab2:
    render_section(shorts_df)
