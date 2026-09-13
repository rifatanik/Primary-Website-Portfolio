# Moodle → Notion Deadline Organizer

Pulls every upcoming assignment/quiz/deadline from your Moodle instance and
syncs it into a Notion database, prioritized by urgency — so your academic
tasks live in one place you already check, instead of scattered across every
course page. Runs automatically every day via GitHub Actions.

It uses Moodle's official **Web Services API** (the same API the Moodle
mobile app uses), not screen-scraping, so it keeps working even if your
Moodle theme changes.

## How it prioritizes

- 🔴 **HIGH** — due within 3 days
- 🟡 **MEDIUM** — due within 7 days
- 🟢 **LOW** — due later (within the lookahead window, default 30 days)
- ⚠ **OVERDUE** — anything past due with an outstanding submission

Each sync also auto-marks a Notion task **Done** once Moodle stops
reporting it as outstanding (e.g. you submitted it), and never re-creates a
task you've already marked Done yourself.

## Pieces

- `agent.py` — logs into Moodle, fetches deadlines, outputs a human-readable
  report (default) or `--json` for the sync script.
- `notion_sync.py` — reads that JSON from stdin and creates/updates/closes
  pages in your Notion "Moodle Tasks" database, deduped by Moodle's event id.
- `.github/workflows/moodle-weekly.yml` — runs both daily via GitHub Actions.

## 1. Get Moodle access

If your school's Moodle uses Microsoft/Google SSO (no native Moodle
password), `MOODLE_USERNAME`/`MOODLE_PASSWORD` won't work — you need a web
service **token** instead. Two ways to get one:

- **Ask IT/your Moodle admin** to issue one for your account (Site
  administration → Users → Security keys), or
- **Grab it yourself** via the same mechanism the Moodle mobile app uses:
  while logged into Moodle in a browser, visit
  `https://<your-moodle>/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=anything123`
  with DevTools' Network tab open (Preserve log) — it'll try to redirect to
  a `moodlemobile://token=BASE64...` link. Base64-decode that value; it
  reads as `wstoken:::privatetoken`. The part before `:::` is your token.

## 2. Set up the Notion side

1. Go to [notion.so → Developer tools → Connections](https://www.notion.so/profile/integrations)
   and create a new connection (Access token, Internal).
2. Copy its **Access token** (starts with `ntn_` or `secret_`) — this is
   `NOTION_TOKEN`.
3. Open the **"Moodle Tasks"** database in Notion → `•••` menu →
   **Connections** → add your new connection, so it can actually see the
   database.
4. `NOTION_DATABASE_ID` is the 32-character id in the database's URL.

## 3. Configure and test locally (optional but recommended)

```bash
cd moodle-deadline-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with `MOODLE_URL`, `MOODLE_TOKEN`, `NOTION_TOKEN`,
`NOTION_DATABASE_ID`. Then run:

```bash
python agent.py --json | python notion_sync.py
```

You should see a line like
`Notion sync complete — created: 5, updated: 0, auto-completed: 0, skipped: 0`
and new tasks appear in Notion.

Other useful modes:

```bash
python agent.py                    # human-readable report to the terminal
python agent.py --out report.txt   # also save it to a file
python agent.py --email            # also email it (needs SMTP_* in .env)
```

## 4. Turn on the daily automation

The workflow at `.github/workflows/moodle-weekly.yml` is already wired up,
scheduled for 07:00 UTC daily. To activate it:

1. In your GitHub repo: **Settings → Secrets and variables → Actions** →
   add repository secrets:
   - `MOODLE_URL`
   - `MOODLE_TOKEN`
   - `NOTION_TOKEN`
   - `NOTION_DATABASE_ID`
2. Adjust the `cron:` line in the workflow file for a different time if you
   like (cron times are UTC).
3. Trigger it once manually from the **Actions** tab ("Run workflow") to
   confirm it works, then let it run on its own daily.

## Security notes

- Credentials live only in `.env` (gitignored) or GitHub Actions secrets —
  never commit them.
- A Moodle web service **token** is preferable to a raw password: it can be
  revoked independently by your school without changing your login.
- The Notion integration only has access to the one database you explicitly
  connect it to.
- The script only *reads* Moodle data — it never submits, modifies, or
  deletes anything there.
