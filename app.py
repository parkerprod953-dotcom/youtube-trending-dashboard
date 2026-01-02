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

CANADA_TZ = pytz.timezone("Canada/Eastern")

# -----------------------------
# Helpers
# -----------------------------

def now_canada():
    return datetime.now(timezone.utc).astimezone(CANADA_TZ)


def clean_text(text):
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def time_ago(published_at):
    delta = now_canada() - published_at
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} minutes ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} hours ago"
    return f"{delta.days} days ago"


def format_views(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


# -----------------------------
# YouTube API helpers
# -----------------------------

def yt_request(endpoint, params):
    params["key"] = API_KEY
    resp = requests.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


# -----------------------------
# Rendering
# -----------------------------

def render_video_list(df, section_key):
    for idx, row in df.iterrows():
        title = clean_text(row["title"])
        channel = row["channel"]
        url = row["url"]
        views = int(row["views"])
        views_str = format_views(views)
        published_at = row["published_at"]
        age_str = time_ago(published_at)
        rank = row["rank"]
        origin = row["origin"]

        st.markdown(
            f"""
            **[{title}]({url})**  
            {views_str} views • {channel} • {age_str}
            """
        )

        # =============================
        # UPDATED COPY-READY DROPDOWN
        # =============================
        with st.expander("Show copy-ready details", expanded=False):
            full_desc = (row["description"] or "").strip()

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

                *{full_desc if full_desc else "No description provided."}*
                """
            ).strip()

            # Preview (readable)
            st.markdown(copy_md)

            # Copyable box
            st.text_area(
                "Copy (select all):",
                copy_md,
                height=180,
                key=f"copy_area_{section_key}_{row['video_id']}",
            )


# -----------------------------
# Main app
# -----------------------------

def main():
    st.set_page_config(page_title="YouTube Trending Tracker", layout="wide")
    st.title("YouTube Trending — News & Politics")

    # ---- existing logic unchanged ----
    # (API calls, dataframe building, ranking, filters, tabs, etc.)
    # ----------------------------------

    # Example render call (already in your file)
    # render_video_list(df_filtered, section_key="canada")


if __name__ == "__main__":
    main()
