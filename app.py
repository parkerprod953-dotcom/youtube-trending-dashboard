import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone

# -----------------------------
# Utility Functions
# -----------------------------

def parse_iso8601_duration(duration: str) -> int:
    """Convert ISO 8601 duration (PT#H#M#S) into seconds."""
    if not duration or not duration.startswith("PT"):
        return 0
    duration = duration[2:]
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


def format_time_ago(iso_time: str) -> str:
    """Return human-friendly time difference (e.g., '3 hours ago')."""
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    except Exception:
        return "unknown"
    now = datetime.now(timezone.utc)
    diff = now - dt
    secs = diff.total_seconds()

    if secs < 60:
        return "just now"
    mins = secs / 60
    if mins < 60:
        return f"{int(mins)} min ago"
    hrs = mins / 60
    if hrs < 24:
        return f"{int(hrs)} hours ago"
    days = hrs / 24
    if days < 7:
        return f"{int(days)} days ago"
    weeks = days / 7
    if weeks < 4:
        return f"{int(weeks)} weeks ago"
    return f"{int(days/30)} months ago"


def format_views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


# -----------------------------
# API Fetching
# -----------------------------

def fetch_trending(api_key: str):
    """Fetch trending News & Politics videos in Canada."""
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,contentDetails,statistics",
        "chart": "mostPopular",
        "regionCode": "CA",
        "videoCategoryId": "25",  # News & Politics
        "maxResults": 50,
        "key": api_key,
    }
    r = requests.get(url, params=params)
    r.raise_for_status()
    data = r.json()

    videos = []
    channel_ids = set()

    for item in data.get("items", []):
        snippet = item["snippet"]
        stats = item["statistics"]
        details = item["contentDetails"]

        # Thumbnail selection
        thumbs = snippet.get("thumbnails", {})
        t = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}
        thumb_url = t.get("url")
        thumb_w = t.get("width", 0)
        thumb_h = t.get("height", 0)

        # Vertical detection
        is_vertical = False
        if thumb_w and thumb_h:
            aspect = thumb_w / thumb_h
            is_vertical = aspect < 0.9

        # Duration
        duration_sec = parse_iso8601_duration(details.get("duration", "PT0S"))

        # Shorts keyword detection
        text = (snippet.get("title", "") + " " + snippet.get("description", "")).lower()
        marked_short = "#shorts" in text or ("#short" in text.replace("#shorts", ""))

        # Final Shorts logic
        is_short = (duration_sec <= 75) or marked_short or is_vertical

        video = {
            "video_id": item["id"],
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "channel_title": snippet.get("channelTitle"),
            "channel_id": snippet.get("channelId"),
            "published_at": snippet.get("publishedAt"),
            "url": f"https://www.youtube.com/watch?v={item['id']}",
            "view_count": int(stats.get("viewCount", 0)),
            "thumbnail_url": thumb_url,
            "duration_sec": duration_sec,
            "is_short": is_short,
            "is_vertical": is_vertical,
        }
        videos.append(video)

        if snippet.get("channelId"):
            channel_ids.add(snippet["channelId"])

    return videos, channel_ids


def fetch_channel_info(api_key: str, channel_ids: set):
    """Fetch channel logo + country for each channel ID."""
    if not channel_ids:
        return {}

    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "snippet",
        "id": ",".join(channel_ids),
        "key": api_key,
    }
    r = requests.get(url, params=params)
    r.raise_for_status()
    data = r.json()

    info = {}
    for ch in data.get("items", []):
        cid = ch["id"]
        snip = ch["snippet"]
        thumbs = snip.get("thumbnails", {})
        t = thumbs.get("default") or thumbs.get("medium") or thumbs.get("high") or {}

        info[cid] = {
            "logo": t.get("url"),
            "country": snip.get("country"),
        }

    return info


@st.cache_data(ttl=60 * 60 * 4)
def load_data(api_key: str):
    """Load videos + channel info, cached for 4 hours."""
    videos, channel_ids = fetch_trending(api_key)
    channel_info = fetch_channel_info(api_key, channel_ids)
    df = pd.DataFrame(videos)
    fetched_at = datetime.now(timezone.utc)
    return df, channel_info, fetched_at


# -----------------------------
# UI Helpers
# -----------------------------

def render_card(row, channel_info):
    """Render a single video as a modern card."""
    cid = row.get("channel_id")
    info = channel_info.get(cid, {}) if cid else {}
    logo_url = info.get("logo")
    country = info.get("country")

    # Origin text
    if country == "CA":
        origin = "🇨🇦 Canadian outlet"
    elif country:
        origin = f"🌍 {country} outlet"
    else:
        origin = "🌍 outlet (country unknown)"

    # Metrics
    views = int(row.get("view_count", 0))
    views_text = f"{format_views(views)} views"
    duration = int(row.get("duration_sec", 0))
    duration_text = f"{duration // 60}:{duration % 60:02d}" if duration > 0 else "live"
    age_text = format_time_ago(row.get("published_at", ""))

    # Hot badge
    badge = ""
    if views >= 1_000_000:
        badge = "🔥"
    elif views >= 200_000:
        badge = "⭐"

    title = row.get("title") or "Untitled"
    url = row.get("url") or "#"
    channel_title = row.get("channel_title") or "Unknown channel"

    # Build card HTML (just for the right-hand side; thumbnail still uses st.image)
    meta_line = f"👁 {views_text} · ⏱ {duration_text} · 🕒 {age_text}"
    channel_line = f"{channel_title} · {origin}"

    right_html = f"""
<div style="display:flex;flex-direction:column;gap:4px;">
  <div style="font-size:1.05rem;font-weight:600;">
    <a href="{url}" target="_blank"
       style="text-decoration:none;color:#101318;">
       {title}
    </a> {badge}
  </div>
  <div style="font-size:0.85rem;color:#6b7280;">
    {meta_line}
  </div>
  <div style="display:flex;align-items:center;gap:8px;font-size:0.9rem;color:#374151;margin-top:2px;">
    {"<img src='" + logo_url + "' style='width:20px;height:20px;border-radius:50%;object-fit:cover;'/>" if logo_url else ""}
    <span>{channel_line}</span>
  </div>
</div>
"""

    card_html_start = """
<div style="
    background-color:#ffffff;
    border-radius:14px;
    padding:14px 18px;
    margin-bottom:14px;
    box-shadow:0 2px 6px rgba(15,23,42,0.08);
">
"""
    card_html_end = "</div>"

    with st.container():
        st.markdown(card_html_start, unsafe_allow_html=True)
        cols = st.columns([1.5, 3])

        with cols[0]:
            if row.get("thumbnail_url"):
                st.image(
                    row.get("thumbnail_url"),
                    use_column_width=True,
                )

        with cols[1]:
            st.markdown(right_html, unsafe_allow_html=True)

        st.markdown(card_html_end, unsafe_allow_html=True)


# -----------------------------
# Main App
# -----------------------------

def main():
    st.set_page_config(page_title="CA YouTube News Dashboard", layout="wide")

    # Global style: sleek system font & lighter background
    st.markdown(
        """
<style>
    html, body, [class*="css"]  {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background-color:#f3f4f6;
    }
    /* tighten default padding a bit */
    section.main > div {
        padding-top: 1rem;
    }
</style>
""",
        unsafe_allow_html=True,
    )

    st.title("🇨🇦 YouTube News & Politics – Trending Dashboard")

    # ---------- PASSWORD GATE ----------
    expected_pwd = st.secrets.get("DASHBOARD_PASSWORD")
    if expected_pwd:  # Only show login if password is set
        if "authed" not in st.session_state:
            st.session_state.authed = False

        if not st.session_state.authed:
            pwd = st.text_input("Enter dashboard password", type="password")
            if st.button("Submit"):
                if pwd == expected_pwd:
                    st.session_state.authed = True
                    st.rerun()
                else:
                    st.error("Incorrect password.")
            return
    # -----------------------------------

    api_key = st.secrets.get("YOUTUBE_API_KEY")
    if not api_key:
        st.error("Missing YOUTUBE_API_KEY in Streamlit Secrets.")
        return

    df, channel_info, fetched_at = load_data(api_key)

    st.caption(f"Last updated: {fetched_at.strftime('%Y-%m-%d %H:%M UTC')}")

    # Split into regular vs Shorts
    regular_df = df[~df["is_short"]].sort_values("view_count", ascending=False).head(15)
    shorts_df = df[df["is_short"]].sort_values("view_count", ascending=False).head(15)

    tab1, tab2, tab3 = st.tabs(["🎬 Regular Videos", "📱 Shorts", "📊 Raw Table"])

    with tab1:
        if regular_df.empty:
            st.info("No regular videos found.")
        else:
            for _, row in regular_df.iterrows():
                render_card(row, channel_info)

    with tab2:
        if shorts_df.empty:
            st.info("No Shorts found.")
        else:
            for _, row in shorts_df.iterrows():
                render_card(row, channel_info)

    with tab3:
        st.dataframe(
            df.sort_values("view_count", ascending=False),
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
