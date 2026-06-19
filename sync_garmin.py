"""
Garmin Connect → ShaSub10Comrades Supabase sync.
Pulls running activities from Garmin Connect and upserts them into the `runs` table.

Usage:
    python sync_garmin.py              # sync last 7 days
    python sync_garmin.py --days 30    # sync last 30 days
    python sync_garmin.py --date 2026-06-10  # sync a specific date
"""

import os
import sys
import argparse
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
import garminconnect
from supabase import create_client

load_dotenv()

GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

TOKEN_STORE = os.path.expanduser("~/.garminconnect")

RUNNING_TYPES = {"running", "trail_running", "treadmill_running", "track_running"}


def get_garmin_client():
    client = garminconnect.Garmin(
        email=GARMIN_EMAIL,
        password=GARMIN_PASSWORD,
        is_cn=False,
        prompt_mfa=lambda: input("Enter MFA code: "),
    )
    try:
        client.login(TOKEN_STORE)
        print("Resumed Garmin session from token cache.")
    except Exception:
        print("Logging in to Garmin Connect...")
        client.login()
        client.garth.dump(TOKEN_STORE)
        print("Login successful. Tokens saved.")
    return client


def format_pace(total_seconds, km):
    """Returns pace as mm:ss/km string."""
    if not km or km == 0:
        return None
    secs_per_km = total_seconds / km
    mins = int(secs_per_km // 60)
    secs = int(secs_per_km % 60)
    return f"{mins}:{secs:02d}"


def classify_session(activity):
    """Guess session type from distance and name."""
    name = (activity.get("activityName") or "").lower()
    km = (activity.get("distance") or 0) / 1000

    if any(w in name for w in ["long", "lsd"]):
        return "long"
    if any(w in name for w in ["interval", "speed", "track", "tempo", "quality"]):
        return "quality"
    if any(w in name for w in ["hill", "hills"]):
        return "hills"
    if any(w in name for w in ["race", "parkrun", "comrades"]):
        return "race"
    if km >= 18:
        return "long"
    return "easy"


def activity_to_run(activity):
    """Map a Garmin activity dict to the `runs` table schema."""
    start_str = activity.get("startTimeLocal") or activity.get("startTimeGMT", "")
    try:
        run_date = datetime.fromisoformat(start_str[:10]).date()
    except ValueError:
        run_date = date.today()

    distance_m = activity.get("distance") or 0
    km = round(distance_m / 1000, 2)

    duration_secs = int(activity.get("duration") or 0)

    pace_sec = None
    if km > 0 and duration_secs > 0:
        pace_sec = round(duration_secs / km, 1)

    avg_hr = activity.get("averageHR") or activity.get("avgHr")
    max_hr = activity.get("maxHR") or activity.get("maxHr")
    cadence = activity.get("averageRunningCadenceInStepsPerMinute") or activity.get("averageBikingCadenceInRevPerMinute")
    elev = activity.get("elevationGain")

    session_type = classify_session(activity)
    week_num = run_date.isocalendar()[1]
    day_name = run_date.strftime("%a")  # Mon, Tue, etc.

    return {
        "week": week_num,
        "day_of_week": day_name,
        "run_date": str(run_date),
        "session_type": session_type,
        "session_name": activity.get("activityName"),
        "actual_km": km if km > 0 else None,
        "actual_seconds": duration_secs if duration_secs > 0 else None,
        "actual_pace_sec": pace_sec,
        "avg_hr": int(avg_hr) if avg_hr else None,
        "max_hr": int(max_hr) if max_hr else None,
        "cadence": int(cadence) if cadence else None,
        "elevation_gain_m": int(elev) if elev else None,
    }


def sync(days=7, target_date=None):
    garmin = get_garmin_client()
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    if target_date:
        start = target_date
        end = target_date
    else:
        end = date.today()
        start = end - timedelta(days=days)

    print(f"\nFetching activities from {start} to {end}...")

    activities = garmin.get_activities_by_date(
        str(start), str(end), activitytype="running"
    )

    if not activities:
        print("No running activities found in that range.")
        return

    print(f"Found {len(activities)} running activities.")
    inserted = 0
    skipped = 0

    for act in activities:
        sport = (act.get("activityType", {}) or {}).get("typeKey", "")
        if sport and sport not in RUNNING_TYPES and "running" not in sport:
            skipped += 1
            continue

        row = activity_to_run(act)
        name = row.get("session_name") or "unnamed"
        km = row.get("actual_km") or 0

        existing = (
            supabase.table("runs")
            .select("id")
            .eq("run_date", row["run_date"])
            .eq("session_name", name)
            .execute()
        )

        if existing.data:
            print(f"  SKIP (already exists): {row['run_date']} — {name} ({km}km)")
            skipped += 1
            continue

        result = supabase.table("runs").insert(row).execute()
        if result.data:
            print(f"  INSERTED: {row['run_date']} — {name} ({km}km) [{row['session_type']}]")
            inserted += 1
        else:
            print(f"  ERROR inserting {name}: {result}")

    print(f"\nDone. {inserted} inserted, {skipped} skipped.")


def main():
    parser = argparse.ArgumentParser(description="Sync Garmin runs to Supabase.")
    parser.add_argument("--days", type=int, default=7, help="Number of past days to sync (default: 7)")
    parser.add_argument("--date", type=str, help="Sync a specific date (YYYY-MM-DD)")
    args = parser.parse_args()

    target = None
    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("Invalid date format. Use YYYY-MM-DD.")
            sys.exit(1)

    sync(days=args.days, target_date=target)


if __name__ == "__main__":
    main()
