"""
Send FCM push notifications:
  --type reminder   Daily training reminder (today's actual session from the plan)
  --type weekly     Weekly summary (km logged vs weekly target, sent Sundays)

The training plan is NOT embedded here. The app repo (Shasub10comrades) is the
single source of truth: its PLAN-DATA block generates plan.json, served by
Netlify. This script fetches it at runtime, so a plan change in the app is
picked up automatically. On fetch failure we exit non-zero so the GitHub
Action surfaces the problem instead of notifying from stale data.

Usage:
    python notify.py --type reminder
    python notify.py --type weekly
"""

import os
import sys
import json
import argparse
import urllib.request
from datetime import date
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, messaging
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
PLAN_URL = os.getenv("PLAN_URL", "https://shasub10comrades.netlify.app/plan.json")

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_plan():
    try:
        with urllib.request.urlopen(PLAN_URL, timeout=30) as resp:
            return json.load(resp)
    except Exception as e:
        print(f"FATAL: could not fetch plan from {PLAN_URL}: {e}")
        sys.exit(1)


def plan_week(plan, d):
    start = date.fromisoformat(plan["planStart"])
    if d < start:
        return 1
    return min(max((d - start).days // 7 + 1, 1), len(plan["weeks"]))


def week_data(plan, week_num):
    return plan["weeks"][week_num - 1]


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


def send_daily_reminder(supabase, plan):
    today = date.today()
    week_num = plan_week(plan, today)
    week = week_data(plan, week_num)
    day_name = DAY_NAMES[today.weekday()]
    day = week["days"][day_name]

    title = f"Week {week_num} {day_name} · {week['phaseName']}"
    parts = [day["label"]]
    if day.get("zone"):
        parts.append(day["zone"])
    if week.get("racePace"):
        parts.append("⚡ " + week["racePace"])
    body = " · ".join(parts)

    print(f"\nDaily reminder: {title} — {body}")
    tokens = get_tokens(supabase)
    if not tokens:
        print("No FCM tokens registered.")
        return
    for token in tokens:
        send(token, title, body)


def send_weekly_summary(supabase, plan):
    today = date.today()
    week_num = plan_week(plan, today)
    week = week_data(plan, week_num)

    # Fetch all runs for this week
    result = supabase.table("runs").select("actual_km, session_type").eq("week", week_num).execute()
    runs = result.data or []

    total_km = sum(float(r["actual_km"] or 0) for r in runs if r.get("actual_km"))
    sessions = len(runs)
    target_km = week.get("weekRunKm") or 0

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

    plan = load_plan()
    init_firebase()
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    if notify_type == "reminder":
        send_daily_reminder(supabase, plan)
    else:
        send_weekly_summary(supabase, plan)


if __name__ == "__main__":
    main()
