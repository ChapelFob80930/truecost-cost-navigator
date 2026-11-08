# Deploy the backend on Vercel (with CI/CD)

What this gives you: a permanent `https://<project>.vercel.app` address for the FastAPI backend (no laptop, no tunnel), tests on
every change, a preview deployment for every pull request, and a production deployment on every push to `main`. A smoke test checks
each deployment before it counts as done.

## What runs where

| Piece | Where | Notes |
|---|---|---|
| FastAPI backend | **Vercel**, as one serverless Python function (`api/index.py`) | same code as local; settings come from Vercel environment variables |
| Web app | **Lovable** (unchanged) | only its API address changes (one constant) |
| Database | **Tiger Data** (unchanged) | reached from Vercel with `DATABASE_URL` and `PGPASSWORD` |
| Streamlit app | your laptop | it can't run on Vercel; it is a local demo and test tool |

## One-time setup (about 10 minutes)

1. **Create and link the Vercel project** (on your computer):
   ```bash
   npm install --global vercel
   vercel login
   vercel link          # choose this repository's folder; accept the defaults
   ```
   This creates `.vercel/project.json` with `orgId` and `projectId`. Do not commit it (it is already git-ignored).
2. **Project settings** (Vercel dashboard, Settings, General): Framework Preset **Other**, Root Directory `.` (the repo root), leave the build and install commands empty.
3. **Environment variables** (Settings, Environment Variables; tick Production and Preview):

   | Name | Value |
   |---|---|
   | `GEMINI_API_KEY` | your Gemini key |
   | `GEMINI_MODEL` | `gemini-3.1-flash-lite` |
   | `GEMINI_FALLBACK_MODEL` | `gemini-3.5-flash-lite` |
   | `DATABASE_URL` | the Tiger Data connection string (no password in it) |
   | `PGPASSWORD` | the Tiger password |
   | `DB_POOL_MAX` | `2` (many copies of the function can run at once) |
   | `CORS_ORIGINS` | your Lovable address(es), comma-separated, for example `https://compare-and-choose-hub.lovable.app` |

   **Leave `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` unset.** The Gmail feature keeps its sign-in state in memory, and serverless
   copies don't share memory, so it would fail intermittently. Unset, the feature stays off.
4. **GitHub repository secrets** (GitHub, Settings, Secrets and variables, Actions):

   | Secret | Where to get it |
   |---|---|
   | `VERCEL_TOKEN` | vercel.com/account/tokens, create a token |
   | `VERCEL_ORG_ID` | `orgId` in `.vercel/project.json` |
   | `VERCEL_PROJECT_ID` | `projectId` in `.vercel/project.json` |
   | `VERCEL_AUTOMATION_BYPASS_SECRET` | optional: Vercel project, Settings, Deployment Protection, Protection Bypass for Automation. Lets the smoke test reach preview deployments, which Vercel otherwise protects with a login |

   Until the first three exist, the deploy job skips itself with a notice and CI still runs.
5. **Push to `main`.** The Deploy workflow runs. Open the run's summary page for the URL and the smoke-test results.
6. **Point Lovable at it:** replace the backend address in its API client with the production URL, and publish.

## What the pipeline does

```
pull request:  CI (tests, secret scan, Vercel bundle check)  ->  preview deploy  ->  smoke test  ->  URL posted on the PR
push to main:  CI (same)                                     ->  production deploy -> smoke test (with AI guardrail checks)
```

- **Tests:** the whole backend suite, including the README architecture check and the deploy-setup checks.
- **Secret scan:** fails the build if a key, token or password is committed.
- **Vercel bundle check:** installs only `api/requirements.txt` in a fresh environment, imports the function exactly as Vercel does, calls it, confirms every data file the app reads at run time is included, and checks the size is under Vercel's limit (currently about 82 MB of 250 MB).
- **Smoke test:** `scripts/smoke_test.py` checks health, the reference data, a hospital ranking, the care timeline, the demo assistant, the live stream, CORS for the web app and the usage stats. With `--live` (production) it also checks the AI refuses rule-breaking and off-topic messages.
- Nothing is deployed unless CI passes. Dependabot opens weekly update pull requests for the dependencies and the actions.

Run the smoke test yourself against any address:
```bash
python scripts/smoke_test.py https://<project>.vercel.app --live
```

## Things to know (serverless is different from a server)

- **Time limit.** `vercel.json` sets `maxDuration` to 60 seconds, the most the free plan allows. A normal assistant run takes 4 to 20 seconds; a run where Gemini is busy and retries can take longer. A paid plan allows more.
- **Cold starts.** After a quiet period the first request takes a few seconds longer (connecting to the database, creating the usage table if needed). Hit `/health` before a demo.
- **Live stream.** The live "thinking" view uses a streamed response. Vercel's Python runtime streams, but this has not been verified on a real deployment. The smoke test reports a warning if the stream arrives all at once. The web app falls back to the normal endpoint if streaming fails.
- **Usage log.** Events are written in the background and can be dropped when a function copy is frozen right after answering. Treat the live-usage numbers as approximate.
- **Gmail** is off (see step 3). **Files** can't be saved between requests; the app doesn't need to.
- **Database connections.** Each running copy opens up to `DB_POOL_MAX` connections. Keep it at 2 so a burst of copies can't use up the database's connection limit.

## Roll back

Vercel dashboard, Deployments, open an earlier good deployment, **Promote to Production**. Or `vercel rollback`.

## Secrets

Never commit `.env`, `.vercel/`, tokens or passwords. The secret scan in CI enforces it. If a secret is ever pushed, rotate it first, then remove it.
