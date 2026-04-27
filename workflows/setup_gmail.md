# Setup — Gmail Send (OAuth)

The Quote Detail "Send Quote" and "Send Contract + Docs" buttons send email via the
Gmail API as the authenticated Google account. This is one-time setup.

## Prerequisites

- A Google account that owns the BMDW domain mailbox you want to send from
  (e.g. `michael@blackmountaindirtworks.ca`).
- Project access to the same Google Cloud project used for Sheets sync (or
  create a new one).

## Steps

### 1. Enable the Gmail API

1. Go to https://console.cloud.google.com → select (or create) your BMDW project.
2. **APIs & Services → Library** → search "Gmail API" → **Enable**.

### 2. Configure the OAuth consent screen

1. **APIs & Services → OAuth consent screen**.
2. User type: **External** (you'll add yourself as a test user — that's fine).
3. App name: `BMDW Estimator`. Support email + developer email = your address.
4. **Scopes**: add `https://www.googleapis.com/auth/gmail.send`.
5. **Test users**: add the BMDW Google account email you'll send from.

### 3. Create the OAuth client

1. **APIs & Services → Credentials → + Create Credentials → OAuth client ID**.
2. Application type: **Desktop app**. Name: `BMDW Estimator desktop`.
3. **Download JSON**. Save it as:
   ```
   config/gmail_client_secret.json
   ```
   (Already gitignored.)

### 4. First-run consent

The first time the app calls `send_email()`, it will:
1. Open a browser window automatically.
2. Ask you to sign in with the BMDW Google account.
3. Show "Google hasn't verified this app" — click **Advanced → Go to BMDW
   Estimator (unsafe)**. (Safe — this is your own app.)
4. Grant the **Send email on your behalf** scope.
5. Save a token to `config/gmail_token.json` (also gitignored).

After that, sends are silent. Token auto-refreshes.

## Verifying it works

1. Open any quote in the Quote Detail.
2. Click **Send Quote**.
3. Check the recipient's inbox — message should arrive within seconds, and the
   Event log should show `quote_sent` with `ok: true`.

If you get `Gmail not configured`, the client_secret.json is missing or in the
wrong place. If it fails after consent, check the Render env (Render does NOT
support the browser consent flow — see Production note below).

## Production (Render) note

The browser-consent flow only works locally. For Render:
1. Run `send_email()` once locally to generate `config/gmail_token.json`.
2. Render's environment is read-only at runtime; commit a deploy hook that
   stages `gmail_token.json` from a Render secret file at startup, OR keep
   the send feature local-only and use Render strictly for the on-site
   capture flow.

A simpler option: don't auto-send from Render. Render is for capture + review
on iPhone; sending happens at home from the laptop where the token lives.
