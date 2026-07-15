# garmin-sync Repo

Companion repo to `C:\Users\Dell\Shasub10comrades` (the Comrades 2027
training app). Two jobs: daily Garmin→Supabase run sync, and FCM push
notifications for the training plan.

## Files
- `sync_garmin.py` — pulls runs from Garmin Connect into Supabase (`runs` table); GitHub Actions `daily-sync.yml`, 10:00 SAST, 7-day lookback (deduped). On failure it pushes an FCM alert to the phone (`notify.py --alert "..."`)
- `notify.py` — sends FCM push notifications; fetches the plan from https://shasub1030comrades.netlify.app/plan.json at runtime (NO embedded plan data — the app repo is the single source of truth)
- `worker/` — Cloudflare Worker `comrades-notify-trigger` (the notification scheduler, see below)
- `tests/test_notify.py` — unit tests, all network mocked; run `python -m unittest tests.test_notify` before every push
- `.github/workflows/notify.yml` — sends the notification (dispatch target + fallback)
- `.github/workflows/daily-sync.yml` — Garmin sync schedule

## Notification architecture (since 2026-07-11)

Design: `docs/superpowers/specs/2026-07-11-reliable-notifications-design.md`

```
Cloudflare Worker cron 03:30 UTC (05:30 SAST, SA has no DST)
  → GitHub API workflow_dispatch of notify.yml   (starts in seconds)
    → WIF auth → notify.py → plan.json (Netlify) → fcm_tokens (Supabase)
      → FCM v1 send (Urgency: high) → devices
```

- **Primary trigger is the Cloudflare Worker**, NOT GitHub's `schedule:` cron
  — GitHub scheduled runs start hours late. Deploy worker changes with
  `npx wrangler deploy` from `worker/`.
- **The `schedule:` trigger in notify.yml is a fallback only**: queues 00:30
  UTC, sleeps to 03:45 UTC (05:45 SAST), then exits early if a dispatched run
  already succeeded/started today. Worker dead → late notification, never none.
- `notify.py` auto-detects Sunday → weekly summary, else daily reminder.
  An explicit `--type` (or non-empty `type` dispatch input) overrides.
- Dead/invalid FCM tokens are deleted from `fcm_tokens` on send failure.
  Zero successful sends → exit 1 → red run → GitHub failure email.
- Device tokens self-heal: the app re-fetches its token on every load
  (app repo, `refreshFCMToken()` in index.html).

## Secrets & credentials
- Worker secret `GH_TOKEN`: fine-grained PAT, repo `sunflowersha/garmin-sync`
  only, permission Actions read+write. **EXPIRES 2027-07-11** — renew at
  github.com/settings/personal-access-tokens, then
  `npx wrangler secret put GH_TOKEN` from `worker/`.
- Firebase auth in Actions is Workload Identity Federation (no key files) —
  do not replace with a service-account key.
- `SUPABASE_URL` / `SUPABASE_KEY`: GitHub repo secrets.
- **Garmin auth (since 2026-07-15):** Garmin rotates DI tokens every few
  days, so a token frozen in a GitHub secret dies (sync was red 13–15 Jul).
  The live token is a Fernet-encrypted blob in Supabase `garmin_state`
  (single row, anon-RLS ok because it's ciphertext); every sync run restores
  it and persists the refreshed one back. `GARMIN_TOKEN_KEY` (GitHub secret +
  local `.env`) is the Fernet key — static, never rotates. `GARMIN_TOKEN`
  (base64 zip of `garmin_tokens.json`) is bootstrap fallback only. If auth
  ever fully breaks: mint a fresh token locally (credential login works from
  a residential IP; Garmin 429s some login strategies — later ones succeed),
  then re-bootstrap `garmin_state`. `garminconnect` is pinned (0.3.6) —
  its login/token API churns with Garmin's auth changes.
- `worker/.dev.vars` (gitignored): local copy of GH_TOKEN for
  `wrangler dev --test-scheduled`.

## Manual testing
- Send now: `gh workflow run notify.yml -f type=reminder` (or `weekly`)
- Fire the worker's scheduled handler locally:
  `npx wrangler dev --test-scheduled` then
  `curl "http://localhost:8787/__scheduled?cron=30+3+*+*+*"` (sends real
  notifications)
- Unit tests: `python -m unittest tests.test_notify -v`
