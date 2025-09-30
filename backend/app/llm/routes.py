from fastapi import APIRouter, Request, HTTPException, Depends
from email.utils import parseaddr
from backend.app.fetcher_gmail.gmail_client import (
    require_gmail_auth,
    build_async_client,
    fetch_llm_projection,
    iter_llm_projection,
)
from backend.app.config import settings


router = APIRouter(
    prefix="/ai",
    tags=["ai"],
    dependencies=[Depends(require_gmail_auth)],  # ensures request.state.token is set
)

CLIENT_ID = settings.GOOGLE_CLIENT_ID
CLIENT_SECRET = settings.GOOGLE_CLIENT_SECRET


@router.get("/summarize_email")
async def summarize_email(
    request: Request,
    id: str | None = None,         # specific Gmail message id; if absent, we take first match from query
    q: str | None = None,          # e.g. "in:anywhere newer_than:30d"
    prefer_plain: bool = True,     # prefer text/plain over text/html when both exist
    model: str = "x-ai/grok-4-fast:free",    # pick your default; or wire to settings later
):
    """
    Summarize one email body into bullet points and return:
      - date (header)
      - from (email address only)
      - subject (header)
      - summary (bullet list as text)
      - body length (chars)
    """

    # 1) Get LLM client
    llm_client = getattr(request.app.state, "llm", None)
    if llm_client is None:
        raise HTTPException(status_code=500, detail="LLM client not configured on app.state.llm")

    # 2) Build Gmail OAuth client
    gmail_client = build_async_client(
        token=request.state.token,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )

    try:
        # 3) Get the minimal email fields for LLM (date, from, subject, body, body_chars)
        if id:
            email_data = await fetch_llm_projection(gmail_client, id, prefer_plain=prefer_plain)
        else:
            # take the first projection from the iterator (note: list order isn't guaranteed by Gmail)
            async for email_data in iter_llm_projection(gmail_client, query=q, limit=1, prefer_plain=prefer_plain):
                break
            else:
                raise HTTPException(status_code=404, detail="No messages matched the query")

        # Extract just the address from the From header
        # The purpose of the "_," is for discarding the display name
        _, from_email = parseaddr(email_data.get("from") or "")

        # 4) Call LLM to summarize body as bullet points
        body_text = email_data.get("body") or ""
        if not body_text.strip():
            summary_text = "- (No body content found)"
        else:
            prompt = (
                "Summarize the main topics of the following email body as concise bullet points.\n"
                "Rules:\n"
                "- Use short, content-rich bullets (no full sentences unless necessary).\n"
                "- 5 bullets max.\n"
                "- No preamble or conclusion—just the bullets.\n\n"
                "Email body:\n"
                f"{body_text}"
            )
            try:
                resp = llm_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are concise. Summarize main topics as up to 5 short bullets. No preamble."},
                        {"role": "user", "content": prompt},
                    ],
                )
                summary_text = resp.choices[0].message.content.strip()
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")

        # 5) Return minimal JSON payload
        return {
            "id": email_data.get("id"),
            "date": email_data.get("date"),
            "from": from_email or email_data.get("from"),
            "subject": email_data.get("subject"),
            "summary": summary_text,
            "body_chars": email_data.get("body_chars", 0),
        }

    finally:
        await gmail_client.aclose()
