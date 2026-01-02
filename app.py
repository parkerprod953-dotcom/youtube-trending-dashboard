import streamlit as st
st.error("DEPLOY CHECK: app.py is live ✅")

import html
import re
import textwrap
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytz
import requests
import streamlit as st
import streamlit.components.v1 as components

# -----------------------------
# Basic config
# -----------------------------

API_KEY = st.secrets["YOUTUBE_API_KEY"]
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
REGION_CODE = "CA"              # Canada
CATEGORY_NEWS_POLITICS = "25"   # News & Politics

BANNER_URL = (
    "https://github.com/parkerprod953-dotcom/youtube-trending-dashboard/"
    "raw/fb65a040fe112f308c30f24e7693af1fade31d1f/assets/banner.jpg"
)

# -----------------------------
# Utility helpers
# -----------------------------


def yt_get(endpoint: str, params: dict) -> dict:
    params = {**params, "key": API_KEY}
    resp = requests.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params, timeout=20)
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
    return (((days * 24 + hours) * 60) + minutes) * 60 + seconds


def format_duration(seconds: int) -> str:
    if not seconds:
        return "–"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


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


def truncate_description_chars(text: str, max_chars: int = 200) -> str:
    """Used for the on-card preview: short snippet."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars + 1]
    cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def truncate_description_two_sentences(text: str, max_sentences: int = 2) -> str:
    """Used for copy-ready markdown: keep to N sentences."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text.strip())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:max_sentences]).strip()


# -----------------------------
# Fetch & prepare data
# -----------------------------


@st.cache_data(ttl=60 * 60 * 4, show_spinner=True)
def fetch_trending_videos():
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
        thumb_obj = thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {}
        thumb_url = thumb_obj.get("url")

        text_combined = (title + " " + desc).lower()
        has_short_tag = "#short" in text_combined or "#shorts" in text_combined
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

    channel_info = {}
    if channel_ids:
        id_list = list(channel_ids)
        for i in range(0, len(id_list), 50):
            chunk = ",".join(id_list[i : i + 50])
            ch_data = yt_get("channels", {"part": "snippet", "id": chunk})
            for ch in ch_data.get("items", []):
                cid = ch["id"]
                country = ch.get("snippet", {}).get("country")
                channel_info[cid] = {"country": country}

    now_utc = datetime.now(timezone.utc)
    df = pd.DataFrame(videos)

    if df.empty:
        return df, channel_info, now_utc

    df["channel_country"] = df["channel_id"].apply(lambda cid: (channel_info.get(cid) or {}).get("country"))
    df["origin_label"] = df["channel_country"].apply(lambda c: "Canadian outlet" if c == "CA" else "Non-Canadian outlet")

    df["age_hours"] = (now_utc - df["published_at"]).dt.total_seconds() / 3600.0
    df["views_per_hour"] = df.apply(lambda r: r["view_count"] / max(r["age_hours"], 1 / 60), axis=1)

    return df, channel_info, now_utc


# -----------------------------
# UI helpers
# -----------------------------


def render_css():
    st.markdown(
        """
<style>
html, body { background-color: #02030a !important; }
section.main > div { padding-top: 1.2rem; }
header[data-testid="stHeader"] { background: rgba(0,0,0,0) !important; }
div[data-testid="stToolbar"] { right: 2rem; }

.stApp {
  background: radial-gradient(1200px 800px at 15% 10%, rgba(255,75,75,0.18), transparent 60%),
              radial-gradient(1000px 700px at 85% 15%, rgba(255,159,67,0.14), transparent 55%),
              radial-gradient(1200px 900px at 50% 100%, rgba(70,90,255,0.10), transparent 65%),
              #02030a;
  color: #e5e9ff;
}

.block-container { max-width: 1200px; }

.video-card {
  border-radius: 16px;
  padding: 16px 18px;
  background: rgba(10, 14, 28, 0.65);
  border: 1px solid rgba(255,255,255,0.06);
  box-shadow: 0 10px 26px rgba(0,0,0,0.50);
  margin-bottom: 12px;
}
.video-thumb img {
  width: 100%;
  border-radius: 12px;
  border: 1px solid rgba(255,255,255,0.07);
}
details {
  border-radius: 12px;
  background: rgba(6, 9, 18, 0.55);
  border: 1px solid rgba(255,255,255,0.06);
  padding: 6px 10px;
}
summary { color: #e5e9ff !important; font-weight: 650; }

.stTabs [data-baseweb="tab-list"] { gap: 8px; }
.stTabs [data-baseweb="tab"] {
  background-color: #080b16;
  border-radius: 999px;
  padding: 0.45rem 1.0rem;
  font-size: 0.9rem;
  font-weight: 650;
  color: #c4cff5;
  border: 1px solid transparent;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
  background: linear-gradient(135deg, #ff4b4b, #ff9f43);
  color: #ffffff;
  border-color: rgba(255,255,255,0.16);
}
.stTabs [data-baseweb="tab-highlight"] { background: transparent !important; border-bottom: none !important; }

.stButton button {
  border-radius: 999px;
  padding: 0.4rem 0.9rem;
  font-size: 0.85rem;
  border: none;
  background: linear-gradient(135deg, #ff4b4b, #ff9f43);
  color: #ffffff;
}
.stTextInput > div > div input {
  background-color: #111522 !important;
  border-radius: 8px !important;
  border: 1px solid rgba(255,255,255,0.05) !important;
  color: #e5e9ff !important;
}
.hero { position: relative; overflow: hidden; border-radius: 16px; margin-bottom: 18px; }
.hero-bg { width: 100%; height: 280px; object-fit: cover; filter: brightness(0.32) blur(1px); transform: scale(1.02); }
.hero-overlay { position: absolute; inset: 0; padding: 32px 40px; display: flex; flex-direction: column; justify-content: center; }
.hero-title { font-size: 34px; font-weight: 680; letter-spacing: .03em; }
.hero-sub { font-size: 14px; max-width: 900px; line-height: 1.6; }
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
       Showing trending <b>News &amp; Politics</b> videos in Canada (YouTube region <b>CA</b>).
       View counts shown are <b>global</b>; the YouTube Data API does not expose Canada-only
       viewership, so rankings follow the CA trending chart.
       <br><br>
       The <span style="color:#ffb347;">🔥 Hot (last 8 hours)</span> section looks only at
       videos uploaded in the last 8 hours and ranks them by <b>views per hour since upload</b>.
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

        short_desc = truncate_description_chars(row["description"], max_chars=200)

        badge = (" ⭐" if rank <= 3 else "") + (" 🔥" if views >= 1_000_000 else "")
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
       <div style="margin-top:4px;color:#c4c9ea;">
         👁 {views_str} &nbsp; ⏱ {duration_str} &nbsp; 🕒 {age_str}
       </div>
       <div style="margin-top:3px;font-size:13px;color:#c4c9ea;">
         {html.escape(channel)} · {origin}
       </div>
       <div style="margin-top:8px;color:#c4c9ea;">
         {html.escape(short_desc)}
       </div>
     </div>
   </div>
 </div>
            """
        )
        st.markdown(card_html, unsafe_allow_html=True)

        # COPY-READY DROPDOWN (markdown preview + auto-copy + two-sentence description)
        with st.expander("Show copy-ready details", expanded=False):
            full_desc = truncate_description_two_sentences(row.get("description", ""), max_sentences=2)

            if origin == "Canadian outlet":
                context_label = "among Canadian outlets"
            elif origin == "Non-Canadian outlet":
                context_label = "among non-Canadian outlets"
            else:
                context_label = "overall"

            copy_md = textwrap.dedent(
                f"""\
                ## {views_str} views - {channel} - Posted {age_str} - Trending #{rank} ({context_label})

                **[{title}]({url})**

                *{full_desc if full_desc else 'No description provided.'}*
                """
            ).strip()

            # Rendered markdown preview
            st.markdown(copy_md)

            # Auto-copy button
            components.html(
                f"""
                <div style="margin:6px 0 10px 0;">
                  <button
                    style="border-radius:999px;padding:8px 14px;border:none;cursor:pointer;
                           background:linear-gradient(135deg,#ff4b4b,#ff9f43);color:white;
                           font-weight:650;"
                    onclick="navigator.clipboard.writeText({json.dumps(copy_md)}).then(() => {{
                      const el = document.getElementById('copystatus_{section_key}_{row['video_id']}');
                      if (el) el.innerText = 'Copied ✅';
                      setTimeout(() => {{ if (el) el.innerText = ''; }}, 1500);
                    }});"
                  >
                    Copy to clipboard
                  </button>
                  <span id="copystatus_{section_key}_{row['video_id']}" style="margin-left:10px;color:#c4cff5;"></span>
                </div>
                """,
                height=60,
            )

            st.text_area(
                "Copy (select all):",
                copy_md,
                height=180,
                key=f"copy_area_{section_key}_{row['video_id']}",
            )
            st.code(copy_md, language="markdown")

        st.write("")


def main():
    st.set_page_config(page_title="CA YouTube News Dashboard", layout="wide")
    render_css()

    top_cols = st.columns([1, 3])
    with top_cols[0]:
        if st.button("🔄 Refresh now"):
            st.cache_data.clear()
            st.rerun()
    with top_cols[1]:
        st.caption("Data auto-refreshes roughly every 4 hours via cache TTL. Use the button to force a fresh API call.")

    df, _, fetched_at_utc = fetch_trending_videos()
    render_banner(fetched_at_utc)

    st.markdown("**Outlet filter**")
    outlet_choice = st.radio(
        "",
        ["All outlets", "Canadian outlets only", "Global (non-Canadian) outlets"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if outlet_choice == "Canadian outlets only":
        outlet_filter = "Canadian only"
    elif outlet_choice == "Global (non-Canadian) outlets":
        outlet_filter = "Global"
    else:
        outlet_filter = "All"

    search = st.text_input("Search titles/descriptions (optional)", "")
    if search.strip():
        q = search.strip().lower()
        df = df[df["title"].str.lower().str.contains(q) | df["description"].str.lower().str.contains(q)].copy()

    tab_trending, tab_shorts, tab_long, tab_24, tab_hot, tab_raw = st.tabs(
        ["🔥 Trending (Top 15)", "🎯 Shorts", "🎬 Regular videos", "🕒 Last 24 hours", "⚡ Hot (last 8 hours)", "🧾 Raw table"]
    )

    with tab_trending:
        st.markdown("### 🔥 Top 15 trending News & Politics videos (CA)")
        dft = filter_by_outlet(df.copy(), outlet_filter)
        render_video_list(dft.head(15), section_key="trend")

    with tab_shorts:
        st.markdown("### 🎯 Shorts (Top 15)")
        dfs = filter_by_outlet(df[df["is_short"]].copy(), outlet_filter)
        render_video_list(dfs.head(15), section_key="shorts")

    with tab_long:
        st.markdown("### 🎬 Regular videos (Top 15)")
        dfl = filter_by_outlet(df[~df["is_short"]].copy(), outlet_filter)
        render_video_list(dfl.head(15), section_key="long")

    with tab_24:
        st.markdown("### 🕒 Last 24 hours (Top 15 by views)")
        cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        df24 = df[df["published_at"] >= cutoff_24h].copy().sort_values("view_count", ascending=False)
        df24 = filter_by_outlet(df24, outlet_filter)
        render_video_list(df24.head(15), section_key="last24")

    with tab_hot:
        st.markdown("### 🔥 Hot News & Politics videos (last 8 hours)")
        cutoff_8h = datetime.now(timezone.utc) - timedelta(hours=8)
        df8 = df[df["published_at"] >= cutoff_8h].copy()
        df8 = filter_by_outlet(df8, outlet_filter).sort_values("views_per_hour", ascending=False)
        render_video_list(df8.head(15), section_key="hot8")

    with tab_raw:
        st.markdown("### Raw table")
        dfr = df.copy()
        dfr["published_at"] = dfr["published_at"].dt.tz_convert("US/Eastern")
        st.dataframe(
            dfr[
                ["title", "channel_title", "origin_label", "view_count", "views_per_hour", "duration_sec", "published_at", "is_short", "url"]
            ].rename(columns={"origin_label": "origin", "duration_sec": "duration_s"}),
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
