# llm_email_assist_poc
Proof of Concept of an LLM powered email assistant

# Gmail + LLM Summarizer (PoC)

A tiny FastAPI proof-of-concept that:

* Authenticates with Google (OAuth)
* Requests **Gmail read-only** permission
* Fetches an email and asks an LLM to summarize its main topics as bullet points

> PoC only — minimal error handling, no DB, not production-hardened.

---

## Prerequisites

* Python 3.10+
* A Google Cloud project with **OAuth consent screen** configured and **Gmail API enabled**
* An OAuth 2.0 Web Client (Client ID & Secret)
* An LLM API key (OpenRouter, OpenAI, etc.). The PoC uses the OpenAI SDK and can point at OpenRouter via `base_url`.

---

## Environment

Create `/backend/.env` with:

```env
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
SESSION_SECRET_KEY=
LLM_API_KEY=
```

> The code references `OPENROUTER_API_KEY` in a few places in the PoC. Rename those to `LLM_API_KEY` (or update your env var name to match the code) and switch the **model** to the one you’re using.

---

## Run

```bash
cd project
python3 -m venv venv
source venv/bin/activate

cd backend/
pip install -r requirements.txt

cd ..
uvicorn backend.app.main:app --reload --host localhost --port 8000
```

Open your browser to:

```
http://localhost:8000
```

Log in with a **whitelisted Google account** (per your OAuth consent screen settings).

---

## Summarize an email

After logging in, hit:

```
/ai/summarize_email?q=in:anywhere newer_than:7d
```

This fetches the first matching message and returns JSON with:

* `date` (From the Date header)
* `from` (sender’s email)
* `subject`
* `summary` (LLM bullet points)
* `body_chars` (size of the email body used)

---

## Notes

* Scope used: `https://www.googleapis.com/auth/gmail.readonly`
* If your LLM provider is OpenRouter, set the OpenAI client’s `base_url` to `https://openrouter.ai/api/v1` and use your API key.
* Gmail search uses native query syntax (e.g., `in:anywhere`, `newer_than:7d`, `from:example.com`). Spaces should be URL-encoded by your client.
