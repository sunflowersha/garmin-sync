# Reliable 05:30 Notifications — Design

**Date:** 2026-07-11
**Status:** Approved
**Goal:** The daily training reminder (and Sunday weekly summary) arrives within ~15 minutes of 05:30 SAST every day, dead push tokens self-heal, and a failed morning is loud instead of silent.

## Problem

Notifications are triggered by a GitHub Actions `schedule:` cron queued at 00:30 UTC
that sleeps until 03:30 UTC (05:30 SAST) before sending. GitHub's scheduler is
best-effort: across the last 10 scheduled runs the runner started 24 minutes to
~5 hours *after* the send target, so reminders arrived between 05:54 and 10:25.
Separately, a send to a dead FCM token reports success, so delivery failures were
invisible (root-caused and partially fixed 2026-07-11 with the app's v2.2
token-refresh-on-load).

## Decision

Keep the existing, battle-tested sender (Python `notify.py`, Workload Identity
Federation, plan.json flow) and replace only the *trigger*: a Cloudflare Worker
cron — which fires within seconds of schedule — dispatches the existing workflow
via the GitHub API. `workflow_dispatch` runs start within seconds; only
`schedule:` events are deprioritized by GitHub.

Rejected alternative: moving scheduling + sending into a Supabase Edge Function
(pg_cron). Cleaner runtime story, but it requires rewriting notify.py in Deno and
storing a long-lived Firebase service-account key as a secret, reversing the
deliberate WIF decision.

## Components

### 1. Cloudflare Worker `comrades-notify-trigger` (new, `worker/` in this repo)
- Cron trigger `30 3 * * *` UTC = 05:30 SAST (South Africa has no DST; never shifts).
- On fire: `POST /repos/sunflowersha/garmin-sync/actions/workflows/notify.yml/dispatches`
  with `{"ref":"master","inputs":{}}` — no `type` input, so Sunday auto-detection
  stays in notify.py.
- Retries once on a non-2xx response; logs the failure. If both attempts fail,
  the fallback schedule (below) still delivers.
- Secret: `GH_TOKEN`, a fine-grained PAT scoped to the garmin-sync repo only,
  permission Actions: read+write. **Expires 2027-07-11 (GitHub 1-year cap) —
  renew before then.** Set via `npx wrangler secret put GH_TOKEN`.
- Deployed manually once: `npx wrangler deploy` from `worker/`. No CI.

### 2. `notify.yml` changes
- `type` dispatch input default changes `reminder` → `''` (empty). When empty,
  run `python notify.py` with no `--type` flag so notify.py auto-detects Sunday.
  An explicit `type` still forces reminder/weekly for manual tests.
- `schedule:` trigger (00:30 UTC queue + sleep-to-03:45) is **kept as fallback**.
  Sleeping 15 minutes past the Worker's 03:30 firing avoids a double-send race
  with an in-flight dispatched run. After the sleep, it checks via the built-in
  `GITHUB_TOKEN` (actions:read)
  whether a `workflow_dispatch` run of this workflow already succeeded today
  (UTC); if so it exits 0 without sending. Worker healthy → fallback is a no-op.
  Worker dead/PAT expired → late notification instead of none.

### 3. `notify.py` hardening
- Add `webpush=messaging.WebpushConfig(headers={"Urgency": "high"})` to each
  message so iOS/Android don't defer delivery.
- On `messaging.UnregisteredError` (or equivalent invalid-token error), delete
  that row from Supabase `fcm_tokens` and log it — dead tokens can no longer
  silently absorb sends.
- Exit non-zero when zero tokens were sent successfully — including the case
  where no tokens are registered at all, since that also means nobody got a
  reminder. Partial success (≥1 delivered) remains exit 0. A red run makes
  GitHub email the account.

## Data flow (happy path)

Cloudflare cron 03:30 UTC → GitHub API dispatch → notify.yml (starts in seconds)
→ WIF auth → notify.py → fetch plan.json (Netlify) → read fcm_tokens (Supabase)
→ FCM v1 send (Urgency: high) → device.

## Error handling summary

| Failure | Outcome |
|---|---|
| CF worker dispatch fails twice | 00:30 fallback schedule sends (late but delivered) |
| PAT expired | Same as above; renew PAT (noted expiry 2027-07-11) |
| A token is dead/unregistered | Row deleted from fcm_tokens, other sends unaffected |
| All sends fail / no tokens | Run exits non-zero → GitHub failure email |
| plan.json unreachable | Existing behavior: exit 1, run red |

## Testing

1. **Worker locally:** `wrangler dev --test-scheduled`, hit the scheduled
   endpoint, confirm a workflow run appears and a notification arrives.
2. **Token cleanup:** insert a syntactically-valid-but-fake token into
   `fcm_tokens`, run a manual dispatch; verify the fake row is deleted, real
   sends succeed, phone receives.
3. **Fallback guard:** after a successful dispatched run, manually fire the
   schedule-path logic and confirm it detects today's run and skips.
4. **End-to-end soak:** confirm next real 05:30 SAST delivery on the phone.

No app (index.html) changes → plan-consistency and Playwright suites unaffected.

## Out of scope

- True delivery guarantee (phone off / Focus mode / airplane mode — no push
  system can promise this).
- Moving the Garmin sync workflow off GitHub cron (its timing is not
  user-facing; can adopt the same pattern later if desired).
