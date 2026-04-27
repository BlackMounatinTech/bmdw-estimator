# Setup — Gmail Send

The Quote Detail "Send Quote" and "Send Contract + Docs" buttons send email
through your `blackmountaindirtworks@gmail.com` account. **Two ways to set
this up — pick ONE.**

---

## ✅ RECOMMENDED: SMTP via Gmail App Password (works on Render)

This is the simpler path. ~5 minutes. Works locally AND on Render. No browser
consent dance, no token files. Sends email via SMTP using an "App Password"
that Google generates for this app specifically.

### Steps

1. **Turn on 2-Step Verification** for `blackmountaindirtworks@gmail.com` if you
   haven't already. Go to https://myaccount.google.com/security → "2-Step
   Verification" → On.

2. **Generate an App Password:**
   - Go to https://myaccount.google.com/apppasswords
   - "Select app" → **Mail**, "Select device" → **Other** → name it
     `BMDW Estimator`
   - Click **Generate**
   - Google shows you a **16-character password** with spaces (e.g.
     `abcd efgh ijkl mnop`). Copy it. **You won't see it again** — if you lose
     it, just generate another.

3. **Add the env vars:**

   **Locally** (in `.env`):
   ```
   SMTP_USER=blackmountaindirtworks@gmail.com
   SMTP_PASSWORD=abcdefghijklmnop      # the 16-char app password (spaces optional)
   ```

   **On Render** (Dashboard → Environment tab):
   - `SMTP_USER` = `blackmountaindirtworks@gmail.com`
   - `SMTP_PASSWORD` = `abcdefghijklmnop`

4. **Restart / redeploy.** Open Quote Detail → footer should read:
   *"Gmail send: ✓ ready (SMTP App Password — works on Render)"*.

5. **Test send.** Open any quote → Send Quote → check inbox. Done.

### Limits

- ~500 emails per day from a regular Gmail account (way more than you need)
- ~2,000 per day from Workspace
- Attachments up to 25 MB total (your PDFs are well under)

---

## Alternative: OAuth (LOCAL MAC ONLY — won't work on Render)

Use this only if you specifically want OAuth scopes for some reason. Otherwise
SMTP is simpler.

### Steps

1. **Google Cloud Console** (https://console.cloud.google.com)
   - Select / create your BMDW project → APIs & Services → Library →
     enable **Gmail API**
   - APIs & Services → OAuth consent screen → External → fill in BMDW info →
     add scope `https://www.googleapis.com/auth/gmail.send` → add
     `blackmountaindirtworks@gmail.com` as a test user

2. **Create OAuth client:**
   - APIs & Services → Credentials → Create Credentials → **OAuth client ID**
   - Application type: **Desktop app**, name: `BMDW Estimator`
   - **Download JSON** → save it as
     `/Users/michaelmackrell/BMDW Ai Project quoting/config/gmail_client_secret.json`

3. **First run on your Mac:**
   - `python3 -m streamlit run Quoting.py`
   - Open any quote → Send Quote
   - Browser tab opens → sign in with `blackmountaindirtworks@gmail.com` →
     "Google hasn't verified this app" → Advanced → Go to BMDW Estimator (unsafe)
   - Approve the "Send email on your behalf" scope
   - Token gets cached at `config/gmail_token.json` (gitignored)

4. **Why local only.** OAuth's first-run consent needs a browser. Render is a
   headless server — no browser available. Subsequent sends from the cached
   token would work, but uploading the token securely to Render is a hassle
   (Render Secret Files on paid plans). Stick with SMTP.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Gmail send: ○ not configured` | Set the env vars and restart |
| `SMTP auth failed` | You're using your regular Gmail password — needs to be the **16-char App Password** |
| `Disabled — email isn't configured` button | Same as above — env vars not set |
| Send works locally but not on Render | You're on the OAuth path. Switch to SMTP and add `SMTP_USER` + `SMTP_PASSWORD` to Render Environment |
| Mail goes to recipient's spam | Send a couple of test emails to yourself first; reply to them. Gmail learns your sending pattern is legit. Or set up SPF/DKIM if you ever switch from `@gmail.com` to a custom BMDW domain. |
