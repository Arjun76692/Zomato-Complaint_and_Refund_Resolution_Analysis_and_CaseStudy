import streamlit as st
import pandas as pd

st.set_page_config(page_title="Zomato Review Classifier", layout="wide")
st.title("🍕 Zomato Review Classifier")
st.markdown("Classify Zomato reviews into complaint themes for analyst review.")

THEMES = [
    "late delivery or delayed order",
    "order not delivered or missing items",
    "wrong order received",
    "food quality issue (cold stale bad taste)",
    "refund delay or refund not received",
    "no response from customer support",
    "delivery partner behavior issue",
    "high pricing or extra charges",
    "payment failure or refund processing issue",
    "app crash or technical problem",
]

LABEL_MAP = {
    "late delivery or delayed order": "late_delivery",
    "order not delivered or missing items": "order_missing",
    "wrong order received": "wrong_order",
    "food quality issue (cold stale bad taste)": "food_quality",
    "refund delay or refund not received": "refund_issue",
    "no response from customer support": "no_support",
    "delivery partner behavior issue": "delivery_behavior",
    "high pricing or extra charges": "pricing_issue",
    "payment failure or refund processing issue": "payment_issue",
    "app crash or technical problem": "app_issue",
}

THEME_COLS = list(LABEL_MAP.values())

# Session state — survives Streamlit reruns
if "reviews_df" not in st.session_state:
    st.session_state["reviews_df"] = None
if "df_final" not in st.session_state:
    st.session_state["df_final"] = None

# Sidebar
st.sidebar.header("Data Source")
source = st.sidebar.radio("Choose input method", ["Upload CSV", "Pull from Play Store"])
st.sidebar.markdown("---")
st.sidebar.header("Filter Settings")
SCORE_THRESHOLD = st.sidebar.slider("Max star rating to classify", 1, 5, 3)
MIN_REVIEW_LEN  = st.sidebar.slider("Min review character length", 5, 50, 15)
NLP_THRESHOLD   = st.sidebar.slider("NLP confidence threshold", 0.50, 0.95, 0.70, 0.05)

@st.cache_resource(show_spinner=False)
def load_classifier():
    from transformers import pipeline
    return pipeline(
        "zero-shot-classification",
        model="MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33",
        device=-1,
        batch_size=8,
    )

# Data loading
if source == "Upload CSV":
    uploaded = st.sidebar.file_uploader("Upload reviews CSV", type=["csv"])
    if uploaded:
        df = pd.read_csv(uploaded)
        col_map = {}
        for col in df.columns:
            lc = col.lower().strip()
            if lc in ("content", "review", "review_text", "text", "body"):
                col_map[col] = "content"
            elif lc in ("score", "rating", "stars"):
                col_map[col] = "score"
            elif lc in ("at", "date", "review_date", "created_at"):
                col_map[col] = "at"
        df.rename(columns=col_map, inplace=True)
        if "content" not in df.columns:
            st.sidebar.error("CSV needs a column named 'content', 'review', or 'text'.")
        else:
            st.session_state["reviews_df"] = df
            st.session_state["df_final"]   = None
            st.sidebar.success(f"Loaded {len(df):,} rows")

else:
    fetch_count = st.sidebar.number_input("Number of reviews to fetch", 1000, 30000, 5000, 1000)
    if st.sidebar.button("Pull reviews from Play Store"):
        with st.spinner("Fetching reviews from Google Play…"):
            try:
                from google_play_scraper import reviews, Sort
                result, _ = reviews(
                    "com.application.zomato",
                    lang="en", country="in",
                    sort=Sort.NEWEST,
                    count=int(fetch_count),
                )
                df = pd.DataFrame(result)[["content", "score", "thumbsUpCount", "at", "appVersion"]]
                df.rename(columns={"thumbsUpCount": "ThumbsUpCount", "appVersion": "AppVersion"}, inplace=True)
                df["app"] = "Zomato"
                df["platform"] = "Playstore"
                st.session_state["reviews_df"] = df
                st.session_state["df_final"]   = None
                st.sidebar.success(f"Fetched {len(df):,} reviews")
            except Exception as e:
                st.sidebar.error(f"Fetch failed: {e}")

# Preview
reviews_df = st.session_state["reviews_df"]

if reviews_df is not None:
    st.subheader("📋 Raw Reviews Preview")
    st.dataframe(reviews_df.head(10), use_container_width=True)
    st.write(f"**Total reviews loaded:** {len(reviews_df):,}")

    if st.button("🚀 Run Classification", type="primary"):
        df_to_classify = reviews_df.copy()
        if "score" in df_to_classify.columns:
            df_to_classify = df_to_classify[df_to_classify["score"] <= SCORE_THRESHOLD]
        df_to_classify = df_to_classify[df_to_classify["content"].notna()]
        df_to_classify = df_to_classify[df_to_classify["content"].str.len() > MIN_REVIEW_LEN]
        df_to_classify = df_to_classify.reset_index(drop=True)

        st.info(f"Reviews after filtering: **{len(df_to_classify):,}**")

        if len(df_to_classify) == 0:
            st.warning("No reviews match the filter. Try relaxing the sliders.")
        else:
            with st.spinner("Loading NLP model… (first run downloads ~700 MB)"):
                try:
                    classifier = load_classifier()
                    st.success("Model loaded ✅")
                except Exception as e:
                    st.error(f"Model failed to load: {e}")
                    st.stop()

            texts      = df_to_classify["content"].tolist()
            total      = len(texts)
            batch_size = 16
            results    = []
            progress_bar = st.progress(0, text="Starting…")
            status_box   = st.empty()

            try:
                for i in range(0, total, batch_size):
                    batch   = texts[i : i + batch_size]
                    outputs = classifier(batch, candidate_labels=THEMES, multi_label=True)
                    if isinstance(outputs, dict):
                        outputs = [outputs]
                    for out in outputs:
                        pairs = sorted(zip(out["labels"], out["scores"]),
                                       key=lambda x: x[1], reverse=True)
                        tags         = {short: 0 for short in LABEL_MAP.values()}
                        count_tagged = 0
                        for label, score in pairs:
                            if score >= NLP_THRESHOLD and count_tagged < 2:
                                tags[LABEL_MAP[label]] = 1
                                count_tagged += 1
                        results.append(tags)
                    done = min(i + batch_size, total)
                    progress_bar.progress(done / total, text=f"Classifying… {done}/{total}")
                    status_box.text(f"Batch {i // batch_size + 1} done")
            except Exception as e:
                st.error(f"Classification error at row {len(results)}: {e}")
                st.stop()

            progress_bar.empty()
            status_box.empty()

            tagged   = pd.DataFrame(results)
            df_final = pd.concat([df_to_classify, tagged], axis=1)
            df_final["any_theme"]    = df_final[THEME_COLS].sum(axis=1)
            df_final["unclassified"] = (df_final["any_theme"] == 0).astype(int)

            st.session_state["df_final"] = df_final
            st.success(f"✅ Done! {len(df_final):,} reviews classified.")
            st.rerun()

# Results
if st.session_state["df_final"] is not None:
    df_final = st.session_state["df_final"]

    st.markdown("---")
    st.subheader("📊 Theme Summary")
    summary     = df_final[THEME_COLS].sum().sort_values(ascending=False)
    summary_pct = (summary / len(df_final) * 100).round(1)
    st.dataframe(pd.DataFrame({
        "Theme":                 summary.index.str.replace("_", " ").str.title(),
        "Count":                 summary.values,
        "% of filtered reviews": summary_pct.values,
    }), use_container_width=True)

    st.subheader("🔍 Classified Reviews — Analyst View")
    theme_options  = ["All"] + [c.replace("_", " ").title() for c in THEME_COLS] + ["Unclassified"]
    selected_theme = st.selectbox("Filter by theme", theme_options)

    display_df = df_final.copy()
    if selected_theme != "All":
        col = selected_theme.lower().replace(" ", "_")
        if col == "unclassified":
            display_df = display_df[display_df["unclassified"] == 1]
        else:
            display_df = display_df[display_df[col] == 1]

    display_cols = ["content"]
    if "score" in display_df.columns:
        display_cols.append("score")
    if "at" in display_df.columns:
        display_cols.append("at")
    display_cols += THEME_COLS

    st.write(f"Showing **{len(display_df):,}** reviews")
    st.dataframe(display_df[display_cols], use_container_width=True, height=500)

    st.subheader("⬇️ Download")
    st.download_button(
        "Download classified reviews as CSV",
        data=df_final.to_csv(index=False).encode("utf-8"),
        file_name="zomato_classified_reviews.csv",
        mime="text/csv",
    )

    if st.button("🔄 Reset / Start Over"):
        st.session_state["reviews_df"] = None
        st.session_state["df_final"]   = None
        st.rerun()