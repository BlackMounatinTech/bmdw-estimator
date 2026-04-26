# Setup — Anthropic API Key

The AI quick-notes parser and the AI contract narrator both call the Anthropic API.
Without a key, the parser button is disabled and contracts use the templated fallback.

## Steps

1. Go to https://console.anthropic.com → sign in → **Settings → API Keys**.
2. Click **Create Key**, name it `bmdw-estimator-prod`, and copy the value
   (starts with `sk-ant-`). You'll only see it once.
3. Open `.env` in the project root and set:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
4. Restart the Streamlit app (`Ctrl+C`, then `python3 -m streamlit run Quoting.py`).
5. On the Quoting page, the **🤖 Generate Line Items from Notes** button should
   now be enabled (was disabled before).

## Verifying it works

1. Type a few lines into Quick Notes (e.g. "20 ft × 4 ft lock-block wall").
2. Hit **Generate Line Items**.
3. You should see an "AI Draft (preview)" block within ~5 seconds.

If it errors with a JSON parse failure, the model truncated. Cut your notes
shorter and retry, or split into two passes via the Iteration field.

## Cost

Sonnet 4.6 with prompt caching costs roughly $0.01–0.03 per quote parse.
Caching the catalogue context cuts the cost in half on the second call within
5 minutes. Keep your monthly Anthropic spend cap reasonable to avoid surprises.

## Render deploy

Set `ANTHROPIC_API_KEY` in the Render dashboard under **Environment** for the
service. It is read at runtime — no code change needed when rotating keys.

## Rotating the key

1. Create a new key in the Anthropic console.
2. Update `.env` locally and `ANTHROPIC_API_KEY` in Render env.
3. Delete the old key from the Anthropic console.
