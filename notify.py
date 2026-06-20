"""
Send FCM push notifications:
  --type reminder   Daily training reminder (what's on today's plan)
  --type weekly     Weekly summary (km logged vs target, sent Sundays)

Usage:
    python notify.py --type reminder
    python notify.py --type weekly
"""

import os
import sys
import json
import argparse
from datetime import date, timedelta
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, messaging
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

PLAN_START = date(2026, 6, 15)

# Training plan — 49 weeks. pace in seconds/km.
PLAN = {
    1:  {"name": "Easy run-walk",         "km": 5,    "pace": 495, "type": "easy"},
    2:  {"name": "Easy continuous",        "km": 6,    "pace": 480, "type": "easy"},
    3:  {"name": "Easy run",               "km": 7,    "pace": 472, "type": "easy"},
    4:  {"name": "Recovery long",          "km": 8,    "pace": 465, "type": "long"},
    5:  {"name": "Long run",               "km": 14,   "pace": 435, "type": "long"},
    6:  {"name": "Long run",               "km": 16,   "pace": 435, "type": "long"},
    7:  {"name": "Long run",               "km": 18,   "pace": 435, "type": "long"},
    8:  {"name": "Recovery long",          "km": 14,   "pace": 440, "type": "long"},
    9:  {"name": "Long run",               "km": 20,   "pace": 440, "type": "long"},
    10: {"name": "Long run",               "km": 22,   "pace": 435, "type": "long"},
    11: {"name": "Long run",               "km": 24,   "pace": 435, "type": "long"},
    12: {"name": "Recovery long",          "km": 18,   "pace": 450, "type": "long"},
    13: {"name": "Long run",               "km": 25,   "pace": 435, "type": "long"},
    14: {"name": "Long run",               "km": 26,   "pace": 435, "type": "long"},
    15: {"name": "Long run",               "km": 28,   "pace": 435, "type": "long"},
    16: {"name": "Recovery long",          "km": 20,   "pace": 440, "type": "long"},
    17: {"name": "Tempo 3×10min",          "km": 13,   "pace": 405, "type": "quality"},
    18: {"name": "Long run",               "km": 30,   "pace": 435, "type": "long"},
    19: {"name": "Long run",               "km": 32,   "pace": 430, "type": "long"},
    20: {"name": "Recovery long",          "km": 22,   "pace": 440, "type": "long"},
    21: {"name": "Intervals 5×1km",        "km": 13,   "pace": 370, "type": "quality"},
    22: {"name": "Long run",               "km": 34,   "pace": 425, "type": "long"},
    23: {"name": "Long with race pace",    "km": 35,   "pace": 430, "type": "long"},
    24: {"name": "Recovery long",          "km": 24,   "pace": 440, "type": "long"},
    25: {"name": "Long run",               "km": 36,   "pace": 425, "type": "long"},
    26: {"name": "Long run",               "km": 38,   "pace": 425, "type": "long"},
    27: {"name": "Long run taper",         "km": 34,   "pace": 430, "type": "long"},
    28: {"name": "Race pace tune-up",      "km": 18,   "pace": 380, "type": "quality"},
    29: {"name": "QUALIFIER MARATHON",     "km": 42.2, "pace": 383, "type": "race"},
    30: {"name": "Post-race recovery",     "km": 12,   "pace": 480, "type": "easy"},
    31: {"name": "Rebuild long",           "km": 24,   "pace": 435, "type": "long"},
    32: {"name": "Long run",               "km": 28,   "pace": 430, "type": "long"},
    33: {"name": "Long run",               "km": 32,   "pace": 425, "type": "long"},
    34: {"name": "Recovery long",          "km": 24,   "pace": 440, "type": "long"},
    35: {"name": "Long + B2B",             "km": 34,   "pace": 430, "type": "long"},
    36: {"name": "Long + B2B",             "km": 36,   "pace": 430, "type": "long"},
    37: {"name": "Long + B2B",             "km": 38,   "pace": 430, "type": "long"},
    38: {"name": "Recovery long",          "km": 26,   "pace": 440, "type": "long"},
    39: {"name": "Long + B2B 40km",        "km": 40,   "pace": 430, "type": "long"},
    40: {"name": "Two Oceans 56km",        "km": 56,   "pace": 430, "type": "race"},
    41: {"name": "Long + B2B",             "km": 38,   "pace": 430, "type": "long"},
    42: {"name": "Recovery long",          "km": 26,   "pace": 440, "type": "long"},
    43: {"name": "PEAK — 45km",            "km": 45,   "pace": 430, "type": "long"},
    44: {"name": "Long run",               "km": 38,   "pace": 430, "type": "long"},
    45: {"name": "Long run",               "km": 30,   "pace": 430, "type": "long"},
    46: {"name": "Race pace tune-up",      "km": 22,   "pace": 404, "type": "quality"},
    47: {"name": "Taper long",             "km": 18,   "pace": 430, "type": "long"},
    48: {"name": "Taper easy",             "km": 8,    "pace": 440, "type": "easy"},
    49: {"name": "COMRADES 2027",          "km": 89,   "pace": 404, "type": "race"},
}

HR_ZONES = {
    "easy":    "Zone 2 · 116–135 bpm",
    "long":    "Zone 2 · 116–135 bpm",
    "quality": "Zone 4 · 153–165 bpm",
    "hills":   "Zone 4–5 · 153–178 bpm",
    "race":    "Zone 3–4 · 136–165 bpm",
}

RACE_PACE_WEEKS = {23, 28, 29, 39, 43, 46, 49}

PHASE_NAMES = {1: "Reactivate", 2: "Aerobic Base", 3: "Marathon Build",
               4: "Comrades Specific", 5: "Taper"}

PHASE_MAP = {
    **{w: 1 for w in range(1, 9)},
    **{w: 2 for w in range(9, 21)},
    **{w: 3 for w in range(21, 35)},
    **{w: 4 for w in range(35, 47)},
    **{w: 5 for w in range(47, 50)},
}


def plan_week(d):
    if d < PLAN_START:
        return 1
    return min(max((d - PLAN_START).days // 7 + 1, 1), 49)


def fmt_pace(secs):
    m = int(secs // 60)
    s = int(secs % 60)
    return f"{m}:{s:02d}/km"


def init_firebase():
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, {"projectId": "pulzeiq-4669c"})


def get_tokens(supabase):
    result = supabase.table("fcm_tokens").select("token").execute()
    return [row["token"] for row in result.data] if result.data else []


def send(token, title, body):
    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        token=token,
    )
    try:
        messaging.send(msg)
        print(f"  Sent to token ...{token[-8:]}")
        return True
    except Exception as e:
        print(f"  FAILED for ...{token[-8:]}: {e}")
        return False


def send_daily_reminder(supabase):
    today = date.today()
    week_num = plan_week(today)
    plan = PLAN.get(week_num)
    if not plan:
        print(f"No plan entry for week {week_num}.")
        return

    phase = PHASE_NAMES.get(PHASE_MAP.get(week_num, 1), "")
    hr = HR_ZONES.get(plan["type"], "")
    pace_str = fmt_pace(plan["pace"])
    race_flag = " ⚡ Race pace week" if week_num in RACE_PACE_WEEKS else ""

    title = f"Week {week_num} · {plan['name']}"
    body = f"{plan['km']}km · {pace_str} · {hr}{race_flag}"

    print(f"\nDaily reminder: {title} — {body}")
    tokens = get_tokens(supabase)
    if not tokens:
        print("No FCM tokens registered.")
        return
    for token in tokens:
        send(token, title, body)


def send_weekly_summary(supabase):
    today = date.today()
    week_num = plan_week(today)
    plan = PLAN.get(week_num)

    # Fetch all runs for this week
    result = supabase.table("runs").select("actual_km, session_type").eq("week", week_num).execute()
    runs = result.data or []

    total_km = sum(float(r["actual_km"] or 0) for r in runs if r.get("actual_km"))
    sessions = len(runs)
    target_km = plan["km"] if plan else 0

    if sessions == 0:
        body = f"No runs logged yet this week. Target: {target_km}km. Open the app to log."
    elif total_km >= target_km * 0.85:
        body = f"{sessions} session{'s' if sessions > 1 else ''} · {total_km:.1f}km logged · Target {target_km}km ✓"
    else:
        body = f"{sessions} session{'s' if sessions > 1 else ''} · {total_km:.1f}km of {target_km}km target. Keep going."

    title = f"Week {week_num} summary"
    print(f"\nWeekly summary: {title} — {body}")
    tokens = get_tokens(supabase)
    if not tokens:
        print("No FCM tokens registered.")
        return
    for token in tokens:
        send(token, title, body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["reminder", "weekly"], default=None)
    args = parser.parse_args()

    # Auto-detect Sunday for weekly summary if no --type given
    notify_type = args.type
    if not notify_type:
        notify_type = "weekly" if date.today().weekday() == 6 else "reminder"

    init_firebase()
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    if notify_type == "reminder":
        send_daily_reminder(supabase)
    else:
        send_weekly_summary(supabase)


if __name__ == "__main__":
    main()
