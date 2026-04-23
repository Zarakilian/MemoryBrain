# MemoryBrain + Gemini Setup Guide

## Why Gemini? (Why you might need an API key)

**Before v0.5.0:** MemoryBrain only used **Ollama** — a local AI model that runs on your machine. No internet, no keys needed, completely private.

**v0.5.0 adds choice:** You can now pick from three AI backends:

| Backend | Key needed? | Speed | Cost | Privacy |
|---------|-----------|-------|------|---------|
| **Ollama** (default) | ❌ No | Medium (local) | Free | 100% — runs locally |
| **Gemini (Google AI)** | ✅ Yes | Fast (cloud) | Free tier available | Sends text to Google's servers |
| **OpenAI** | ✅ Yes | Fast (cloud) | Paid | Sends text to OpenAI's servers |

**Why would you want Gemini?**
- **Speed:** Cloud models are faster than local Ollama
- **Better quality:** Gemini embeddings are higher quality than local models
- **Free tier:** Google gives 15,000 free requests/month — enough for most personal use
- **Less compute:** Doesn't require running local models (useful if your machine is slow)

**Why stick with Ollama?**
- **Complete privacy** — nothing leaves your machine
- **No API keys** — nothing to manage or forget
- **No cloud dependency** — works offline
- **Free & open source**

---

## Getting a Google API Key (5 minutes)

### Step 1: Go to Google AI Studio

Visit: **https://aistudio.google.com/app/apikey**

(No Google Cloud project needed — this is the free tier.)

### Step 2: Click "Create API Key"

You'll see:
```
📋 Create API Key
  ├─ Create new API key in new project  ← Click this
  └─ Create API key in existing project
```

Click **"Create new API key in new project"**

### Step 3: Copy Your Key

Google will show:
```
Your API Key:
sk-proj-a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

**Copy this** — you'll need it in 1 minute.

### Step 4: Paste into `.env`

Open the MemoryBrain `.env` file:
```bash
nano ~/memorybrain/.env
# or: code ~/.env
```

Find this line:
```bash
GOOGLE_API_KEY=
```

Paste your key:
```bash
GOOGLE_API_KEY=sk-proj-a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

Save the file.

### Step 5: Restart MemoryBrain

```bash
cd ~/memorybrain
docker compose restart brain
```

### Step 6: Verify It Works

```bash
curl http://localhost:7741/readiness | jq .
```

You should see:
```json
{
  "ready": true,
  "checks": {
    "sqlite": "ok",
    "chromadb": "ok",
    "gemini_api_key": "ok",
    "gemini_client": "ok"
  }
}
```

If you see `"gemini_client": "error"`, the key is wrong or Gemini API is down. Check the error message for details.

---

## Guided Setup (Recommended)

Instead of manual steps, use the interactive setup:

```bash
cd ~/memorybrain
python3 cli/brain.py setup --gemini-guided
```

This will:
1. ✅ Open the Google API Studio link in your browser
2. 📋 Prompt you to paste the key
3. 🧪 Test the connection
4. 💾 Save to `.env`
5. 🔄 Restart Docker

---

## Troubleshooting

### "API Key is invalid"
- ✋ Paste it **exactly** as Google shows it (including `sk-proj-` prefix)
- Make sure there are no extra spaces
- Try copying again from: https://aistudio.google.com/app/apikey

### "Rate limit exceeded"
- Google's free tier allows 15,000 requests/month (~500/day)
- If you hit this, wait 24 hours or upgrade to a paid plan
- Or switch back to Ollama: just remove `GOOGLE_API_KEY=` from `.env`

### "Network error / can't reach Google"
- Check your internet connection
- Check if Google is accessible: `curl https://aistudio.google.com`
- Gemini works best with a stable connection

### "gemini_client says 'error' but API key looks right"
- Restart Docker: `docker compose restart brain`
- Wait 30 seconds for the container to be ready
- Try `/readiness` again

---

## Switching Providers

You can easily switch between all three:

### Use Gemini (cloud, fast, free tier)
```bash
GOOGLE_API_KEY=sk-proj-xxxx
# (leave OPENAI_API_KEY blank)
```

### Use OpenAI (cloud, paid)
```bash
OPENAI_API_KEY=sk-xxxxx
# (leave GOOGLE_API_KEY blank)
```

### Use Ollama (local, private, free)
```bash
# Leave both API keys blank
GOOGLE_API_KEY=
OPENAI_API_KEY=
```

Restart: `docker compose restart brain`

**Provider priority:** Gemini > OpenAI > Ollama (whichever key is set first is used)

---

## What Data Does Gemini See?

When you use Gemini, MemoryBrain sends:

✅ **Sent to Google:**
- Memory text content (for embeddings + summarization)
- Queries you search for (for semantic search)

❌ **NOT sent:**
- Your `.env` file or API keys
- Memory metadata (project, tags, status)
- Database structure

**Google's policy:** Free tier API calls are not used to train models. Your content is not stored. See: https://ai.google.dev/gemini-api/docs/faq#data-handling

---

## Questions?

- **How many free requests do I get?** 15,000 per month (Google's free tier limit)
- **Can I use my own Google Cloud project?** Yes, but you'll need to enable the Generative AI API and create a service account key. This guide uses the simpler "free tier" approach.
- **What if my key expires?** Google keys don't expire, but you can revoke them anytime at https://aistudio.google.com/app/apikey
- **Can I use Gemini for embeddings but Ollama for summaries?** Yes! Set individual env vars for each model type.

---

## Next Steps

Once Gemini is set up:
1. Use Claude Code / Gemini CLI as normal — MemoryBrain auto-detects Gemini
2. Memories are embedded using Gemini's high-quality model
3. Search is faster because cloud embeddings are computed on-demand
4. Everything else works the same (no code changes needed)

---

**Last updated:** 2026-04-23 (v0.5.0)
