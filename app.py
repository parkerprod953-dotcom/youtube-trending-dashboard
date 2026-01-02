        # ✅ CLEAN dropdown with a real copy button (exact format requested)
        with st.expander("Details", expanded=False):
            outlet_ui = st.session_state.get("outlet_choice_ui", "All outlets")
            if outlet_ui == "Canadian outlets only":
                filter_label = "Canadian outlets only"
            elif outlet_ui == "Global (non-Canadian) outlets":
                filter_label = "Global (non-Canadian) outlets"
            else:
                filter_label = "All outlets"

            # EXACT requested format:
            # Views - Channel - Posted x days/hours ago - Trending # - Category filter
            # Title:
            # Description:
            details_text = (
                f"{views_str} views - {channel} - Posted {age_str} - Trending #{rank} - {filter_label}\n"
                f"Title: {title}\n"
                f"Description: {truncate_description(row['description'] or '', max_chars=200)}"
            )

            # Show it cleanly (easy to read + easy to copy)
            st.code(details_text, language=None)

            # Real copy button (clipboard)
            safe_text = json.dumps(details_text)  # safely escapes quotes/newlines for JS
            btn_id = f"copybtn_{section_key}_{row['video_id']}"

            st.components.v1.html(
                f"""
                <div style="margin-top:8px;">
                  <button id="{btn_id}"
                    style="
                      border-radius:999px;
                      padding:8px 14px;
                      font-size:14px;
                      font-weight:650;
                      border:none;
                      cursor:pointer;
                      color:white;
                      background: linear-gradient(135deg, #ff4b4b, #ff9f43);
                      box-shadow: 0 10px 22px rgba(0,0,0,0.45);
                    ">
                    📋 Copy
                  </button>
                  <span id="{btn_id}_msg" style="margin-left:10px;color:#cfd7ff;font-size:13px;"></span>
                </div>

                <script>
                  const btn = document.getElementById("{btn_id}");
                  const msg = document.getElementById("{btn_id}_msg");
                  btn.addEventListener("click", async () => {{
                    try {{
                      await navigator.clipboard.writeText({safe_text});
                      msg.textContent = "Copied!";
                      setTimeout(() => msg.textContent = "", 1200);
                    }} catch (e) {{
                      msg.textContent = "Copy failed (browser blocked clipboard).";
                      setTimeout(() => msg.textContent = "", 2000);
                    }}
                  }});
                </script>
                """,
                height=60,
            )
