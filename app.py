import streamlit as st
import streamlit.components.v1 as components
import sqlite3
import json
import re
import os
from datetime import date, datetime, time as dt_time, timedelta
from PIL import Image
import google.generativeai as genai

# =========================================================
# APP CONFIG
# =========================================================
st.set_page_config(
    page_title="Smart Academic Organizer",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_NAME = "academic_organizer.db"
UPLOAD_DIR = "uploaded_notes"

os.makedirs(UPLOAD_DIR, exist_ok=True)

# FIXED GEMINI API KEY (Backend secured)
FIXED_GEMINI_API_KEY = "AQ.Ab8RN6K4ZjesnMW6RHh7j5AUKTkIq8t8XGnRR7rWfQbqDLyRIQ"

INTERVAL_OPTIONS = {
    "Every 15 mins": 15,
    "Every 30 mins": 30,
    "Every 1 hour": 60,
    "Every 3 hours": 180,
    "Every 6 hours": 360,
    "Daily": 1440,
}
REVERSE_INTERVAL = {v: k for k, v in INTERVAL_OPTIONS.items()}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


# =========================================================
# DATABASE LAYER
# =========================================================
def get_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_str TEXT NOT NULL,
            institute_name TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            UNIQUE(date_str, institute_name, subject_name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id INTEGER NOT NULL,
            task TEXT,
            due_date TEXT,
            due_time TEXT,
            interval_minutes INTEGER,
            is_done INTEGER DEFAULT 0,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE
        )
    """)

    try:
        cur.execute("ALTER TABLE reminders ADD COLUMN last_notified_at TEXT")
    except sqlite3.OperationalError:
        pass

    cur.execute("""
        CREATE TABLE IF NOT EXISTS descriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id INTEGER NOT NULL,
            content TEXT,
            timestamp TEXT,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id INTEGER NOT NULL,
            file_name TEXT,
            file_path TEXT,
            FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


# --------------------- FOLDER CRUD ---------------------
def create_folder(date_str, institute_name, subject_name):
    institute_name = institute_name.strip() if institute_name.strip() else "Bennett University"
    subject_name = subject_name.strip()

    if not subject_name:
        return False, "Subject name cannot be empty."

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT OR IGNORE INTO folders (date_str, institute_name, subject_name)
            VALUES (?, ?, ?)
        """, (date_str, institute_name, subject_name))
        conn.commit()
        return True, "Folder created successfully."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_all_folders():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, date_str, institute_name, subject_name
        FROM folders
        ORDER BY date_str DESC, institute_name ASC, subject_name ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_folder(folder_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT file_path FROM uploads WHERE folder_id = ?", (folder_id,))
    for (file_path,) in cur.fetchall():
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    cur.execute("DELETE FROM reminders WHERE folder_id = ?", (folder_id,))
    cur.execute("DELETE FROM descriptions WHERE folder_id = ?", (folder_id,))
    cur.execute("DELETE FROM uploads WHERE folder_id = ?", (folder_id,))
    cur.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    conn.commit()
    conn.close()


# --------------------- REMINDERS CRUD ---------------------
def add_reminder(folder_id, task, due_date, due_time, interval_minutes):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO reminders (folder_id, task, due_date, due_time, interval_minutes, is_done, last_notified_at)
        VALUES (?, ?, ?, ?, ?, 0, NULL)
    """, (folder_id, task, due_date, due_time, interval_minutes))
    conn.commit()
    conn.close()


def get_reminders(folder_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, task, due_date, due_time, interval_minutes, is_done
        FROM reminders
        WHERE folder_id = ?
        ORDER BY is_done ASC, due_date ASC, due_time ASC
    """, (folder_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def toggle_reminder_done(reminder_id, is_done):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE reminders SET is_done = ? WHERE id = ?", (1 if is_done else 0, reminder_id))
    conn.commit()
    conn.close()


def delete_reminder(reminder_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()


def update_last_notified(reminder_id, timestamp_str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE reminders SET last_notified_at = ? WHERE id = ?", (timestamp_str, reminder_id))
    conn.commit()
    conn.close()


def get_active_reminders_with_info():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.id, r.task, r.due_date, r.due_time, r.interval_minutes, r.is_done, r.last_notified_at,
               f.id, f.subject_name, f.institute_name, f.date_str
        FROM reminders r
        JOIN folders f ON r.folder_id = f.id
        WHERE r.is_done = 0
        ORDER BY r.due_date ASC, r.due_time ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_reminder_counts():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM reminders WHERE is_done = 0")
    pending = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM reminders WHERE is_done = 1")
    completed = cur.fetchone()[0]
    conn.close()
    return pending, completed


def folder_has_overdue_reminder(folder_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT due_date, due_time FROM reminders WHERE folder_id = ? AND is_done = 0", (folder_id,))
    rows = cur.fetchall()
    conn.close()
    now = datetime.now()
    for due_date_str, due_time_str in rows:
        dt = parse_due_datetime(due_date_str, due_time_str)
        if dt and dt < now:
            return True
    return False


def folder_matches_keyword(folder_id, subject_name, institute_name, keyword):
    keyword = (keyword or "").lower().strip()
    if not keyword:
        return True
    if keyword in subject_name.lower() or keyword in institute_name.lower():
        return True

    conn = get_connection()
    cur = conn.cursor()
    like_pattern = f"%{keyword}%"
    cur.execute("SELECT COUNT(*) FROM reminders WHERE folder_id = ? AND LOWER(task) LIKE ?", (folder_id, like_pattern))
    if cur.fetchone()[0] > 0:
        conn.close()
        return True
    cur.execute("SELECT COUNT(*) FROM descriptions WHERE folder_id = ? AND LOWER(content) LIKE ?", (folder_id, like_pattern))
    count = cur.fetchone()[0]
    conn.close()
    return count > 0


# --------------------- DESCRIPTIONS CRUD ---------------------
def add_description(folder_id, content, timestamp):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO descriptions (folder_id, content, timestamp)
        VALUES (?, ?, ?)
    """, (folder_id, content, timestamp))
    conn.commit()
    conn.close()


def get_descriptions(folder_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, content, timestamp
        FROM descriptions
        WHERE folder_id = ?
        ORDER BY timestamp DESC
    """, (folder_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_description(desc_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM descriptions WHERE id = ?", (desc_id,))
    conn.commit()
    conn.close()


# --------------------- UPLOADS CRUD ---------------------
def add_upload(folder_id, file_name, file_path):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO uploads (folder_id, file_name, file_path)
        VALUES (?, ?, ?)
    """, (folder_id, file_name, file_path))
    conn.commit()
    conn.close()


def get_uploads(folder_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, file_name, file_path
        FROM uploads
        WHERE folder_id = ?
        ORDER BY id DESC
    """, (folder_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_upload(upload_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT file_path FROM uploads WHERE id = ?", (upload_id,))
    row = cur.fetchone()
    if row:
        file_path = row[0]
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
    cur.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    conn.commit()
    conn.close()


# =========================================================
# NOTIFICATION / ALERT ENGINE HELPERS
# =========================================================
def parse_due_datetime(due_date_str, due_time_str):
    try:
        return datetime.strptime(f"{due_date_str} {due_time_str}", "%Y-%m-%d %H:%M")
    except Exception:
        return None


def is_interval_triggered(last_notified_at, interval_minutes):
    if not last_notified_at:
        return True
    try:
        last_dt = datetime.strptime(last_notified_at, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return True
    elapsed_minutes = (datetime.now() - last_dt).total_seconds() / 60.0
    return elapsed_minutes >= interval_minutes


def get_due_status(due_dt):
    if due_dt is None:
        return "unknown"
    now = datetime.now()
    if due_dt < now:
        return "overdue"
    elif due_dt - now <= timedelta(hours=1):
        return "approaching"
    else:
        return "upcoming"


def compute_triggered_reminders():
    active = get_active_reminders_with_info()
    triggered = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for (rid, task, due_date, due_time, interval_minutes, is_done, last_notified_at,
         folder_id, subject_name, institute_name, folder_date) in active:

        if is_interval_triggered(last_notified_at, interval_minutes):
            due_dt = parse_due_datetime(due_date, due_time)
            status = get_due_status(due_dt)
            triggered.append({
                "id": rid,
                "task": task,
                "due_date": due_date,
                "due_time": due_time,
                "interval_minutes": interval_minutes,
                "subject_name": subject_name,
                "institute_name": institute_name,
                "folder_date": folder_date,
                "status": status
            })
            update_last_notified(rid, now_str)

    return triggered


def build_notification_js(triggered_reminders):
    if not triggered_reminders:
        return ""

    items_js = json.dumps([
        {"subject": t["subject_name"], "task": t["task"], "due": f'{t["due_date"]} {t["due_time"]}'}
        for t in triggered_reminders
    ])

    html_code = f"""
    <script>
    (function() {{
        try {{
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const playBeep = (freq, startTime, duration) => {{
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.value = freq;
                gain.gain.setValueAtTime(0.0001, startTime);
                gain.gain.exponentialRampToValueAtTime(0.2, startTime + 0.05);
                gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start(startTime);
                osc.stop(startTime + duration);
            }};
            const now = ctx.currentTime;
            playBeep(880, now, 0.15);
            playBeep(1046, now + 0.2, 0.2);
        }} catch (e) {{
            console.log("Audio not supported:", e);
        }}

        const items = {items_js};
        function fireNotifications() {{
            items.forEach(function(item) {{
                try {{
                    new Notification("📚 " + item.subject + " Reminder", {{
                        body: item.task + " (Due: " + item.due + ")",
                        icon: "https://img.icons8.com/color/96/alarm-clock--v1.png"
                    }});
                }} catch (e) {{
                    console.log("Notification failed:", e);
                }}
            }});
        }}

        if (typeof Notification !== "undefined") {{
            if (Notification.permission === "granted") {{
                fireNotifications();
            }} else if (Notification.permission !== "denied") {{
                Notification.requestPermission().then(function(permission) {{
                    if (permission === "granted") {{
                        fireNotifications();
                    }}
                }});
            }}
        }}
    }})();
    </script>
    """
    return html_code


def date_matches_filter(date_str, filter_option, custom_start=None, custom_end=None):
    try:
        folder_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return True

    today = date.today()

    if filter_option == "All Dates":
        return True
    elif filter_option == "Today":
        return folder_date == today
    elif filter_option == "This Week":
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        return start_week <= folder_date <= end_week
    elif filter_option == "Next 7 Days":
        return today <= folder_date <= today + timedelta(days=7)
    elif filter_option == "Custom Range":
        if custom_start and custom_end:
            return custom_start <= folder_date <= custom_end
        return True
    return True


# =========================================================
# GEMINI VISION LOGIC
# =========================================================
def extract_json_from_text(text):
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    else:
        match2 = re.search(r"(\{.*\})", text, re.DOTALL)
        if match2:
            text = match2.group(1)
    return text


def analyze_timetable_image(image, fallback_date, institute_heading):
    genai.configure(api_key=FIXED_GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-3.8-flash")

    prompt = f"""
You are an expert timetable parser. Analyze the uploaded timetable screenshot carefully.

TASK:
1. Identify every DATE present in the image. If the image only shows a day name
   (e.g., "Monday") or no date at all, use this fallback date for ALL entries: {fallback_date}.
2. For each date, extract the list of all SUBJECT or CLASS names scheduled
   (ignore generic labels like "Lunch Break", "Recess", "Free Period" unless
   they are clearly an actual subject).
3. Clean up subject names (remove room numbers, teacher codes, extra symbols)
   but keep them human-readable (e.g., "Digital Design", "Data Structures").

STRICT OUTPUT FORMAT:
Return ONLY valid JSON (no explanations, no markdown fences) in this exact structure:

{{
  "schedule": [
    {{
      "date": "YYYY-MM-DD",
      "subjects": ["Subject 1", "Subject 2", "Subject 3"]
    }}
  ]
}}

If you cannot determine a proper date, use "{fallback_date}" as the date value.
Do not include any text outside the JSON object.
"""

    response = model.generate_content([prompt, image])
    raw_text = response.text
    cleaned = extract_json_from_text(raw_text)

    try:
        data = json.loads(cleaned)
        schedule = data.get("schedule", [])
        return schedule, None
    except json.JSONDecodeError as e:
        return None, f"Failed to parse JSON from Gemini response: {e}\n\nRaw response:\n{raw_text}"


# =========================================================
# UI HELPER: ULTRA-LUXURY HTML CARD BUILDERS
# =========================================================
def pill_for_status(status, is_done=False):
    if is_done:
        return '<span class="status-pill pill-done">✨ COMPLETED</span>'
    if status == "overdue":
        return '<span class="status-pill pill-overdue">⚡ OVERDUE</span>'
    elif status == "approaching":
        return '<span class="status-pill pill-duesoon">🔥 DUE SOON</span>'
    else:
        return '<span class="status-pill pill-upcoming">💎 UPCOMING</span>'


def build_reminder_card_html(task, due_date_str, due_time_str, interval_label, is_done, status):
    title_class = "task-title done" if is_done else "task-title"
    pill = pill_for_status(status, is_done)
    safe_task = task.replace("<", "&lt;").replace(">", "&gt;")
    html = f"""
    <div class="reminder-card">
        <div class="reminder-card-header">
            <span class="{title_class}">{safe_task}</span>
            {pill}
        </div>
        <div class="chip-row">
            <span class="chip">📅 {due_date_str}</span>
            <span class="chip">⏰ {due_time_str}</span>
            <span class="chip chip-interval">🔁 {interval_label}</span>
        </div>
    </div>
    """
    return html


def build_note_card_html(content, timestamp):
    safe_content = content.replace("<", "&lt;").replace(">", "&gt;")
    html = f"""
    <div class="note-card">
        <p class="note-content">💡 {safe_content}</p>
        <span class="note-timestamp">🕒 Recorded on: {timestamp}</span>
    </div>
    """
    return html


def build_alert_card_html(subject_name, institute_name, task, due_date, due_time, interval_label, status):
    pill = pill_for_status(status, False)
    safe_task = task.replace("<", "&lt;").replace(">", "&gt;")
    html = f"""
    <div class="reminder-card">
        <div class="reminder-card-header">
            <span class="task-title">{subject_name} <span class="muted-text">— {institute_name}</span></span>
            {pill}
        </div>
        <div class="alert-task-line">📌 {safe_task}</div>
        <div class="chip-row">
            <span class="chip">📅 {due_date}</span>
            <span class="chip">⏰ {due_time}</span>
            <span class="chip chip-interval">🔁 {interval_label}</span>
        </div>
    </div>
    """
    return html


def build_metric_cards_html(pending, completed, triggered_count):
    pulse_class = "pulse" if triggered_count > 0 else ""
    html = f"""
    <div class="metric-row">
        <div class="metric-card metric-indigo">
            <div class="metric-icon">⏳</div>
            <div class="metric-value">{pending}</div>
            <div class="metric-label">Active Focus Tasks</div>
        </div>
        <div class="metric-card metric-emerald">
            <div class="metric-icon">🎯</div>
            <div class="metric-value">{completed}</div>
            <div class="metric-label">Milestones Completed</div>
        </div>
        <div class="metric-card metric-rose {pulse_class}">
            <div class="metric-icon">⚡</div>
            <div class="metric-value">{triggered_count}</div>
            <div class="metric-label">Priority Alerts Now</div>
        </div>
    </div>
    """
    return html


# =========================================================
# GLOBAL CSS INJECTION
# =========================================================
def inject_custom_css():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');

    html, body, [class*="css"], .stMarkdown, .stText, p, span, div, label {
        font-family: 'Plus Jakarta Sans', sans-serif !important;
    }

    #MainMenu {visibility: hidden;}
    header[data-testid="stHeader"] { background: transparent; visibility: hidden; height: 0; }
    footer {visibility: hidden; height: 0;}
    div[data-testid="stDecoration"] {display: none;}
    div[data-testid="stToolbar"] {visibility: hidden; height: 0;}

    .block-container {
        padding-top: 2.5rem;
        padding-bottom: 5rem;
        max-width: 1240px;
    }

    /* Luxury Deep Obsidian Space Background */
    .stApp {
        background: radial-gradient(circle at 15% 15%, #0d1527 0%, #030712 50%, #010409 100%);
        color: #f1f5f9;
    }

    /* Floating Frosted Glass Containers & Forms */
    [data-testid="stVerticalBlockBorderWrapper"],
    [data-testid="stForm"] {
        background: rgba(15, 23, 42, 0.65) !important;
        backdrop-filter: blur(20px) !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 18px !important;
        box-shadow: 0 20px 50px rgba(0, 0, 0, 0.45) !important;
        margin-top: 1.2rem !important;
        margin-bottom: 1.8rem !important;
        padding: 1.2rem !important;
    }

    /* Custom Institute Header Card */
    .institute-card {
        background: rgba(30, 41, 59, 0.75);
        backdrop-filter: blur(20px);
        border: 1px solid rgba(99, 102, 241, 0.4);
        border-radius: 16px;
        padding: 1.2rem 1.6rem;
        margin: 1.8rem 0 1.2rem 0;
        box-shadow: 0 15px 35px rgba(0, 0, 0, 0.4);
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .institute-title {
        font-size: 1.35rem;
        font-weight: 800;
        color: #f8fafc;
        letter-spacing: -0.5px;
    }
    .institute-badge {
        background: rgba(99, 102, 241, 0.2);
        border: 1px solid rgba(99, 102, 241, 0.5);
        color: #818cf8;
        padding: 6px 14px;
        border-radius: 999px;
        font-size: 0.85rem;
        font-weight: 700;
    }

    /* Bulletproof Pill Navigation Tabs */
    .stTabs {
        margin-top: 2rem !important;
        margin-bottom: 2rem !important;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 14px;
        background: rgba(3, 7, 18, 0.9);
        padding: 14px;
        border-radius: 16px;
        border: 1px solid rgba(255, 255, 255, 0.08);
        box-shadow: inset 0 2px 12px rgba(0, 0, 0, 0.7);
        margin-bottom: 2.5rem !important;
        display: flex;
        flex-wrap: wrap;
    }
    .stTabs [data-baseweb="tab"] {
        height: 52px;
        border-radius: 12px;
        padding: 0 26px;
        color: #94a3b8;
        font-weight: 600;
        font-size: 1rem;
        background: transparent;
        transition: all 0.3s ease;
        white-space: nowrap;
        margin-bottom: 6px;
    }
    .stTabs [data-baseweb="tab"]:hover {
        color: #f1f5f9;
        background: rgba(255, 255, 255, 0.08);
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%) !important;
        color: white !important;
        box-shadow: 0 8px 25px rgba(139, 92, 246, 0.5) !important;
    }

    /* Modern Minimalist Inputs */
    .stTextInput > div > div > input,
    .stTextArea textarea,
    .stDateInput input,
    .stTimeInput input,
    .stNumberInput input {
        background: rgba(3, 7, 18, 0.6) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 12px !important;
        color: #f8fafc !important;
        padding: 0.8rem 1rem !important;
        transition: all 0.25s ease;
    }
    .stTextInput > div > div > input:focus,
    .stTextArea textarea:focus {
        border-color: #818cf8 !important;
        box-shadow: 0 0 0 3px rgba(129, 140, 248, 0.25) !important;
    }
    .stSelectbox > div > div {
        background: rgba(3, 7, 18, 0.6) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 12px !important;
    }
    .stFileUploader > div {
        background: rgba(3, 7, 18, 0.4) !important;
        border: 2px dashed rgba(129, 140, 248, 0.3) !important;
        border-radius: 16px !important;
        padding: 1.5rem !important;
        text-align: center;
    }

    /* Glowing Luxury Primary Buttons */
    div.stButton > button[kind="primary"],
    div[data-testid="stFormSubmitButton"] > button[kind="primary"] {
        background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%);
        border: none;
        color: white !important;
        border-radius: 12px;
        padding: 0.75rem 1.6rem;
        font-weight: 700;
        letter-spacing: 0.3px;
        transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        box-shadow: 0 6px 20px rgba(99, 102, 241, 0.45);
        margin-top: 1rem;
        margin-bottom: 0.8rem;
    }
    div.stButton > button[kind="primary"]:hover,
    div[data-testid="stFormSubmitButton"] > button[kind="primary"]:hover {
        transform: translateY(-3px);
        box-shadow: 0 12px 35px rgba(139, 92, 246, 0.65);
    }

    /* Secondary Luxury Buttons */
    div.stButton > button[kind="secondary"] {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.12);
        color: #e2e8f0 !important;
        border-radius: 12px;
        font-weight: 600;
        transition: all 0.25s ease;
        margin-top: 0.8rem;
    }
    div.stButton > button[kind="secondary"]:hover {
        border-color: rgba(244, 63, 94, 0.6);
        color: #fda4af !important;
        background: rgba(244, 63, 94, 0.08);
        transform: translateY(-2px);
    }

    div.stDownloadButton > button {
        background: rgba(99, 102, 241, 0.12);
        border: 1px solid rgba(99, 102, 241, 0.35);
        color: #a5b4fc !important;
        border-radius: 12px;
        font-weight: 600;
        transition: all 0.25s ease;
        margin-top: 0.8rem;
    }
    div.stDownloadButton > button:hover {
        background: rgba(99, 102, 241, 0.25);
        box-shadow: 0 0 20px rgba(99, 102, 241, 0.4);
        transform: translateY(-2px);
    }

    /* Hero Header */
    .hero-banner {
        text-align: center;
        padding: 3.5rem 1rem 2.5rem 1rem;
        background: radial-gradient(circle at center top, rgba(99, 102, 241, 0.15) 0%, transparent 70%);
        border-radius: 24px;
        margin-bottom: 2.5rem;
    }
    .hero-badge {
        display: inline-block;
        padding: 8px 22px;
        border-radius: 999px;
        background: rgba(99, 102, 241, 0.15);
        border: 1px solid rgba(99, 102, 241, 0.4);
        color: #a5b4fc;
        font-size: 0.8rem;
        font-weight: 800;
        letter-spacing: 1.2px;
        margin-bottom: 1.2rem;
        text-transform: uppercase;
        box-shadow: 0 0 25px rgba(99, 102, 241, 0.3);
    }
    .hero-title {
        font-size: 3.6rem;
        font-weight: 800;
        background: linear-gradient(135deg, #818cf8 0%, #c084fc 40%, #f472b6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin-bottom: 0.8rem;
        line-height: 1.15;
        letter-spacing: -1px;
    }
    .hero-subtitle {
        color: #94a3b8;
        font-size: 1.15rem;
        max-width: 680px;
        margin: 0 auto;
        line-height: 1.6;
        font-weight: 400;
    }

    /* Premium Metric Dashboard Cards */
    .metric-row {
        display: flex;
        gap: 1.5rem;
        margin: 2.5rem 0 2.5rem 0;
        flex-wrap: wrap;
    }
    .metric-card {
        flex: 1;
        min-width: 230px;
        background: rgba(15, 23, 42, 0.6);
        backdrop-filter: blur(16px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 18px;
        padding: 1.8rem;
        text-align: center;
        box-shadow: 0 15px 35px rgba(0, 0, 0, 0.3);
        transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .metric-card:hover {
        transform: translateY(-4px);
    }
    .metric-icon { font-size: 2.1rem; margin-bottom: 0.4rem; }
    .metric-value { font-size: 2.8rem; font-weight: 800; color: #ffffff; letter-spacing: -1px; }
    .metric-label { color: #94a3b8; font-weight: 600; font-size: 0.92rem; margin-top: 0.3rem; letter-spacing: 0.4px; }

    .metric-indigo { border-color: rgba(99, 102, 241, 0.4) !important; box-shadow: 0 0 30px rgba(99, 102, 241, 0.2); }
    .metric-emerald { border-color: rgba(16, 185, 129, 0.4) !important; box-shadow: 0 0 30px rgba(16, 185, 129, 0.2); }
    .metric-rose { border-color: rgba(244, 63, 94, 0.4) !important; box-shadow: 0 0 30px rgba(244, 63, 94, 0.2); }

    .pulse { animation: pulseGlow 1.8s infinite; }
    @keyframes pulseGlow {
        0% { box-shadow: 0 0 20px rgba(244, 63, 94, 0.4); }
        50% { box-shadow: 0 0 50px rgba(244, 63, 94, 0.85); }
        100% { box-shadow: 0 0 20px rgba(244, 63, 94, 0.4); }
    }

    /* Luxury Status Pills */
    .status-pill {
        display: inline-block;
        padding: 5px 14px;
        border-radius: 999px;
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.6px;
        text-transform: uppercase;
    }
    .pill-done { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.45); box-shadow: 0 0 15px rgba(16, 185, 129, 0.25); }
    .pill-overdue { background: rgba(244, 63, 94, 0.18); color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.45); box-shadow: 0 0 15px rgba(244, 63, 94, 0.3); }
    .pill-duesoon { background: rgba(245, 158, 11, 0.18); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.45); box-shadow: 0 0 15px rgba(245, 158, 11, 0.3); }
    .pill-upcoming { background: rgba(99, 102, 241, 0.18); color: #a5b4fc; border: 1px solid rgba(99, 102, 241, 0.45); box-shadow: 0 0 15px rgba(99, 102, 241, 0.3); }

    /* Chips & Tags */
    .chip-row { margin-top: 1rem; display: flex; gap: 10px; flex-wrap: wrap; }
    .chip {
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.09);
        padding: 6px 14px;
        border-radius: 10px;
        font-size: 0.8rem;
        color: #cbd5e1;
        font-weight: 500;
    }
    .chip-interval {
        background: rgba(236, 72, 153, 0.12);
        border-color: rgba(236, 72, 153, 0.35);
        color: #f9a8d4;
    }

    /* Cards */
    .reminder-card, .note-card {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 16px;
        padding: 1.4rem 1.6rem;
        margin-bottom: 1.2rem;
        transition: all 0.25s ease;
    }
    .reminder-card:hover, .note-card:hover {
        border-color: rgba(255, 255, 255, 0.2);
        background: rgba(255, 255, 255, 0.05);
        transform: translateY(-2px);
    }
    .reminder-card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 12px;
    }
    .task-title { font-size: 1.1rem; font-weight: 700; color: #f8fafc; }
    .task-title.done { text-decoration: line-through; color: #64748b; }
    .muted-text { color: #94a3b8; font-weight: 500; font-size: 0.95rem; }
    .alert-task-line { margin-top: 0.6rem; color: #e2e8f0; font-size: 1rem; font-weight: 500; }
    .note-content { font-size: 1.05rem; color: #f1f5f9; margin-bottom: 0.6rem; line-height: 1.6; }
    .note-timestamp { font-size: 0.8rem; color: #64748b; font-weight: 500; }
    .subject-header { font-size: 1.4rem; font-weight: 800; margin: 1.2rem 0 1.4rem 0; color: #ffffff; letter-spacing: -0.5px; }
    .section-label { font-size: 1.8rem; font-weight: 800; color: #ffffff; margin-bottom: 0.6rem; letter-spacing: -0.5px; }
    .section-caption { color: #94a3b8; margin-bottom: 2rem; font-size: 1.05rem; }

    /* Custom Scrollbar */
    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-track { background: #030712; }
    ::-webkit-scrollbar-thumb {
        background: linear-gradient(135deg, #6366f1, #8b5cf6);
        border-radius: 10px;
    }
    </style>
    """, unsafe_allow_html=True)


# =========================================================
# INITIALIZE DB & SESSION STATE
# =========================================================
init_db()

if "live_refresh" not in st.session_state:
    st.session_state.live_refresh = False

inject_custom_css()

# =========================================================
# HERO BANNER (UPDATED BADGE)
# =========================================================
st.markdown("""
<div class="hero-banner">
    <div class="hero-badge">✨ YOUR PERSONAL PRODUCTIVITY HUB</div>
    <div class="hero-title">Smart Academic Organizer</div>
    <div class="hero-subtitle">
        Your high-performance command center for timetables, live reminders, 
        intelligent notes, and secure document uploads.
    </div>
</div>
""", unsafe_allow_html=True)


# =========================================================
# TOP NOTIFICATION CENTER
# =========================================================
triggered_reminders = compute_triggered_reminders()

if triggered_reminders:
    for t in triggered_reminders:
        st.toast(f"⏰ {t['subject_name']}: {t['task']}", icon="🔔")
    components.html(build_notification_js(triggered_reminders), height=0)

pending_count, completed_count = get_reminder_counts()
st.markdown(build_metric_cards_html(pending_count, completed_count, len(triggered_reminders)), unsafe_allow_html=True)

with st.container(border=True):
    st.markdown("#### ⚡ Active Priority Alerts")
    if triggered_reminders:
        for t in triggered_reminders:
            interval_label = REVERSE_INTERVAL.get(t["interval_minutes"], f"Every {t['interval_minutes']} mins")
            card_html = build_alert_card_html(
                t["subject_name"], t["institute_name"], t["task"],
                t["due_date"], t["due_time"], interval_label, t["status"]
            )
            ac1, ac2 = st.columns([5, 1])
            with ac1:
                st.markdown(card_html, unsafe_allow_html=True)
            with ac2:
                st.write("")
                if st.button("✨ Mark Done", key=f"topdone_{t['id']}", type="primary", use_container_width=True):
                    toggle_reminder_done(t['id'], True)
                    st.rerun()
    else:
        st.info("No active priority alerts right now. You're completely up to date! 🎉")

st.write("")


# =========================================================
# TAB RENDER FUNCTIONS
# =========================================================
def render_reminders_tab(folder_id):
    st.markdown("##### ➕ Create New Task Reminder")
    with st.form(f"reminder_form_{folder_id}", clear_on_submit=True):
        r_col1, r_col2 = st.columns([2, 1])
        with r_col1:
            task_text = st.text_input("Task / Milestone Description", key=f"task_{folder_id}", placeholder="e.g., Submit Assignment 1")
        with r_col2:
            interval_label = st.selectbox("Alert Frequency", list(INTERVAL_OPTIONS.keys()), key=f"interval_{folder_id}")

        r_col3, r_col4 = st.columns(2)
        with r_col3:
            due_date_val = st.date_input("Due Date", value=date.today(), key=f"duedate_{folder_id}")
        with r_col4:
            due_time_val = st.time_input("Due Time", value=dt_time(9, 0), key=f"duetime_{folder_id}")

        if st.form_submit_button("🚀 Add Reminder", use_container_width=True, type="primary"):
            if not task_text.strip():
                st.error("⚠️ Task description cannot be empty.")
            else:
                add_reminder(folder_id, task_text.strip(), due_date_val.strftime("%Y-%m-%d"), due_time_val.strftime("%H:%M"), INTERVAL_OPTIONS[interval_label])
                st.success("✅ Reminder added successfully!")
                st.rerun()

    st.markdown("##### 📋 Scheduled Reminders")
    reminders = get_reminders(folder_id)
    if not reminders:
        st.info("No reminders configured for this subject yet.")
    else:
        for reminder_id, task, due_date_str, due_time_str, interval_minutes, is_done in reminders:
            interval_label = REVERSE_INTERVAL.get(interval_minutes, f"Every {interval_minutes} mins")
            status = get_due_status(parse_due_datetime(due_date_str, due_time_str))
            card_col, action_col = st.columns([5, 1.3])
            with card_col:
                st.markdown(build_reminder_card_html(task, due_date_str, due_time_str, interval_label, bool(is_done), status), unsafe_allow_html=True)
            with action_col:
                st.write("")
                done_checkbox = st.checkbox("Done", value=bool(is_done), key=f"done_{reminder_id}")
                if done_checkbox != bool(is_done):
                    toggle_reminder_done(reminder_id, done_checkbox)
                    st.rerun()
                if st.button("🗑️ Delete", key=f"delreminder_{reminder_id}", use_container_width=True):
                    delete_reminder(reminder_id)
                    st.rerun()


def render_descriptions_tab(folder_id):
    st.markdown("##### ➕ Record Class Insights & Notes")
    new_point = st.text_area("Write key takeaways, formulas, or lecture points...", key=f"desc_text_{folder_id}", height=120)
    if st.button("💾 Save Insight", key=f"save_desc_{folder_id}", use_container_width=True, type="primary"):
        if not new_point.strip():
            st.error("⚠️ Cannot save empty notes.")
        else:
            add_description(folder_id, new_point.strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            st.success("✅ Class insight saved!")
            st.rerun()

    st.markdown("##### 🗒️ Recorded Insights")
    descriptions = get_descriptions(folder_id)
    if not descriptions:
        st.info("No class insights recorded yet.")
    else:
        for desc_id, content, timestamp in descriptions:
            card_col, action_col = st.columns([5, 1.3])
            with card_col:
                st.markdown(build_note_card_html(content, timestamp), unsafe_allow_html=True)
            with action_col:
                st.write("")
                if st.button("🗑️ Delete", key=f"deldesc_{desc_id}", use_container_width=True):
                    delete_description(desc_id)
                    st.rerun()


def render_uploads_tab(folder_id):
    st.markdown("##### ➕ Secure File & Document Upload")
    uploaded_file = st.file_uploader("Upload board snapshots, PDFs, or notes", type=["png", "jpg", "jpeg", "pdf", "docx", "txt"], key=f"file_uploader_{folder_id}")
    if uploaded_file is not None:
        if st.button("💾 Store File securely", key=f"save_file_{folder_id}", use_container_width=True, type="primary"):
            file_path = os.path.join(UPLOAD_DIR, f"{folder_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uploaded_file.name}")
            with open(file_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            add_upload(folder_id, uploaded_file.name, file_path)
            st.success("✅ File stored successfully!")
            st.rerun()

    st.markdown("##### 📂 Attached Vault Files")
    uploads = get_uploads(folder_id)
    if not uploads:
        st.info("No files attached to this subject yet.")
    else:
        for upload_id, file_name, file_path in uploads:
            with st.container(border=True):
                st.markdown(f"**📄 {file_name}**")
                if os.path.exists(file_path) and os.path.splitext(file_name)[1].lower() in IMAGE_EXTENSIONS:
                    st.image(file_path, width=280)
                u_col1, u_col2 = st.columns(2)
                with u_col1:
                    if os.path.exists(file_path):
                        with open(file_path, "rb") as f_dl:
                            st.download_button("⬇️ Download", data=f_dl, file_name=file_name, key=f"dl_{upload_id}", use_container_width=True)
                with u_col2:
                    if st.button("🗑️ Delete", key=f"delupload_{upload_id}", use_container_width=True):
                        delete_upload(upload_id)
                        st.rerun()


def render_folder_hierarchy(search_query, date_filter_option, custom_start, custom_end):
    all_folders = get_all_folders()
    if not all_folders:
        st.info("No academic folders created yet. Use the **AI Timetable Scanner** or **Quick Manual Folder** tab to begin!")
        return

    filtered_folders = [
        f for f in all_folders
        if (folder_has_overdue_reminder(f[0]) if date_filter_option == "Overdue Reminders Only" else date_matches_filter(f[1], date_filter_option, custom_start, custom_end))
        and folder_matches_keyword(f[0], f[3], f[2], search_query)
    ]

    if not filtered_folders:
        st.warning("No folders match your current search or filter parameters.")
        return

    grouped = {}
    for folder_id, date_str, institute_name, subject_name in filtered_folders:
        grouped.setdefault(date_str, {}).setdefault(institute_name, []).append((folder_id, subject_name))

    sorted_dates = sorted(grouped.keys(), reverse=True)
    date_tabs = st.tabs([f"📅 {d}" for d in sorted_dates])

    for tab, date_str in zip(date_tabs, sorted_dates):
        with tab:
            for institute_name, subjects in grouped[date_str].items():
                st.markdown(f'''
                    <div class="institute-card">
                        <span class="institute-title">🏫 {institute_name}</span>
                        <span class="institute-badge">{len(subjects)} active subject(s)</span>
                    </div>
                ''', unsafe_allow_html=True)

                for folder_id, subject_name in subjects:
                    with st.container(border=True):
                        header_col, delete_col = st.columns([4, 1.3])
                        with header_col:
                            st.markdown(f'<div class="subject-header">📁 {subject_name}</div>', unsafe_allow_html=True)
                        with delete_col:
                            if st.button("🗑️ Delete Folder", key=f"delete_{folder_id}", use_container_width=True):
                                delete_folder(folder_id)
                                st.rerun()

                        r_tab, d_tab, u_tab = st.tabs(["⏰ Reminders", "📝 Class Insights", "📎 Vault Files"])
                        with r_tab: render_reminders_tab(folder_id)
                        with d_tab: render_descriptions_tab(folder_id)
                        with u_tab: render_uploads_tab(folder_id)


# =========================================================
# MAIN WORKSPACE NAVIGATION TABS
# =========================================================
workspace_tab, scanner_tab, manual_tab = st.tabs(
    ["🗂️ My Academic Workspace", "📸 AI Timetable Scanner", "➕ Quick Manual Folder"]
)

with workspace_tab:
    st.markdown('<div class="section-label">🔍 Intelligent Search & Exam Filter</div>', unsafe_allow_html=True)
    with st.container(border=True):
        sc_col, fl_col = st.columns([2, 1])
        with sc_col:
            search_query = st.text_input("Global Search", placeholder="Search by subject, institute, or task keyword...")
        with fl_col:
            date_filter_option = st.selectbox("Timeline Filter", ["All Dates", "Today", "This Week", "Next 7 Days", "Overdue Reminders Only"])
    st.write("")
    render_folder_hierarchy(search_query, date_filter_option, None, None)

with scanner_tab:
    st.markdown('<div class="section-label">📸 AI Timetable Vision Analyzer</div>', unsafe_allow_html=True)
    with st.container(border=True):
        col1, col2 = st.columns([1, 1])
        with col1:
            uploaded_image = st.file_uploader("Upload Timetable Screenshot", type=["png", "jpg", "jpeg"])
            if uploaded_image:
                st.image(uploaded_image, caption="Timetable Preview", use_container_width=True)
        with col2:
            institute_heading_input = st.text_input("Institute / University Heading", value="Bennett University")
            selected_date = st.date_input("Default Schedule Date", value=date.today())
            analyze_btn = st.button("🚀 Analyze & Generate Folders", type="primary", use_container_width=True)

        if analyze_btn:
            if not uploaded_image:
                st.error("⚠️ Please upload a timetable screenshot first.")
            else:
                with st.spinner("✨ Gemini AI is analyzing your timetable structure..."):
                    try:
                        schedule, error = analyze_timetable_image(
                            Image.open(uploaded_image),
                            selected_date.strftime("%Y-%m-%d"),
                            institute_heading_input
                        )
                        if error:
                            st.error(error)
                        else:
                            total = sum(
                                create_folder(entry.get("date", selected_date.strftime("%Y-%m-%d")), institute_heading_input.strip(), sub)[0]
                                for entry in schedule for sub in entry.get("subjects", [])
                            )
                            st.success(f"🎉 Success! Generated {total} structured subject folders.")
                            st.rerun()
                    except Exception as e:
                        st.error(f"Error during analysis: {e}")

with manual_tab:
    st.markdown('<div class="section-label">➕ Quick Manual Folder Creation</div>', unsafe_allow_html=True)
    with st.container(border=True):
        with st.form("manual_folder_form", clear_on_submit=True):
            mc1, mc2, mc3 = st.columns(3)
            with mc1: manual_date = st.date_input("Date", value=date.today())
            with mc2: manual_institute = st.text_input("Institute", value="Bennett University")
            with mc3: manual_subject = st.text_input("Subject Name", placeholder="e.g., Digital Design")
            if st.form_submit_button("🚀 Create Subject Folder", use_container_width=True, type="primary"):
                if manual_subject.strip():
                    create_folder(manual_date.strftime("%Y-%m-%d"), manual_institute, manual_subject)
                    st.success("✅ Folder created successfully!")
                    st.rerun()
                else:
                    st.error("⚠️ Subject name cannot be empty.")