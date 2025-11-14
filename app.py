import math
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple

import pytz
import requests
import streamlit as st

# ------------------------------------------------------------
# Config / constants
# ------------------------------------------------------------

API_KEY = st.secrets["YOUTUBE_API_KEY"]
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

REGION_CODE = "CA"
CATEGORY_ID = "25"  # News & Politics
CACHE_TTL_SECONDS = 4 * 60 * 60  # ~4 hours

ET_TZ = pytz.timezone("America/Toronto")

BANNER_URL = (
    "https://github.com/parkerprod953-dotcom/youtube-trending-dashboard/"
    "raw/fb65a040fe112f308c30f24e7693af1fade31d1f/assets/banner.jpg"
)

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------


def parse_iso8601_duration(duration: str) -> int:
    """
    Parse YouTube's ISO8601 duration format into total seconds.
    Example: PT1H2M3S -> 3723
    """
    if not duration or not duration.startswith("PT"):
        return 0

    time_str = duration[2:]
    hours = minutes = seconds = 0
    num = ""
    for ch in time_str:
        if ch.isdigit():
            num += ch
        else:
            if ch == "H":
                hours = int(num or 0)
            elif ch == "M":
                minutes = int(num or 0)
            elif ch == "S":
                seconds = int(num or 0)
            num = ""
    return hours * 3600 + minutes * 60 + seconds


def format_views(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B".rstrip("0").rstrip(".")
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M".rstrip("0").rstrip(".")
    if n >= 1_000:
        return f"{n/1_000:.1f}K".rstrip("0").rstrip(".")
    return str(n)


def format_duration(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def format_time_ago(published_at_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
    except Exception:
        return ""
    now = datetime.now(timezone.utc)
    diff = now - dt

    if diff < timedelta(minutes=1):
        return "just now"
    if diff < timedelta(hours=1):
        mins = int(diff.total_seconds() // 60)
        return f"{mins} min ago"
    if diff < timedelta(days=1):
        hrs = int(diff.total_seconds() // 3600)
        return f"{hrs} hours ago"
    days = diff.days
    if days == 1:
        return "1 day ago"
    if days < 7:
        return f"{days} days ago"
    weeks = days // 7
    if weeks == 1:
        return "1 week ago"
    return f"{weeks} weeks ago"


def classify_origin(country_code: str | None) -> Tuple[str, str]:
    """
    Return (machine_label, human_readable) from channel country.
    """
    if not country_code:
        return "global", "Global / unknown outlet"
    if country_code == "CA":
        return "canadian", "Canadian outlet"
    return "global", f"{country_code} outlet"


# ------------------------------------------------------------
# Data fetching
# ------------------------------------------------------------


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_trending_data() -> Dict:
    """
    Fetch trending News & Politics videos for Canada using YouTube Data API.
    Returns a dict with:
      - videos: list[dict]
      - fetched_at: datetime (UTC)
    """
    params = {
        "part": "snippet,contentDetails,statistics",
        "chart": "mostPopular",
        "regionCode": REGION_CODE,
        "videoCategoryId": CATEGORY_ID,
        "maxResults": 50,
        "key": API_KEY,
    }

    resp = requests.get(YOUTUBE_VIDEOS_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])

    videos: List[Dict] = []
    channel_ids: set[str] = set()

    for item in items:
        snippet = item.get("snippet", {})
        details = item.get("contentDetails", {})
        stats = item.get("statistics", {})
        thumbs = snippet.get("thumbnails", {}) or {}

        # Pick a decent thumbnail and get its size
        thumb_obj = (
            thumbs.get("medium")
            or thumbs.get("high")
            or thumbs.get("default")
            or {}
        )
        thumb_url = thumb_obj.get("url", "")
        thumb_w = thumb_obj.get("width")
        thumb_h = thumb_obj.get("height")

        # Vertical detection – clearly taller than wide
        if thumb_w and thumb_h and thumb_h > 0:
            aspect = thumb_w / thumb_h
            is_vertical = aspect < 0.9
        else:
            is_vertical = False

        duration_secs = parse_iso8601_duration(details.get("duration", "PT0S"))

        # Shorts detection by hashtag or duration
        text = (snippet.get("title", "") + " " + snippet.get("description", "")).lower()
        marked_as_shorts = (
            "#shorts" in text
            or "#short " in text
            or " #short" in text
        )
        is_short = marked_as_shorts or duration_secs <= 75 or is_vertical

        view_count = int(stats.get("viewCount", 0))

        channel_id = snippet.get("channelId", "")
        if channel_id:
            channel_ids.add(channel_id)

        videos.append(
            {
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "channel_title": snippet.get("channelTitle", ""),
                "channel_id": channel_id,
                "published_at": snippet.get("publishedAt", ""),
                "view_count": view_count,
                "duration_sec": duration_secs,
                "is_short": is_short,
                "thumbnail_url": thumb_url,
                "is_vertical": is_vertical,
            }
        )

    # Fetch channel countries + logos
    channel_info: Dict[str, Dict] = {}
    if channel_ids:
        ch_params = {
            "part": "snippet",
            "id": ",".join(channel_ids),
            "key": API_KEY,
        }
        ch_resp = requests.get(YOUTUBE_CHANNELS_URL, params=ch_params, timeout=15)
        ch_resp.raise_for_status()
        ch_data = ch_resp.json()
        for ch in ch_data.get("items", []):
            ch_id = ch.get("id")
            ch_snip = ch.get("snippet", {})
            country = ch_snip.get("country")
            logos = ch_snip.get("thumbnails", {}) or {}
            logo_url = (
                logos.get("default", {}).get("url")
                or logos.get("medium", {}).get("url")
                or logos.get("high", {}).get("url")
                or ""
            )
            channel_info[ch_id] = {"country": country, "logo": logo_url}

    for v in videos:
        info = channel_info.get(v["channel_id"], {})
        country = info.get("country")
        logo = info.get("logo", "")
        origin_label, origin_text = classify_origin(country)
        v["channel_country"] = country
        v["channel_logo"] = logo
        v["origin_label"] = origin_label
        v["origin_text"] = origin_text

    fetched_at = datetime.now(timezone.utc)
    return {"videos": videos, "fetched_at": fetched_at}


def split_video_lists(videos: List[Dict]) -> Dict:
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    regular = [v for v in videos if not v["is_short"]]
    shorts = [v for v in videos if v["is_short"]]

    def sort_key(v):
        return v["view_count"]

    regular.sort(key=sort_key, reverse=True)
    shorts.sort(key=sort_key, reverse=True)

    recent_regular = []
    for v in regular:
        try:
            dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        if dt >= cutoff_24h:
            recent_regular.append(v)

    recent_regular.sort(key=sort_key, reverse=True)

    return {
        "regular": regular,
        "shorts": shorts,
        "recent_regular": recent_regular,
    }


def filter_by_outlet(videos: List[Dict], outlet_filter: str) -> List[Dict]:
    if outlet_filter == "All outlets":
        return videos
    if outlet_filter == "Canadian outlets only":
        return [v for v in videos if v.get("origin_label") == "canadian"]
    if outlet_filter == "Global (non-Canadian) outlets":
        return [v for v in videos if v.get("origin_label") != "canadian"]
    return videos


# ------------------------------------------------------------
# UI helpers
# ------------------------------------------------------------


def inject_base_css():
    st.markdown(
        """
<style>
html, body, [class*="css"]  {
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

/* Hero banner */
.hero-banner {
    margin-top: 0.75rem;
    margin-bottom: 1.75rem;
    border-radius: 18px;
    overflow: hidden;
    position: relative;
    background-image:
        linear-gradient(120deg, rgba(0,0,0,0.82), rgba(0,0,0,0.50)),
        url('""" + BANNER_URL + """');
    background-size: cover;
    background-position: center;
    padding: 96px 48px 110px 48px;
    color: #ffffff;
}

/* Title + subtitle */
.hero-title {
    font-size: 2.3rem;
    font-weight: 650;
    letter-spacing: 0.03em;
    margin-bottom: 0.35rem;
}
.hero-subtitle {
    font-size: 0.98rem;
    opacity: 0.88;
}
.hero-subnote {
    font-size: 0.86rem;
    opacity: 0.78;
    margin-top: 0.2rem;
}

/* Last updated pill */
.hero-pill {
    position: absolute;
    left: 48px;
    bottom: 26px;
    background: rgba(0,0,0,0.75);
    border-radius: 999px;
    padding: 6px 14px;
    font-size: 0.83rem;
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
}

/* Video card layout */
.video-card {
    margin-bottom: 1.3rem;
    padding: 0.8rem 0.6rem 0.2rem 0.6rem;
    border-radius: 12px;
    transition: background-color 120ms ease, box-shadow 120ms ease;
}
.video-card:hover {
    background-color: rgba(0,0,0,0.02);
    box-shadow: 0 2px 10px rgba(15,15,15,0.06);
}
.video-thumb img {
    border-radius: 10px;
    width: 100%;
    max-width: 240px;  /* half-ish size thumbnail */
    height: auto;
    object-fit: cover;
}

/* Title + description */
.video-title {
    font-size: 1.02rem;
    font-weight: 620;
    margin-bottom: 0.15rem;
}
.video-meta {
    font-size: 0.92rem;
    color: #555;
}
.video-desc {
    font-size: 0.9rem;
    color: #333;
    margin-top: 0.35rem;
}

/* Rank badge */
.rank-badge {
    font-weight: 600;
    font-size: 0.9rem;
    color: #999;
    margin-right: 0.5rem;
}

.copy-pill {
    font-size: 0.78rem;
    padding: 0.2rem 0.45rem;
    border-radius: 999px;
    border: 1px solid rgba(0,0,0,0.12);
    background: rgba(255,255,255,0.9);
}

/* Tabs underline tweak */
.css-1y4p8pa e16nr0p33 {
    font-weight: 600;
}
</style>
""",
        unsafe_allow_html=True,
    )


def render_hero(fetched_at_utc: datetime):
    fetched_et = fetched_at_utc.astimezone(ET_TZ)
    fetched_str = fetched_et.strftime("%b %d, %Y • %I:%M %p ET")

    st.markdown(
        f"""
<div class="hero-banner">
  <div class="hero-title">YouTube News &amp; Politics – Trending Dashboard</div>
  <div class="hero-subtitle">
    Showing trending News &amp; Politics videos in Canada (YouTube's "mostPopular" chart for region CA).
  </div>
  <div class="hero-subnote">
    View counts shown are <b>global</b>. The YouTube Data API does not expose Canada-only viewership.
  </div>
  <div class="hero-pill">
    <span style="font-size:0.9rem;">⏱</span>
    <span><b>Last updated:</b> {fetched_str}</span>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def build_copy_block(v: Dict) -> str:
    return (
        f"Title: {v['title']}\n"
        f"Description: {v['description']}\n"
        f"Views: {format_views(v['view_count'])}\n"
        f"Duration: {format_duration(v['duration_sec'])}\n"
        f"Channel: {v['channel_title']} ({v['origin_text']})\n"
        f"URL: https://www.youtube.com/watch?v={v['video_id']}"
    )


def render_video_list(videos: List[Dict], section_key: str):
    if not videos:
        st.info("No videos match this filter yet. Try switching outlet filter or section.")
        return

    for idx, v in enumerate(videos, start=1):
        url = f"https://www.youtube.com/watch?v={v['video_id']}"
        views_str = format_views(v["view_count"])
        duration_str = format_duration(v["duration_sec"])
        age_str = format_time_ago(v["published_at"])

        is_hot = v["view_count"] >= 1_000_000
        try:
            dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
        except Exception:
            dt = None
        is_fresh = dt and (datetime.now(timezone.utc) - dt <= timedelta(hours=24))

        emoji = ""
        if is_hot:
            emoji += " 🔥"
        if is_fresh:
            emoji += " ⭐"

        with st.container():
            cols = st.columns([1.0, 3.0])
            with cols[0]:
                st.markdown(
                    '<div class="video-thumb">',
                    unsafe_allow_html=True,
                )
                st.image(v["thumbnail_url"], use_column_width=True)
                st.markdown("</div>", unsafe_allow_html=True)

            with cols[1]:
                st.markdown(
                    f'<div class="video-card">',
                    unsafe_allow_html=True,
                )

                # Title / rank
                st.markdown(
                    f"""
<span class="rank-badge">#{idx}</span>
<a href="{url}" target="_blank" class="video-title" style="text-decoration:none;color:#0056b3;">
    {v['title']}
</a>{emoji}
""",
                    unsafe_allow_html=True,
                )

                # Meta line (views, duration, age)
                st.markdown(
                    f"""
<div class="video-meta">
  👁 {views_str} views &nbsp;•&nbsp; ⏱ {duration_str} &nbsp;•&nbsp; {age_str}
</div>
""",
                    unsafe_allow_html=True,
                )

                # Channel line
                origin = v["origin_text"]
                st.markdown(
                    f"""
<div class="video-meta" style="margin-top:0.18rem;">
  <b>{v['channel_title']}</b> · {origin}
</div>
""",
                    unsafe_allow_html=True,
                )

                # Description (trimmed a bit)
                desc = (v["description"] or "").strip()
                if desc:
                    desc_short = "\n".join(desc.splitlines()[:3])
                    if len(desc) > len(desc_short):
                        desc_short += " …"
                    st.markdown(
                        f'<div class="video-desc">{desc_short}</div>',
                        unsafe_allow_html=True,
                    )

                # Copy details button
                copy_text = build_copy_block(v)
                with st.expander("Copy details", expanded=False):
                    st.code(copy_text)

                st.markdown("</div>", unsafe_allow_html=True)


# ------------------------------------------------------------
# Main app
# ------------------------------------------------------------


def main():
    st.set_page_config(
        page_title="CA YouTube News Dashboard",
        layout="wide",
        page_icon="📺",
    )

    inject_base_css()

    top_bar = st.columns([1, 3])
    with top_bar[0]:
        if st.button("🔄 Refresh data now"):
            fetch_trending_data.clear()
            st.experimental_rerun()
    with top_bar[1]:
        st.markdown(
            "<div style='margin-top:0.5rem;font-size:0.9rem;color:#666;'>"
            "Data auto-refreshes every ~4 hours, or use the button to refresh manually."
            "</div>",
            unsafe_allow_html=True,
        )

    data = fetch_trending_data()
    videos = data["videos"]
    fetched_at = data["fetched_at"]

    render_hero(fetched_at)

    # Outlet filter
    st.markdown("### Outlet filter")
    outlet_filter = st.radio(
        "",
        ["All outlets", "Canadian outlets only", "Global (non-Canadian) outlets"],
        horizontal=True,
        index=0,
    )

    # Legend for emojis
    st.markdown(
        """
<span style="font-size:0.9rem;color:#555;">
<b>Legend:</b> 🔥 = 1M+ views · ⭐ = Posted in last 24 hours and among the top-ranked videos today.
</span>
""",
        unsafe_allow_html=True,
    )

    split = split_video_lists(videos)

    filtered_regular = filter_by_outlet(split["regular"], outlet_filter)
    filtered_shorts = filter_by_outlet(split["shorts"], outlet_filter)
    filtered_recent = filter_by_outlet(split["recent_regular"], outlet_filter)

    st.markdown("---")

    tabs = st.tabs(["Regular videos", "Shorts", "Last 24 hours", "Raw table"])

    # --------------------------------------------------------
    # Regular videos tab
    # --------------------------------------------------------
    with tabs[0]:
        st.markdown(
            "## Top trending regular News & Politics videos in Canada\n"
            "<span style='font-size:0.9rem;color:#555;'>"
            "These are YouTube’s most popular News & Politics videos in Canada right now "
            "(16:9 / non-Shorts), ranked by YouTube’s trending chart."
            "</span>",
            unsafe_allow_html=True,
        )
        render_video_list(filtered_regular, "regular")

    # --------------------------------------------------------
    # Shorts tab
    # --------------------------------------------------------
    with tabs[1]:
        st.markdown(
            "## Top trending News & Politics Shorts in Canada\n"
            "<span style='font-size:0.9rem;color:#555;'>"
            "Videos detected as Shorts (vertical or ≤75 seconds, or tagged #shorts). "
            "Ranked by YouTube’s trending chart for Canada."
            "</span>",
            unsafe_allow_html=True,
        )
        render_video_list(filtered_shorts, "shorts")

    # --------------------------------------------------------
    # Last 24 hours tab
    # --------------------------------------------------------
    with tabs[2]:
        st.markdown(
            "## Best News & Politics uploads in the last 24 hours (Canada)\n"
            "<span style='font-size:0.9rem;color:#555;'>"
            "Regular (16:9) News & Politics videos posted within the last 24 hours, "
            "ranked by total views so far."
            "</span>",
            unsafe_allow_html=True,
        )
        render_video_list(filtered_recent, "recent")

    # --------------------------------------------------------
    # Raw table tab
    # --------------------------------------------------------
    with tabs[3]:
        st.markdown(
            "## Raw data view\n"
            "<span style='font-size:0.9rem;color:#555;'>"
            "All fetched videos with key fields for debugging / analysis."
            "</span>",
            unsafe_allow_html=True,
        )
        st.dataframe(
            [
                {
                    "title": v["title"],
                    "channel": v["channel_title"],
                    "origin": v["origin_text"],
                    "views": v["view_count"],
                    "duration_sec": v["duration_sec"],
                    "short": v["is_short"],
                    "published_at": v["published_at"],
                    "url": f"https://www.youtube.com/watch?v={v['video_id']}",
                }
                for v in filter_by_outlet(videos, outlet_filter)
            ],
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
