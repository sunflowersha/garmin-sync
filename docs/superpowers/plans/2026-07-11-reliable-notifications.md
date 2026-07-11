# Reliable 05:30 Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The daily training reminder fires within ~15 minutes of 05:30 SAST every day, dead FCM tokens self-delete, and a failed morning turns the workflow red.

**Architecture:** A Cloudflare Worker cron (fires within seconds) dispatches the existing `notify.yml` GitHub workflow via API at 03:30 UTC. The old GitHub `schedule:` trigger becomes a self-skipping fallback. `notify.py` gains high-urgency sends, dead-token cleanup, and a non-zero exit when nothing was delivered.

**Tech Stack:** Cloudflare Workers (plain JS, wrangler), GitHub Actions, Python 3.12 (`firebase-admin`, `supabase`), Supabase Postgres (`fcm_tokens` table).

## Global Constraints

- Repo: `C:\Users\Dell\garmin-sync`, default branch `master`, GitHub `sunflowersha/garmin-sync`.
- SAST = UTC+2, no DST. 05:30 SAST = 03:30 UTC, always.
- Do NOT touch the WIF auth setup in `notify.yml` (`google-github-actions/auth@v2` block) — keeping WIF is a design requirement.
- The `fcm_tokens` table (Supabase project `bbwgbfosozzogreklbqe`) has columns `id`, `token`, `created_at`, `updated_at`.
- Spec: `docs/superpowers/specs/2026-07-11-reliable-notifications-design.md`.
- The GitHub PAT for the worker expires **2027-07-11**; this date must appear in `worker/wrangler.toml` comments.

---

### Task 1: Harden notify.py (urgency, dead-token cleanup, loud failure)

**Files:**
- Modify: `notify.py` (functions `send`, `send_daily_reminder`, `send_weekly_summary`, `main`)
- Test: `tests/test_notify.py` (new file, new directory)

**Interfaces:**
- Produces: `send(supabase, token, title, body) -> bool` (was `send(token, title, body)`); `send_daily_reminder(supabase, plan) -> int` and `send_weekly_summary(supabase, plan) -> int` now return the number of successful sends; `main()` exits 1 when that count is 0.

- [ ] **Step 1: Ensure Python deps are installed locally**

Run: `cd /c/Users/Dell/garmin-sync && pip install -r requirements.txt`
Expected: firebase-admin, supabase, python-dotenv installed (or already satisfied).

- [ ] **Step 2: Write the failing tests**

Create `tests/test_notify.py`:

```python
"""Unit tests for notify.py send hardening. All network calls are mocked."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import notify  # noqa: E402

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PLAN_STUB = {
    "planStart": "2026-06-15",
    "weeks": [
        {
            "phaseName": "Base",
            "racePace": None,
            "weekRunKm": 17,
            "days": {d: {"label": "Easy 5km", "zone": "Z2"} for d in DAYS},
        }
    ] * 49,
}


class SendTests(unittest.TestCase):
    def test_message_has_high_urgency(self):
        captured = {}
        supabase = mock.MagicMock()
        with mock.patch.object(notify.messaging, "send",
                               side_effect=lambda m: captured.update(msg=m)):
            ok = notify.send(supabase, "tok-alive", "title", "body")
        self.assertTrue(ok)
        self.assertEqual(captured["msg"].webpush.headers["Urgency"], "high")

    def test_dead_token_is_deleted(self):
        supabase = mock.MagicMock()
        err = notify.messaging.UnregisteredError("token gone")
        with mock.patch.object(notify.messaging, "send", side_effect=err):
            ok = notify.send(supabase, "tok-dead", "title", "body")
        self.assertFalse(ok)
        supabase.table.assert_called_with("fcm_tokens")
        supabase.table.return_value.delete.return_value.eq.assert_called_with(
            "token", "tok-dead")

    def test_other_send_errors_do_not_delete(self):
        supabase = mock.MagicMock()
        with mock.patch.object(notify.messaging, "send",
                               side_effect=RuntimeError("transient")):
            ok = notify.send(supabase, "tok-x", "title", "body")
        self.assertFalse(ok)
        supabase.table.assert_not_called()


class CountTests(unittest.TestCase):
    def test_daily_reminder_returns_zero_without_tokens(self):
        supabase = mock.MagicMock()
        with mock.patch.object(notify, "get_tokens", return_value=[]):
            sent = notify.send_daily_reminder(supabase, PLAN_STUB)
        self.assertEqual(sent, 0)

    def test_daily_reminder_counts_successes(self):
        supabase = mock.MagicMock()
        with mock.patch.object(notify, "get_tokens", return_value=["a", "b"]), \
             mock.patch.object(notify, "send", side_effect=[True, False]):
            sent = notify.send_daily_reminder(supabase, PLAN_STUB)
        self.assertEqual(sent, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /c/Users/Dell/garmin-sync && python -m unittest tests.test_notify -v`
Expected: FAILURES/ERRORS — current `send()` takes 3 args and has no webpush config; reminder function returns `None`.

- [ ] **Step 4: Implement the hardening in notify.py**

Replace the current `send` function (lines ~74–85) with:

```python
def send(supabase, token, title, body):
    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        webpush=messaging.WebpushConfig(headers={"Urgency": "high"}),
        token=token,
    )
    try:
        messaging.send(msg)
        print(f"  Sent to token ...{token[-8:]}")
        return True
    except (messaging.UnregisteredError, exceptions.InvalidArgumentError) as e:
        # Dead or malformed token: purge it so it can't silently absorb sends.
        print(f"  DEAD token ...{token[-8:]} ({e}) — deleting from fcm_tokens")
        supabase.table("fcm_tokens").delete().eq("token", token).execute()
        return False
    except Exception as e:
        print(f"  FAILED for ...{token[-8:]}: {e}")
        return False
```

Add the import next to the existing firebase imports (line ~26):

```python
from firebase_admin import credentials, messaging, exceptions
```

In `send_daily_reminder`, replace the final loop-and-return block:

```python
    print(f"\nDaily reminder: {title} — {body}")
    tokens = get_tokens(supabase)
    if not tokens:
        print("No FCM tokens registered.")
        return 0
    return sum(1 for token in tokens if send(supabase, token, title, body))
```

In `send_weekly_summary`, same change to its final block:

```python
    title = f"Week {week_num} summary"
    print(f"\nWeekly summary: {title} — {body}")
    tokens = get_tokens(supabase)
    if not tokens:
        print("No FCM tokens registered.")
        return 0
    return sum(1 for token in tokens if send(supabase, token, title, body))
```

In `main()`, replace the final if/else with:

```python
    if notify_type == "reminder":
        sent = send_daily_reminder(supabase, plan)
    else:
        sent = send_weekly_summary(supabase, plan)

    if sent == 0:
        print("FATAL: no notification was delivered to any device")
        sys.exit(1)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /c/Users/Dell/garmin-sync && python -m unittest tests.test_notify -v`
Expected: `OK` — 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add notify.py tests/test_notify.py
git commit -m "Harden sends: Urgency high, purge dead tokens, exit 1 when nothing delivered"
```

---

### Task 2: notify.yml — auto-type dispatch input + self-skipping fallback schedule

**Files:**
- Modify: `.github/workflows/notify.yml` (whole file — full replacement below)

**Interfaces:**
- Consumes: `notify.py` from Task 1 (unchanged CLI: `python notify.py [--type reminder|weekly]`).
- Produces: `workflow_dispatch` with optional string input `type` (empty = auto-detect in notify.py). Task 4's worker dispatches with `"inputs": {}`.

- [ ] **Step 1: Replace `.github/workflows/notify.yml` with:**

```yaml
name: Send Training Notifications

on:
  schedule:
    # FALLBACK ONLY. The primary trigger is a Cloudflare Worker cron
    # (worker/) that dispatches this workflow at 03:30 UTC (05:30 SAST).
    # GitHub schedule crons start hours late, so this queues early, sleeps
    # to 03:45 UTC, then skips itself if a dispatched run already happened.
    - cron: '30 0 * * *'
  workflow_dispatch:
    inputs:
      type:
        description: 'Notification type (empty = auto: weekly on Sundays, else reminder)'
        required: false
        default: ''
        type: string

permissions:
  contents: read
  id-token: write   # Required for Workload Identity Federation OIDC
  actions: read     # Fallback guard lists today's runs

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Wait until 03:45 UTC (05:45 SAST)
        if: github.event_name == 'schedule'
        run: |
          target=$(date -u -d "today 03:45" +%s)
          now=$(date -u +%s)
          if [ "$now" -lt "$target" ]; then
            echo "Sleeping $((target - now))s until 03:45 UTC (05:45 SAST)"
            sleep $((target - now))
          else
            echo "Runner started after 03:45 UTC (GitHub delay) — continuing immediately"
          fi

      - name: Skip if the Worker-dispatched run already handled today
        if: github.event_name == 'schedule'
        id: guard
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          today=$(date -u +%Y-%m-%d)
          n=$(gh run list -R "${{ github.repository }}" --workflow notify.yml \
                --event workflow_dispatch --created ">=$today" \
                --json status,conclusion \
                --jq '[.[] | select(.conclusion == "success" or .status == "in_progress" or .status == "queued")] | length')
          echo "Found $n dispatched run(s) today"
          echo "already_sent=$([ "$n" -gt 0 ] && echo true || echo false)" >> "$GITHUB_OUTPUT"

      - uses: actions/checkout@v4
        if: steps.guard.outputs.already_sent != 'true'

      - id: auth
        if: steps.guard.outputs.already_sent != 'true'
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: 'projects/624011748994/locations/global/workloadIdentityPools/github-pool/providers/github-provider'
          service_account: 'firebase-adminsdk-fbsvc@pulzeiq-4669c.iam.gserviceaccount.com'

      - uses: actions/setup-python@v5
        if: steps.guard.outputs.already_sent != 'true'
        with:
          python-version: '3.12'

      - name: Install dependencies
        if: steps.guard.outputs.already_sent != 'true'
        run: pip install firebase-admin supabase python-dotenv

      - name: Send notification
        if: steps.guard.outputs.already_sent != 'true'
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
        run: |
          if [ -n "${{ github.event.inputs.type }}" ]; then
            python notify.py --type "${{ github.event.inputs.type }}"
          else
            python notify.py
          fi
```

Notes on why (do not deviate):
- Guard steps only run on `schedule`; for `workflow_dispatch` the `if:` on later steps compares `'' != 'true'` → they run normally.
- Fallback sleeps to 03:45 (not 03:30) so a healthy Worker run has 15 minutes to appear before the guard checks — avoids a double-send race.
- Guard counts `queued`/`in_progress` dispatched runs as "handled" so an in-flight Worker run isn't duplicated.

- [ ] **Step 2: Validate the guard query locally**

Run (from `/c/Users/Dell/garmin-sync`):
```bash
today=$(date -u +%Y-%m-%d)
gh run list -R sunflowersha/garmin-sync --workflow notify.yml \
  --event workflow_dispatch --created ">=$today" \
  --json status,conclusion \
  --jq '[.[] | select(.conclusion == "success" or .status == "in_progress" or .status == "queued")] | length'
```
Expected: a number ≥ 1 (2026-07-11 had manual dispatch test runs). Confirms the query syntax the guard step relies on.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/notify.yml
git commit -m "Make schedule a self-skipping fallback; empty dispatch type auto-detects"
```

---

### Task 3: End-to-end verification of Tasks 1–2 (real dispatch, real phone)

**Files:** none (verification only; Tasks 1–2 must be pushed first).

**Interfaces:**
- Consumes: pushed `notify.py` + `notify.yml`; Supabase `fcm_tokens` (2 live rows: PC `...PzTn2Ops`, iPhone `...BEi0FmA0`).

- [ ] **Step 1: Run unit tests, then push Tasks 1–2** (user rule: tests before every push)

```bash
cd /c/Users/Dell/garmin-sync
python -m unittest tests.test_notify -v   # expected: OK
git push
```

- [ ] **Step 2: Insert a deliberately malformed token** (will make FCM raise
  `InvalidArgumentError`, exercising the cleanup path)

SQL against Supabase project `bbwgbfosozzogreklbqe`:
```sql
insert into fcm_tokens (token) values ('garbage-token-cleanup-test');
```

- [ ] **Step 3: Dispatch a real run and wait for completion**

```bash
cd /c/Users/Dell/garmin-sync
gh workflow run notify.yml -f type=reminder
# wait for completion:
until [ "$(gh run list --workflow=notify.yml --limit 1 --json status --jq '.[0].status')" = "completed" ]; do sleep 5; done
id=$(gh run list --workflow=notify.yml --limit 1 --json databaseId --jq '.[0].databaseId')
gh run view $id --log | grep -i "sent to\|dead\|failed"
```
Expected log lines:
- `Sent to token ...PzTn2Ops` and `Sent to token ...BEi0FmA0`
- `DEAD token ...nup-test (...) — deleting from fcm_tokens`
- Run conclusion: success (≥1 delivered → exit 0).

- [ ] **Step 4: Verify the garbage row is gone**

```sql
select id, right(token, 8) as token_end from fcm_tokens order by id;
```
Expected: only the two live rows (`...PzTn2Ops`, `...BEi0FmA0`).

- [ ] **Step 5: Confirm with the user that the phone received the test notification.** (Blocking user check.)

---

### Task 4: Cloudflare Worker trigger

**Files:**
- Create: `worker/wrangler.toml`
- Create: `worker/worker.js`
- Create: `worker/.gitignore` (ignores `.dev.vars`)

**Interfaces:**
- Consumes: `workflow_dispatch` API of `notify.yml` (Task 2) with body `{"ref":"master","inputs":{}}`.
- Produces: deployed worker `comrades-notify-trigger`, cron `30 3 * * *` UTC; secret `GH_TOKEN`.

- [ ] **Step 1: Create `worker/wrangler.toml`**

```toml
# Fires at 03:30 UTC (05:30 SAST — SA has no DST) and dispatches the
# notify.yml workflow in sunflowersha/garmin-sync. GitHub's own schedule
# crons start hours late; workflow_dispatch runs start in seconds.
#
# Secret GH_TOKEN: fine-grained PAT, repo sunflowersha/garmin-sync only,
# permission Actions: read+write. EXPIRES 2027-07-11 — renew before then
# (github.com/settings/personal-access-tokens), then:
#   npx wrangler secret put GH_TOKEN
name = "comrades-notify-trigger"
main = "worker.js"
compatibility_date = "2026-07-11"

[triggers]
crons = ["30 3 * * *"]
```

- [ ] **Step 2: Create `worker/worker.js`**

```js
export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
};

async function dispatch(env, attempt = 1) {
  const res = await fetch(
    'https://api.github.com/repos/sunflowersha/garmin-sync/actions/workflows/notify.yml/dispatches',
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.GH_TOKEN}`,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'comrades-notify-trigger',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      body: JSON.stringify({ ref: 'master', inputs: {} }),
    }
  );
  if (res.status !== 204) {
    const body = await res.text();
    console.error(`dispatch attempt ${attempt} failed: ${res.status} ${body}`);
    if (attempt < 2) return dispatch(env, attempt + 1);
    throw new Error(`workflow dispatch failed after 2 attempts: ${res.status}`);
  }
  console.log('notify.yml dispatched');
}
```

- [ ] **Step 3: Create `worker/.gitignore`**

```
.dev.vars
```

- [ ] **Step 4 (USER): Create the fine-grained GitHub PAT**

Ask the user to create it at https://github.com/settings/personal-access-tokens/new with:
- Token name: `comrades-notify-trigger`
- Expiration: 1 year (expires 2027-07-11)
- Repository access: Only select repositories → `sunflowersha/garmin-sync`
- Permissions → Repository permissions → **Actions: Read and write** (nothing else)

Then have the user put it in `worker/.dev.vars` (line: `GH_TOKEN=<token>`) for local testing. Do not echo the token into the chat or shell history.

- [ ] **Step 5: Test the scheduled handler locally**

Terminal A (background): `cd /c/Users/Dell/garmin-sync/worker && npx wrangler dev --test-scheduled`
Terminal B: `curl "http://localhost:8787/__scheduled?cron=30+3+*+*+*"`
Then: `gh run list -R sunflowersha/garmin-sync --workflow notify.yml --limit 1`
Expected: a fresh `workflow_dispatch` run appears (and completes green); worker log shows `notify.yml dispatched`. Note: this sends a real notification — tell the user to expect one.

- [ ] **Step 6 (USER-assisted): Deploy**

```bash
cd /c/Users/Dell/garmin-sync/worker
npx wrangler login    # user completes browser auth if not already logged in
npx wrangler secret put GH_TOKEN   # user pastes the PAT at the prompt
npx wrangler deploy
```
Expected: deploy succeeds; output lists trigger `30 3 * * *`.
Verify secret: `npx wrangler secret list` → shows `GH_TOKEN`.

- [ ] **Step 7: Commit**

```bash
cd /c/Users/Dell/garmin-sync
git add worker/
git commit -m "Add Cloudflare Worker cron that dispatches notify.yml at 05:30 SAST"
git push
```

---

### Task 5: Soak verification (next morning)

**Files:** none.

- [ ] **Step 1: After the next 05:30 SAST, check both runs:**

```bash
gh run list -R sunflowersha/garmin-sync --workflow notify.yml --limit 3 --json event,status,conclusion,createdAt
```
Expected:
- A `workflow_dispatch` run created ≈03:30 UTC, conclusion success (the Worker).
- A `schedule` run that concluded success with the send steps **skipped** (the fallback guard) — inspect with `gh run view <id>` and confirm the skip message.

- [ ] **Step 2: Confirm with the user the reminder arrived ≈05:30 SAST on the phone.**

- [ ] **Step 3: Update the project memory file** (`C:\Users\Dell\.claude\projects\C--Users-Dell\memory\project_comrades_app.md`): notifications now triggered by Cloudflare Worker `comrades-notify-trigger` at 05:30 SAST; GitHub schedule is a self-skipping fallback (05:45); PAT expires 2027-07-11.
