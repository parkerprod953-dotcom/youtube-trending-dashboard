import streamlit as st
import requests
from datetime import datetime, timedelta, timezone
import pytz
import re

# -----------------------------
# CONFIG / CONSTANTS
# -----------------------------

API_KEY = st.secrets["YOUTUBE_API_KEY"]      # set in Streamlit secrets
YOUTUBE_BASE = "https://www.googleapis.com/youtube/v3"
REGION_CODE = "CA"
NEWS_CATEGORY_ID = "25"                      # News & Politics
BANNER_URL = (
    "https://github.com/parkerprod953-dotcom/"
    "youtube-trending-dashboard/raw/"
    "fb65a040fe112f308c30f24e7693af1fade31d1f/assets/banner.jpg"
)

TZ_ET = pytz.timezone("US/Eastern")

# -----------------------------
# UTILS
# -----------------------------


def parse_iso8601_duration(duration: str) -> int:
    """Convert ISO 8601 duration (e.g. PT1H2M10S) to seconds."""
    if not duration or not duration.startswith("PT"):
        return 0
    pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    m = re.match(pattern, duration)
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def format_duration(seconds: int) -> str:
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:d}:{m:02d}:{s:02d}"
    m = seconds // 60
    s = seconds % 60
    return f"{m:d}:{s:02d}"


def format_views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def parse_published_at(ts: str) -> datetime:
    # YouTube timestamps are like "2025-11-13T20:42:01Z"
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def format_age(published_at: datetime, now: datetime) -> str:
    delta = now - published_at
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        minutes = int(delta.total_seconds() // 60)
        return f"{minutes} min ago"
    if delta < timedelta(days=1):
        hours = int(delta.total_seconds() // 3600)
        return f"{hours} hours ago"
    days = delta.days
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def origin_label(country_code: str | None) -> str:
    if country_code == "CA":
        return "Canadian outlet"
    if country_code == "US":
        return "US outlet"
    if not country_code:
        return "Global outlet"
    return f"{country_code} outlet"


# -----------------------------
# DATA FETCH
# -----------------------------


def fetch_trending_videos(max_results: int = 40) -> list[dict]:
    """Fetch trending News & Politics in Canada."""
    params = {
        "part": "snippet,contentDetails,statistics",
        "chart": "mostPopular",
        "regionCode": REGION_CODE,
        "videoCategoryId": NEWS_CATEGORY_ID,
        "maxResults": max_results,
        "key": API_KEY,
    }
    resp = requests.get(f"{YOUTUBE_BASE}/videos", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    videos: list[dict] = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        details = item.get("contentDetails", {})
        thumbs = (snippet.get("thumbnails") or {})

        # Choose best thumbnail
        t_obj = (
            thumbs.get("maxres")
            or thumbs.get("standard")
            or thumbs.get("high")
            or thumbs.get("medium")
            or thumbs.get("default")
            or {}
        )
        thumb_url = t_obj.get("url")
        thumb_w = t_obj.get("width") or 0
        thumb_h = t_obj.get("height") or 0

        # Vertical detection from thumbnail aspect ratio
        is_vertical = False
        if thumb_w and thumb_h:
            aspect = thumb_w / thumb_h
            # < 0.9 means taller than wide
            is_vertical = aspect < 0.9

        # Duration
        duration_secs = parse_iso8601_duration(details.get("duration", ""))

        # Shorts detection
        text = (snippet.get("title", "") + " " +
                snippet.get("description", "")).lower()
        tagged_short = (
            "#shorts" in text
            or "#short " in text
            or " #short" in text
        )
        is_short = tagged_short or duration_secs <= 75 or is_vertical

        video_id = item.get("id")
        if not video_id:
            continue

        try:
            view_count = int(stats.get("viewCount", 0))
        except (TypeError, ValueError):
            view_count = 0

        published_at = parse_published_at(snippet.get("publishedAt", ""))

        videos.append(
            {
                "video_id": video_id,
                "title": snippet.get("title", ""),
                "description": snippet.get("description", "") or "",
                "channel_title": snippet.get("channelTitle", "") or "",
                "channel_id": snippet.get("channelId", ""),
                "published_at": published_at,
                "view_count": view_count,
                "duration_sec": duration_secs,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail_url": thumb_url,
                "is_short": is_short,
                "is_vertical": is_vertical,
            }
        )

    return videos


def fetch_channel_info(channel_ids: list[str]) -> dict:
    """Fetch channel country + logo thumbnail."""
    if not channel_ids:
        return {}
    unique_ids = list({cid for cid in channel_ids if cid})

    info: dict[str, dict] = {}
    # API allows up to 50 ids per request
    for i in range(0, len(unique_ids), 50):
        chunk = unique_ids[i : i + 50]
        params = {
            "part": "snippet",
            "id": ",".join(chunk),
            "key": API_KEY,
            "maxResults": 50,
        }
        resp = requests.get(f"{YOUTUBE_BASE}/channels", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("items", []):
            cid = item.get("id")
            snip = item.get("snippet", {})
            thumbs = (snip.get("thumbnails") or {})
            logo_obj = (
                thumbs.get("default")
                or thumbs.get("medium")
                or thumbs.get("high")
                or {}
            )
            info[cid] = {
                "country": snip.get("country"),
                "logo_url": logo_obj.get("url"),
            }
    return info


# -----------------------------
# PRESENTATION HELPERS
# -----------------------------


def header_and_styles(last_updated_et: datetime):
    last_str = last_updated_et.strftime("%b %d, %Y · %I:%M %p ET")

    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap');

        html, body, [class*="css"] {{
            font-family: "Montserrat", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }}

        .banner-wrapper {{
            position: relative;
            width: 100%;
            height: 170px;
            margin-bottom: 0.5rem;
        }}
        .banner-bg {{
            position: absolute;
            inset: 0;
            background-image: url('{BANNER_URL}');
            background-size: cover;
            background-position: center;
            filter: brightness(0.35) blur(2px);
        }}
        .banner-overlay {{
            position: absolute;
            inset: 0;
            background: linear-gradient(to right, rgba(0,0,0,0.7), rgba(0,0,0,0.2));
        }}
        .banner-content {{
            position: absolute;
            inset: 0;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            color: #ffffff;
            padding: 0 1rem;
        }}
        .banner-title {{
            font-size: 2.1rem;
            font-weight: 700;
            letter-spacing: 0.04em;
        }}
        .banner-subtext {{
            margin-top: 0.25rem;
            font-size: 0.98rem;
            opacity: 0.9;
        }}
        .last-updated-pill {{
            display: inline-block;
            margin-top: 0.5rem;
            padding: 0.22rem 0.75rem;
            border-radius: 999px;
            background-color: rgba(255,255,255,0.12);
            font-size: 0.86rem;
        }}

        .section-title {{
            font-size: 1.35rem;
            font-weight: 600;
            margin-bottom: 0.1rem;
        }}
        .section-subtitle {{
            font-size: 0.92rem;
            color: #555;
            margin-bottom: 0.5rem;
        }}
        .video-title {{
            font-size: 1.02rem;
            font-weight: 600;
        }}
        .video-metrics {{
            font-size: 0.98rem;
            color: #444;
        }}
        .video-desc {{
            font-size: 0.90rem;
            color: #555;
        }}
        .channel-row {{
            font-size: 0.92rem;
            color: #333;
        }}
        .channel-row img {{
            vertical-align: middle;
            border-radius: 50%;
            margin-right: 6px;
        }}
        .rank-badge {{
            font-weight: 600;
            margin-right: 6px;
            color: #888;
        }}
        .legend-box {{
            background-color: #f7f7f7;
            border-radius: 8px;
            padding: 0.6rem 0.9rem;
            font-size: 0.88rem;
        }}
        </style>

        <div class="banner-wrapper">
            <div class="banner-bg"></div>
            <div class="banner-overlay"></div>
            <div class="banner-content">
                <div class="banner-title">
                    YouTube News &amp; Politics – Trending Dashboard
                </div>
                <div class="banner-subtext">
                    Showing trending News &amp; Politics videos in Canada.<br/>
                    View counts shown are global; the YouTube Data API does not expose Canada-only viewership.
                </div>
                <div class="last-updated-pill">
                    ⏱ Last updated: {last_str}
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def filter_by_outlet(videos: list[dict], channel_info: dict, mode: str) -> list[dict]:
    if mode == "Canadian outlets only":
        return [
            v
            for v in videos
            if channel_info.get(v["channel_id"], {}).get("country") == "CA"
        ]
    if mode == "Global (non-Canadian) outlets":
        return [
            v
            for v in videos
            if channel_info.get(v["channel_id"], {}).get("country") != "CA"
        ]
    return videos


def render_video_list(
    videos: list[dict],
    channel_info: dict,
    outlet_mode: str,
    section_title: str,
    section_desc: str,
    max_items: int = 15,
):
    videos = filter_by_outlet(videos, channel_info, outlet_mode)
    videos = videos[:max_items]

    if not videos:
        st.info("No videos match this filter yet.")
        return

    st.markdown(f'<div class="section-title">{section_title}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="section-subtitle">{section_desc}</div>', unsafe_allow_html=True)

    for rank, v in enumerate(videos, start=1):
        with st.container():
            cols = st.columns([1.1, 2.3])
            with cols[0]:
                if v["thumbnail_url"]:
                    st.image(v["thumbnail_url"], use_column_width=True)
                else:
                    st.markdown("No thumbnail")
            with cols[1]:
                # badges
                hot = "🔥" if v["view_count"] >= 1_000_000 else ""
                star = "⭐" if rank <= 3 else ""
                rank_html = f'<span class="rank-badge">#{rank}</span>'
                title_html = (
                    f'<span class="video-title">'
                    f'{rank_html}<a href="{v["url"]}" target="_blank">{v["title"]}</a> '
                    f'{star} {hot}'
                    f"</span>"
                )
                st.markdown(title_html, unsafe_allow_html=True)

                now_utc = datetime.now(timezone.utc)
                views_str = format_views(v["view_count"])
                dur_str = format_duration(v["duration_sec"])
                age_str = format_age(v["published_at"], now_utc)

                metrics_html = (
                    f'<div class="video-metrics">'
                    f'👁 {views_str} views · ⏱ {dur_str} · {age_str}'
                    f"</div>"
                )
                st.markdown(metrics_html, unsafe_allow_html=True)

                # short description
                desc = (v["description"] or "").replace("\n", " ")
                if len(desc) > 220:
                    desc = desc[:220].rsplit(" ", 1)[0] + "…"
                if desc:
                    desc_html = f'<div class="video-desc">{desc}</div>'
                    st.markdown(desc_html, unsafe_allow_html=True)

                # channel row
                cinfo = channel_info.get(v["channel_id"], {}) or {}
                origin = origin_label(cinfo.get("country"))
                logo = cinfo.get("logo_url")
                if logo:
                    logo_html = f'<img src="{logo}" width="24" height="24" />'
                else:
                    logo_html = "🎙️"
                ch_html = (
                    f'<div class="channel-row">{logo_html}'
                    f'<strong>{v["channel_title"]}</strong> · {origin}</div>'
                )
                st.markdown(ch_html, unsafe_allow_html=True)

                # copy-ready snippet
                snippet = (
                    f"Title: {v['title']}\n"
                    f"Channel: {v['channel_title']}\n"
                    f"Views: {views_str}\n"
                    f"Duration: {dur_str}\n"
                    f"URL: {v['url']}\n\n"
                    f"Description:\n{v['description']}"
                )
                with st.expander("Copy video details"):
                    st.text_area(
                        label="",
                        value=snippet,
                        height=140,
                        label_visibility="collapsed",
                    )

        st.markdown("---")


# -----------------------------
# MAIN APP
# -----------------------------


def main():
    st.set_page_config(
        page_title="CA YouTube News Dashboard",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Use session_state to avoid refetching too often
    now_utc = datetime.now(timezone.utc)
    if "videos_data" not in st.session_state or "videos_last_fetch" not in st.session_state:
        st.session_state["videos_data"] = None
        st.session_state["videos_last_fetch"] = None

    fetch_needed = False
    last_fetch = st.session_state["videos_last_fetch"]

    # Manual refresh button
    refresh_col, info_col = st.columns([0.22, 0.78])
    with refresh_col:
        if st.button("🔄 Refresh data now"):
            fetch_needed = True

    if last_fetch is None or (now_utc - last_fetch) > timedelta(hours=4):
        fetch_needed = True

    if fetch_needed:
        with st.spinner("Fetching latest trending data from YouTube…"):
            videos = fetch_trending_videos(max_results=50)
            channel_ids = [v["channel_id"] for v in videos if v["channel_id"]]
            channel_info = fetch_channel_info(channel_ids)
            st.session_state["videos_data"] = {
                "videos": videos,
                "channel_info": channel_info,
            }
            st.session_state["videos_last_fetch"] = now_utc

    data = st.session_state["videos_data"]
    if not data:
        st.error("Unable to load data.")
        return

    videos = data["videos"]
    channel_info = data["channel_info"]
    last_fetch = st.session_state["videos_last_fetch"]
    last_fetch_et = last_fetch.astimezone(TZ_ET)

    # Banner + styles
    header_and_styles(last_fetch_et)

    with info_col:
        st.markdown(
            "Data auto-refreshes every ~4 hours, or use the button to refresh manually.",
        )

    # Derived subsets
    regular = [v for v in videos if not v["is_short"]]
    shorts = [v for v in videos if v["is_short"]]

    now_utc = datetime.now(timezone.utc)
    recent = [v for v in videos if (now_utc - v["published_at"]) <= timedelta(hours=24)]
    recent_regular = [v for v in recent if not v["is_short"]]
    recent_shorts = [v for v in recent if v["is_short"]]

    # Sort inside "recent" by views
    recent_regular.sort(key=lambda v: v["view_count"], reverse=True)
    recent_shorts.sort(key=lambda v: v["view_count"], reverse=True)

    # Outlet filter
    outlet_mode = st.radio(
        "Outlet filter",
        ["All outlets", "Canadian outlets only", "Global (non-Canadian) outlets"],
        horizontal=True,
    )

    st.markdown("")

    # Tabs
    tab_reg, tab_short, tab_recent, tab_table = st.tabs(
        ["Regular videos", "Shorts", "Last 24 hours", "Raw table"]
    )

    with tab_reg:
        render_video_list(
            regular,
            channel_info,
            outlet_mode,
            section_title="Top trending regular News & Politics videos in Canada",
            section_desc=(
                "These are YouTube’s **most popular News & Politics videos** in Canada "
                "right now (16:9 / non-Shorts). Ranked by YouTube’s trending chart."
            ),
            max_items=15,
        )

    with tab_short:
        render_video_list(
            shorts,
            channel_info,
            outlet_mode,
            section_title="Top trending YouTube Shorts – News & Politics in Canada",
            section_desc=(
                "Short-form vertical videos and content tagged as Shorts, "
                "based on YouTube’s News & Politics **trending chart in Canada**."
            ),
            max_items=15,
        )

    with tab_recent:
        st.markdown(
            '<div class="section-title">Best News & Politics videos posted in the last 24 hours</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="section-subtitle">'
            "Videos published **within the last 24 hours**, ranked by global view count. "
            "Useful for spotting very fresh stories that are starting to move."
            "</div>",
            unsafe_allow_html=True,
        )

        subtab_reg, subtab_short = st.tabs(["Regular uploads", "Shorts"])
        with subtab_reg:
            render_video_list(
                recent_regular,
                channel_info,
                outlet_mode,
                section_title="Top regular uploads (< 24h old)",
                section_desc="Ranked by current global views among videos less than 24 hours old.",
                max_items=15,
            )
        with subtab_short:
            render_video_list(
                recent_shorts,
                channel_info,
                outlet_mode,
                section_title="Top Shorts (< 24h old)",
                section_desc="Short-form News & Politics content published in the last 24 hours.",
                max_items=15,
            )

    with tab_table:
        st.write("Underlying data (all fetched items):")
        # Light table, without description (too long)
        table_rows = []
        for v in videos:
            cinfo = channel_info.get(v["channel_id"], {}) or {}
            table_rows.append(
                {
                    "title": v["title"],
                    "channel": v["channel_title"],
                    "country": cinfo.get("country"),
                    "views": v["view_count"],
                    "duration_sec": v["duration_sec"],
                    "published_at_utc": v["published_at"],
                    "is_short": v["is_short"],
                    "url": v["url"],
                }
            )
        st.dataframe(table_rows, use_container_width=True)

    st.markdown("")
    st.markdown(
        """
        <div class="legend-box">
        <strong>Legend</strong><br/>
        ⭐ &nbsp;Top 3 video in this list<br/>
        🔥 &nbsp;Approx. 1M+ global views (hot performance)<br/>
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
