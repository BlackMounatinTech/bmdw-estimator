# Workflow — Set up Google Sheets sync

This is a one-time setup. After it's done, the app writes the Job Ledger and Customer Roster automatically every time a job event happens.

## What you'll end up with

1. A Google Sheet called something like **"BMDW Job Ledger"** in your BMDW Google account.
2. Two tabs in that sheet: `Job Ledger` and `Customer Roster`.
3. A "service account" — basically a robot Google identity the app uses to write to the sheet.
4. The app pushing real rows in real time.

## Step 1 — Make the Sheet

1. Sign into your BMDW Google account.
2. Open Google Sheets → **+ Blank**.
3. Rename the sheet **"BMDW Job Ledger"** (top left).
4. Rename the default tab **"Job Ledger"** (right-click the tab → Rename).
5. Add a second tab → rename it **"Customer Roster"**.
6. Copy the **Sheet ID** out of the URL. The URL looks like:
   `https://docs.google.com/spreadsheets/d/`**`1aBcDeFgHiJk...XYZ`**`/edit#gid=0`
   The ID is the long string between `/d/` and `/edit`.
7. Paste it into [config/company.json](../config/company.json) under `google_sheet_id`.

## Step 2 — Create a service account in Google Cloud

A service account is a non-human Google identity the app uses to write to your sheet. You only do this once.

1. Go to https://console.cloud.google.com/ — sign in with your BMDW Google account.
2. Top bar → project dropdown → **New Project**. Name it **"BMDW Estimator"**. Create.
3. Make sure that project is selected in the top bar.
4. Left menu → **APIs & Services → Library**. Search **Google Sheets API** → Enable. Search **Gmail API** → Enable (we'll use this later for email send).
5. Left menu → **APIs & Services → Credentials**.
6. **+ Create Credentials → Service account.**
   - Name: `bmdw-estimator-app`.
   - Skip the optional steps (no role needed) and **Done**.
7. You'll be back on the Credentials page. Click the new service account email (looks like `bmdw-estimator-app@bmdw-estimator.iam.gserviceaccount.com`) — copy that email address.
8. On the service account page, **Keys** tab → **Add Key → Create new key → JSON → Create**. A JSON file downloads.
9. Move that downloaded file to [config/google_service_account.json](../config/) (the `config/` folder in this project). Rename if needed.

## Step 3 — Share the Sheet with the service account

1. Open your BMDW Job Ledger sheet.
2. Top right → **Share**.
3. Paste the service account email (the `…@…iam.gserviceaccount.com` one) into the share box.
4. Set permission to **Editor**. Untick "Notify people" (the bot doesn't read email).
5. **Share**.

## Step 4 — Test it

In the app, open any quote → **Sync Sheets now**. You should see "Pushed N quotes, M customers." Open the sheet in your browser — fresh rows.

## What's stored where

- **`config/google_service_account.json`** — the bot's identity. **GITIGNORED.** Never commit. If it leaks, anyone with it can edit your sheet (but only that one sheet).
- **`config/company.json` → `google_sheet_id`** — the ID of your sheet. Safe to commit.

## Troubleshooting

- **"Google service account JSON not found"** → file is missing or named wrong. Must be exactly `config/google_service_account.json`.
- **Quota / permission errors** → did you share the sheet with the service account email (Step 3)? It needs Editor access on that specific sheet.
- **Tab not found** → tab names must match exactly: `Job Ledger` and `Customer Roster` (capital L, capital R, single space).
