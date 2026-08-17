# Deploying the ADEF demo to Render.com

End goal: a public HTTPS URL like `https://adef-demo.onrender.com/` that
your colleagues can open in any browser, sign in with a demo password, and
run the 10-agent onboarding pipeline.

Total time: ~15 minutes on the first deploy, ~30 seconds on subsequent
`git push`es (auto-deploy).

---

## Step 1 · Create the GitHub repo (2 min)

1. Go to <https://github.com/new>
2. Fill in:
   - **Repository name:** `Autonomous-Data-Engineering-Factory` (or your preferred name)
   - **Description:** `Autonomous Data Engineering Factory — Cognizant Agentic Engineering Excellence Platform demo`
   - **Visibility:** Public (Render's free tier can also read private repos but public is simpler)
   - **Do NOT** initialise with a README, .gitignore, or licence — the local repo already has these.
3. Click **Create repository**.
4. Copy the HTTPS URL GitHub shows you (looks like `https://github.com/<you>/Autonomous-Data-Engineering-Factory.git`).

## Step 2 · Push the code (2 min)

Open PowerShell in the project folder:

```powershell
git remote add origin https://github.com/<you>/Autonomous-Data-Engineering-Factory.git
git push -u origin main
```

The first push will pop a browser window for GitHub authentication (Git
Credential Manager handles this automatically on Windows). Sign in.

Verify the push worked by refreshing your GitHub repo page — you should see
all the files.

## Step 3 · Connect to Render (5 min)

1. Go to <https://dashboard.render.com/register> and sign up (free — a
   Google or GitHub sign-in is the fastest).
2. On the dashboard click **New +** → **Blueprint**.
3. Click **Connect account** and grant Render read access to your GitHub.
4. Pick the `Autonomous-Data-Engineering-Factory` repo.
5. Render detects the `render.yaml` we shipped and shows a summary. Click
   **Apply**.
6. Render will start the first build (`pip install` + generate fake data +
   launch uvicorn) — this takes ~3-4 minutes.

## Step 4 · Fill in the secrets (2 min)

While the build runs, click into the newly-created service
**adef-demo** → **Environment**. You'll see five variables marked
`sync: false` — these need values:

| Variable                        | Value                                                      |
|---|---|
| `AZURE_OPENAI_ENDPOINT`         | `https://<your-resource>.openai.azure.com/`               |
| `AZURE_OPENAI_API_KEY`          | (paste your key)                                          |
| `AZURE_OPENAI_CHAT_DEPLOYMENT`  | `gpt-4.1` (or your deployment name)                       |
| `AZURE_OPENAI_API_VERSION`      | `2025-01-01-preview`                                      |
| `APP_PASSWORD`                  | Any string. Your colleagues will need this to sign in.   |

Click **Save changes**. Render will restart the service — takes ~30 seconds.

## Step 5 · Verify it's live (1 min)

Once the top-right status pill says **Live**, click the service URL. You'll
see the ADEF sign-in page. Enter your `APP_PASSWORD`, and the demo loads
with the top-right pill showing **`● LIVE · Azure OpenAI`**.

Click **▶ Start agentic onboarding** and confirm a full run completes:
plan → pipeline → dbt → profile → dq → pii (approve) → synth → docs →
review (approve) → deploy (approve) → product.

## Step 6 · Share with colleagues (30 s)

Send them two things:
- The URL: `https://adef-demo.onrender.com/` (or whatever Render assigned)
- The password from `APP_PASSWORD`

Everything from that point runs on Render — colleagues don't install anything.

---

## Ongoing operations

### Push new changes
```powershell
git add .
git commit -m "your message"
git push
```
Render auto-detects the push and redeploys within ~2 minutes.

### Watch logs
Dashboard → your service → **Logs** tab. Live tail of the uvicorn output.

### Force a redeploy
Dashboard → **Manual Deploy** → **Clear build cache & deploy**.

### Rotate the password
Dashboard → **Environment** → edit `APP_PASSWORD` → **Save changes**. The
service restarts and all existing sessions are invalidated (the session token
is regenerated on every restart).

### Rotate Azure OpenAI keys
Same as password — just edit the env var in the dashboard.

---

## Free-tier caveats

- **Sleeps after 15 min idle.** First click after a sleep takes ~30 s to wake.
  Users see a spinner, no errors. Zero cost.
- **512 MB RAM.** Adequate for the demo (measured ~300 MB peak during a run).
- **Ephemeral filesystem.** The `artifacts/` folder resets each time Render
  restarts the container. That's fine — new runs write fresh artifacts.
- **1 concurrent build.** If two colleagues start a run at the same time,
  both work in parallel (async), no queueing.

If you outgrow the free tier: **Starter** at $7/mo removes sleep and doubles
RAM. That's still tiny for a demo box.

---

## Troubleshooting

### The build fails on `pip install`
Almost always a wheel-availability issue. Check the Render build log — if a
particular package fails, pin it in `requirements.txt` and push again.

### The service starts but the demo shows "Azure OpenAI offline"
Env var wasn't saved or has a typo. Re-check the four `AZURE_OPENAI_*`
values in the dashboard. The endpoint URL should include the trailing `/`.

### Colleagues get a 502 or timeout
The instance is waking from sleep. Wait 30-45 seconds and retry. Refresh the
page.

### A run hangs at an approval gate
This is by design — the pipeline waits for the human. Click **Approve &
continue** in the modal. If the modal never appeared, check browser console
for network errors; verify the browser accepted the `adef_session` cookie.

### The password page keeps rejecting me
Passwords are case-sensitive and trimmed of leading/trailing spaces. If
you're sure it's right, try in an incognito window in case an old cookie is
stuck.

---

## Total cost estimate

- **Render:** $0 (free tier)
- **Azure OpenAI (per full onboarding run):** ~$0.05 - $0.10 on gpt-4.1
- **10 colleagues doing one run each in a week:** ~$1 total

If someone shares the URL more widely than intended and you start seeing
unexpected token spend, just rotate `APP_PASSWORD` in the Render dashboard
— all existing sessions die immediately.
