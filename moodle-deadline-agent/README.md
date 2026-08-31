# Moodle Deadline Organizer Agent

A small script that logs in to your Moodle instance, pulls all upcoming
assignments/quizzes/deadlines, prioritizes them by urgency, and prints (or
emails) a weekly overview — so you get one clear list instead of hunting
through every course page.

It uses Moodle's official **Web Services API** (the same API the Moodle
mobile app uses), not screen-scraping, so it keeps working even if your
Moodle theme changes.

## How it prioritizes

- 🔴 **HIGH** — due within 3 days
- 🟡 **MEDIUM** — due within 7 days
- 🟢 **LOW** — due later (within the lookahead window, default 30 days)
- ⚠ **OVERDUE** — anything past due with an outstanding submission

## 1. Set up locally

```bash
cd moodle-deadline-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in:

- `MOODLE_URL` — e.g. `https://moodle.yourschool.edu`
- `MOODLE_USERNAME` / `MOODLE_PASSWORD` — your Moodle login
- (or) `MOODLE_TOKEN` — a pre-generated web service token, if your school
  disables password-based token issuing

Most institutions have the "Moodle Mobile" web service already enabled,
which is what `MOODLE_SERVICE=moodle_mobile_app` uses. If login fails,
ask your Moodle admin to confirm Web Services + Mobile service are
enabled for your account, or to issue you a token directly (**Site
administration → Users → Security keys**, or **Preferences → Security
keys** on some sites).

## 2. Run it

```bash
python agent.py
```

Options:

```bash
python agent.py --out report.txt   # also save the report to a file
python agent.py --email            # also email the report (needs SMTP_* in .env)
```

## 3. Run it automatically every week

You have two easy options — pick one.

### Option A: GitHub Actions (no computer needs to be on)

This repo already includes `.github/workflows/moodle-weekly.yml`, scheduled
for every Monday 07:00 UTC. To activate it:

1. In your GitHub repo, go to **Settings → Secrets and variables →
   Actions** and add these repository secrets:
   - `MOODLE_URL`, `MOODLE_USERNAME`, `MOODLE_PASSWORD` (or `MOODLE_TOKEN`)
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`,
     `EMAIL_TO` (for email delivery — e.g. a Gmail address with an
     [App Password](https://myaccount.google.com/apppasswords))
2. Adjust the `cron:` schedule in the workflow file if you want a
   different day/time (cron times are UTC).
3. That's it — GitHub will run it weekly and email you the report. You
   can also trigger it manually from the **Actions** tab
   ("Run workflow").

### Option B: A cron job on your own machine

```bash
crontab -e
```

Add a line (runs every Monday at 8am):

```
0 8 * * 1 cd /path/to/moodle-deadline-agent && /path/to/venv/bin/python agent.py --email >> agent.log 2>&1
```

## Security notes

- Credentials live only in `.env` (gitignored) or GitHub Actions secrets —
  never commit them.
- Prefer a web service **token** over your raw password if your Moodle
  admin can issue one; it can be revoked independently of your login
  password.
- The script only reads calendar/assignment data — it does not submit,
  modify, or delete anything on Moodle.
