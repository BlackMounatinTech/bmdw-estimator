# Deploy — Render + iPhone Add-to-Home-Screen

Goal: Streamlit app reachable at a private URL on Michael's iPhone, behind a
password gate, so he can capture quotes on site.

## One-time setup

### 1. Push the repo to GitHub

```bash
cd "/Users/michaelmackrell/BMDW Ai Project quoting"
git init
git add .
git commit -m "Initial BMDW estimator"
gh repo create bmdw-estimator --private --source . --push
```

(`.env`, `data/`, `config/gmail_*.json`, `config/google_service_account.json`
are all in `.gitignore` already.)

### 2. Create the Render Web Service

1. Render dashboard → **New → Web Service** → connect the GitHub repo.
2. **Runtime**: Python 3.
3. **Build command**:
   ```
   pip install -r requirements.txt
   ```
4. **Start command**:
   ```
   streamlit run Quoting.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true
   ```
5. **Plan**: Starter ($7/mo) is fine for one user.

### 3. Set environment variables

In **Environment** for the service, add:

| Key | Value |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `BMDW_APP_PASSWORD` | a long random string (you'll memorize this) |

That's it. Render will redeploy on push. First boot takes ~2 minutes.

### 4. Add to iPhone Home Screen

1. Open Safari → navigate to your Render URL (e.g. `https://bmdw-estimator.onrender.com`).
2. Enter the password to log in once.
3. Tap **Share → Add to Home Screen**. Name it "BMDW".
4. The icon now opens the app full-screen, no browser chrome.

## What works on Render

- Quoting page (capture flow + AI parser + 5-bucket entries).
- Quote Detail (review, edit, math breakdown, contract editor).
- Customers + Jobs pages.
- PDF generation (WeasyPrint requires the build pack — Render's default Python
  image includes the cairo/pango libs needed).

## What does NOT work on Render

- **Gmail send** — requires browser-based OAuth consent on first run, which a
  cloud server can't show. Run `send_email()` once locally to mint the token,
  then either commit the token via Render Secret File mechanism, or keep
  sending local-only.
- **Google Sheets sync** — works if you upload the service-account JSON as a
  Render Secret File at `/etc/secrets/google_service_account.json` and symlink
  it into `config/`.

## Cost watchout

- Render free tier sleeps the service after 15 min idle → first request takes
  20–40 seconds to wake. Use Starter ($7/mo) to avoid this on site.
- Anthropic costs are bounded by the per-quote parse — set a monthly cap on
  your Anthropic billing dashboard.
