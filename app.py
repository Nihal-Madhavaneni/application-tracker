import streamlit as st
import pandas as pd
import json
import time
import os
import base64
import pickle
import tempfile
import gspread
from google import genai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# ========================
# CONFIGURATION
# ========================
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SPREADSHEET_ID = "1xxYcv_dDC1HOrF2aq5ei9X-p5j1sTzPfK0GW2KtCWdI"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

COLUMNS = ["Company", "Role", "Status", "Date", "Notes"]

# ========================
# PAGE CONFIG
# ========================
st.set_page_config(
    page_title="AI Job Tracker",
    page_icon="🗂️",
    layout="wide"
)

# ========================
# GOOGLE SHEETS CONNECTION
# ========================
@st.cache_resource
def get_sheet():
    """Connect to Google Sheets using credentials from Streamlit secrets."""

    # Load token from secrets (base64 encoded pickle)
    token_b64 = st.secrets["GOOGLE_TOKEN"]
    token_bytes = base64.b64decode(token_b64)
    creds = pickle.loads(token_bytes)

    # Refresh token if expired
    if creds and creds.expired and creds.refresh_token:
        # Write client secret to a temp file for refresh
        client_secret_json = st.secrets["GOOGLE_CLIENT_SECRET"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(client_secret_json)
            tmp_path = f.name
        try:
            creds.refresh(Request())
        finally:
            os.unlink(tmp_path)

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    worksheet = sh.sheet1

    # Add headers if sheet is empty
    if worksheet.row_count == 0 or worksheet.acell("A1").value is None:
        worksheet.append_row(COLUMNS)

    return worksheet


# ========================
# GEMINI CLIENT
# ========================
@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=GEMINI_API_KEY)


# ========================
# FUNCTIONS
# ========================
def parse_message(message, retries=3):
    """
    Extract job info from the input message using Gemini 2.5 Flash-Lite.
    Retries on 429 rate limit errors.
    """
    client = get_gemini_client()
    prompt = f"""
    Extract the following fields from this job update message:
    - Company
    - Role
    - Status (must be one of: Applied, Interview, Offer, Rejected, Cancelled, Rescheduled)
    - Date (in YYYY-MM-DD format, use today's date if not mentioned)
    - Notes (any extra details)

    Message: {message}

    Return ONLY a JSON object like this (no extra text, no markdown, no code fences):
    {{
      "Company": "",
      "Role": "",
      "Status": "",
      "Date": "",
      "Notes": ""
    }}
    """
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return text.strip()

        except Exception as e:
            error_str = str(e)
            if "429" in error_str and attempt < retries - 1:
                wait = 10 * (attempt + 1)
                st.warning(f"⏳ Rate limit hit. Waiting {wait}s before retry {attempt + 2}/{retries}...")
                time.sleep(wait)
            else:
                st.warning(f"⚠️ Gemini API error: {e}\nFalling back to mock data.")
                return json.dumps({
                    "Company": "Example Corp",
                    "Role": "Software Engineer",
                    "Status": "Applied",
                    "Date": time.strftime("%Y-%m-%d"),
                    "Notes": "Mock data — Gemini API unavailable"
                })


def load_tracker():
    """Load all rows from Google Sheet as a DataFrame."""
    worksheet = get_sheet()
    records = worksheet.get_all_records()
    if not records:
        return pd.DataFrame(columns=COLUMNS)
    return pd.DataFrame(records)


def append_row(data):
    """Append a new row to the Google Sheet."""
    worksheet = get_sheet()
    row = [data.get(col, "") for col in COLUMNS]
    worksheet.append_row(row)


def delete_row(row_index):
    """
    Delete a row from Google Sheet.
    row_index is 0-based DataFrame index; Sheet rows are 1-based with row 1 as header.
    """
    worksheet = get_sheet()
    sheet_row = row_index + 2  # +1 for header, +1 for 1-based index
    worksheet.delete_rows(sheet_row)


def get_status_color(status):
    colors = {
        "Applied":     "🔵",
        "Interview":   "🟡",
        "Offer":       "🟢",
        "Rejected":    "🔴",
        "Cancelled":   "⚫",
        "Rescheduled": "🟠",
    }
    return colors.get(status, "⚪")


# ========================
# STREAMLIT UI
# ========================
st.title("🗂️ AI Job Application Tracker")
st.caption("Powered by Gemini 2.5 Flash-Lite · Stored in Google Sheets")

st.divider()

# --- Input Section ---
st.subheader("➕ Add a Job Update")
st.markdown("Describe your update in plain English — Gemini will extract the details automatically.")

col1, col2 = st.columns([4, 1])
with col1:
    message = st.text_input(
        "Job update message",
        placeholder="e.g. Got an interview at Google for SWE role on March 20th",
        label_visibility="collapsed"
    )
with col2:
    submit = st.button("Update Tracker", use_container_width=True, type="primary")

with st.expander("💡 Example messages you can try"):
    st.markdown("""
    - *Applied to Amazon for Data Engineer position today*
    - *Interview scheduled with Meta for Product Manager on April 5th at 2pm*
    - *Got rejected from Netflix SWE role, applied last week*
    - *Offer received from Stripe for Backend Engineer, $150k base*
    - *Microsoft interview cancelled, will reschedule next week*
    """)

# --- Process Input ---
if submit:
    if not message.strip():
        st.warning("Please enter a job update message!")
    else:
        with st.spinner("🤖 Parsing with Gemini..."):
            try:
                result = parse_message(message)
                data = json.loads(result)
                append_row(data)
                st.success("✅ Tracker updated successfully!")

                cols = st.columns(5)
                for col, field in zip(cols, COLUMNS):
                    with col:
                        st.metric(label=field, value=data.get(field, "—"))

            except json.JSONDecodeError:
                st.error("❌ Could not parse Gemini's response as JSON. Try rephrasing your message.")
            except Exception as e:
                st.error(f"❌ Error: {e}")

st.divider()

# --- Tracker Table ---
st.subheader("📋 Your Applications")

df = load_tracker()

if df.empty:
    st.info("No applications tracked yet. Add your first one above!")
else:
    total = len(df)

    # Status summary
    stat_cols = st.columns(6)
    for col, status in zip(stat_cols, ["Applied", "Interview", "Offer", "Rejected", "Cancelled", "Rescheduled"]):
        count = len(df[df["Status"] == status]) if "Status" in df.columns else 0
        with col:
            st.metric(f"{get_status_color(status)} {status}", count)

    st.markdown(f"**Total: {total} application(s)**")
    st.divider()

    # Filter
    all_statuses = ["All"] + sorted(df["Status"].dropna().unique().tolist())
    selected_status = st.selectbox("Filter by Status", all_statuses)
    display_df = df if selected_status == "All" else df[df["Status"] == selected_status]

    st.dataframe(
        display_df,
        use_container_width=True,
        column_config={
            "Company": st.column_config.TextColumn("🏢 Company"),
            "Role":    st.column_config.TextColumn("💼 Role"),
            "Status":  st.column_config.TextColumn("📌 Status"),
            "Date":    st.column_config.TextColumn("📅 Date"),
            "Notes":   st.column_config.TextColumn("📝 Notes"),
        }
    )

    # Delete
    st.divider()
    st.subheader("🗑️ Delete an Entry")
    if not display_df.empty:
        row_to_delete = st.selectbox(
            "Select row to delete",
            options=display_df.index.tolist(),
            format_func=lambda i: f"[{i}] {df.loc[i, 'Company']} — {df.loc[i, 'Role']} ({df.loc[i, 'Status']})"
        )
        if st.button("Delete Selected Row", type="secondary"):
            delete_row(row_to_delete)
            st.success(f"Deleted row {row_to_delete}.")
            st.rerun()
