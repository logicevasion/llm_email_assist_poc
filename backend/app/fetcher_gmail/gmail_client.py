# backend/app/fetcher_gmail/gmail_client.py
import asyncio
import base64
import random
from fastapi import HTTPException, status, Request
from typing import AsyncIterator, Dict, List, Optional, Tuple
from authlib.integrations.httpx_client import AsyncOAuth2Client
import httpx

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
TRANSIENT_STATUSES = {429, 500, 502, 503}


# Client construction
def build_async_client(
    token: dict,
    client_id: str,
    client_secret: str,
    token_endpoint: str = "https://oauth2.googleapis.com/token",
    save_token_callback=None,
) -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=client_id,
        client_secret=client_secret,
        token=token,
        token_endpoint=token_endpoint,
        update_token=(save_token_callback or (lambda t, *a, **kw: None)),
    )


##########################################################################
# Utility Functions to be moved to a separate file later
##########################################################################

# ---- auth guard --------------------------------------------------------------
REQUIRED_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
def require_gmail_auth(request: Request):
    token = request.session.get("token") or request.session.get("oauth_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
    # Be robust: scopes may be in token["scope"] or stored separately, and may be None
    scopes_str = token.get("scope") or request.session.get("granted_scopes") or ""
    scopes = scopes_str.split()
    if REQUIRED_SCOPE not in scopes:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing gmail.readonly scope")
    request.state.token = token
    return True

# ------------------------
# Low-level helpers
# ------------------------
def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "===")

async def _backoff_get(
    client: AsyncOAuth2Client,
    url: str,
    params: Optional[Dict] = None,
    max_retries: int = 5,
    timeout: float = 30.0,
) -> httpx.Response:
    for attempt in range(max_retries + 1):
        resp = await client.get(url, params=params, timeout=timeout)
        if resp.status_code not in TRANSIENT_STATUSES:
            return resp
        await asyncio.sleep((2 ** attempt) + random.random())
    resp.raise_for_status()
    return resp

async def _paginate(
    client: AsyncOAuth2Client,
    url: str,
    params: Optional[Dict] = None,
    item_key: str = "messages",
    page_token_param: str = "pageToken",
) -> AsyncIterator[Dict]:
    params = dict(params or {})
    while True:
        resp = await _backoff_get(client, url, params=params)
        data = resp.json()
        for item in data.get(item_key, []):
            yield item
        token = data.get("nextPageToken")
        if not token:
            break
        params[page_token_param] = token

# ------------------------
# Message listing + fetching
# ------------------------
async def list_all_message_ids(
    client: AsyncOAuth2Client,
    query: Optional[str] = None,
    label_ids: Optional[List[str]] = None,
    page_size: int = 500,
) -> AsyncIterator[str]:
    params = {"maxResults": page_size}
    if query:
        params["q"] = query
    if label_ids:
        params["labelIds"] = ",".join(label_ids)
    async for item in _paginate(
        client, f"{GMAIL_BASE}/users/me/messages", params=params, item_key="messages"
    ):
        yield item["id"]

async def fetch_message_full(client: AsyncOAuth2Client, msg_id: str) -> Dict:
    resp = await _backoff_get(
        client, f"{GMAIL_BASE}/users/me/messages/{msg_id}", params={"format": "full"}
    )
    resp.raise_for_status()
    return resp.json()

# ------------------------
# Header and body extraction
# ------------------------
def _headers_to_map(headers: List[Dict[str, str]]) -> Dict[str, str]:
    return {h["name"]: h["value"] for h in headers or []}

def _pick(hmap: Dict[str, str], name: str) -> Optional[str]:
    lname = name.lower()
    for k, v in hmap.items():
        if k.lower() == lname:
            return v
    return None

def extract_bodies(payload: Dict) -> Tuple[Optional[str], Optional[str]]:
    def walk(p) -> Tuple[List[bytes], List[bytes]]:
        txts, htmls = [], []
        mime = p.get("mimeType", "") or ""
        body = p.get("body", {}) or {}
        data = body.get("data")
        if data and not mime.startswith("multipart/"):
            content = _b64url_decode(data)
            if mime.startswith("text/plain"):
                txts.append(content)
            elif mime.startswith("text/html"):
                htmls.append(content)
        for part in p.get("parts", []) or []:
            t, h = walk(part)
            txts.extend(t)
            htmls.extend(h)
        return txts, htmls

    txt_parts, html_parts = walk(payload or {})
    text = b"\n".join(txt_parts).decode("utf-8", errors="replace") if txt_parts else None
    html = b"\n".join(html_parts).decode("utf-8", errors="replace") if html_parts else None
    return text, html

def normalize_message(msg: Dict) -> Dict:
    payload = msg.get("payload", {}) or {}
    headers_list = payload.get("headers", []) or []
    h = _headers_to_map(headers_list)
    text, html = extract_bodies(payload)
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "history_id": msg.get("historyId"),
        "internal_date_ms": int(msg.get("internalDate", "0")),
        "size_estimate": msg.get("sizeEstimate"),
        "message_id": _pick(h, "Message-ID"),
        "subject": _pick(h, "Subject"),
        "from_": _pick(h, "From"),
        "to": _pick(h, "To"),
        "cc": _pick(h, "Cc"),
        "bcc": _pick(h, "Bcc"),
        "reply_to": _pick(h, "Reply-To"),
        "in_reply_to": _pick(h, "In-Reply-To"),
        "references": _pick(h, "References"),
        "snippet": msg.get("snippet"),
        "body_text": text,
        "body_html": html,
        "labels": msg.get("labelIds", []) or [],
        "headers": h,
    }

# ------------------------
# NEW: "full" JSON without attachment blobs
# ------------------------
def _strip_attachments_preserve_meta(
    part: Dict,
    decode_text: bool = True,
    max_text_chars: Optional[int] = 50_000,
    _collect_meta: Optional[List[Dict]] = None,
) -> Dict:
    """
    Return a copy of a message part where non-text blobs are stripped,
    but metadata (filename, mimeType, size, attachmentId, headers) is kept.
    Text parts can be decoded to UTF-8 for readability.
    """
    out = dict(part)
    mime = (part.get("mimeType") or "").lower()
    filename = part.get("filename") or ""
    body = dict(part.get("body") or {})
    parts = part.get("parts") or []

    # Recurse first
    if parts:
        out["parts"] = [
            _strip_attachments_preserve_meta(
                p, decode_text=decode_text, max_text_chars=max_text_chars, _collect_meta=_collect_meta
            ) for p in parts
        ]

    is_text = mime.startswith("text/plain") or mime.startswith("text/html")
    is_multipart = mime.startswith("multipart/")
    is_attachment_like = bool(filename) or (not is_text and not is_multipart)

    if is_attachment_like:
        # collect metadata
        meta = {
            "filename": filename or None,
            "mimeType": part.get("mimeType"),
            "size": body.get("size"),
            "attachmentId": body.get("attachmentId"),
            "headers": part.get("headers", []),
            "inline": (not filename) and not is_multipart and not is_text,
        }
        if _collect_meta is not None:
            _collect_meta.append(meta)
        # strip data
        if "data" in body:
            body.pop("data", None)
        body["_stripped"] = True
        out["body"] = body
        return out

    if is_text and decode_text:
        data_b64 = body.get("data")
        if data_b64:
            raw = _b64url_decode(data_b64)
            decoded = raw.decode("utf-8", errors="replace")
            truncated = False
            if max_text_chars is not None and len(decoded) > max_text_chars:
                decoded = decoded[:max_text_chars]
                truncated = True
            body = {k: v for k, v in body.items() if k != "data"}
            body["decodedText"] = decoded
            body["_decoded_len"] = len(raw)
            if truncated:
                body["_truncated"] = True
            out["body"] = body
            return out

    out["body"] = body
    return out

def strip_message_full_keep_blobs_out(
    msg: Dict,
    decode_text: bool = True,
    max_text_chars: Optional[int] = 50_000,
    include_attachments_meta: bool = True,
) -> Dict:
    """Process a Gmail 'full' message JSON to remove attachment blobs but keep everything else."""
    out = dict(msg)
    payload = msg.get("payload") or {}
    attachments_meta: List[Dict] = []
    out["payload"] = _strip_attachments_preserve_meta(
        payload, decode_text=decode_text, max_text_chars=max_text_chars,
        _collect_meta=(attachments_meta if include_attachments_meta else None),
    )
    if include_attachments_meta:
        out["attachments_meta"] = attachments_meta
    return out

async def fetch_message_full_no_blobs(
    client: AsyncOAuth2Client,
    msg_id: str,
    decode_text: bool = True,
    max_text_chars: Optional[int] = 50_000,
    include_attachments_meta: bool = True,
) -> Dict:
    full = await fetch_message_full(client, msg_id)
    return strip_message_full_keep_blobs_out(
        full,
        decode_text=decode_text,
        max_text_chars=max_text_chars,
        include_attachments_meta=include_attachments_meta,
    )

async def iter_messages_full_no_blobs(
    client: AsyncOAuth2Client,
    query: Optional[str] = None,
    label_ids: Optional[List[str]] = None,
    page_size: int = 500,
    limit: Optional[int] = None,
    decode_text: bool = True,
    max_text_chars: Optional[int] = 50_000,
    include_attachments_meta: bool = True,
) -> AsyncIterator[Dict]:
    """
    Stream Gmail 'full' JSON messages with attachment blobs removed (metadata kept).
    Useful for inspecting all available fields before choosing what to persist.
    """
    count = 0
    async for msg_id in list_all_message_ids(client, query=query, label_ids=label_ids, page_size=page_size):
        cleaned = await fetch_message_full_no_blobs(
            client,
            msg_id,
            decode_text=decode_text,
            max_text_chars=max_text_chars,
            include_attachments_meta=include_attachments_meta,
        )
        yield cleaned
        count += 1
        if limit and count >= limit:
            break

# ------------------------
# High-level iterator (fetch → normalize)
# ------------------------
async def iter_messages_normalized(
    client: AsyncOAuth2Client,
    query: Optional[str] = None,
    label_ids: Optional[List[str]] = None,
    page_size: int = 500,
    limit: Optional[int] = None,
) -> AsyncIterator[Dict]:
    count = 0
    async for msg_id in list_all_message_ids(client, query=query, label_ids=label_ids, page_size=page_size):
        full = await fetch_message_full(client, msg_id)
        yield normalize_message(full)
        count += 1
        if limit and count >= limit:
            break

# ------------------------
# History helpers (for incremental sync later)
# ------------------------
async def get_profile_history_id(client: AsyncOAuth2Client) -> Optional[str]:
    resp = await _backoff_get(client, f"{GMAIL_BASE}/users/me/profile")
    if resp.status_code == 200:
        return resp.json().get("historyId")
    return None

async def iter_history_pages(
    client: AsyncOAuth2Client,
    start_history_id: str,
    history_types: str = "messageAdded,messageDeleted",
) -> AsyncIterator[Dict]:
    params = {"startHistoryId": start_history_id, "historyTypes": history_types, "maxResults": 1000}
    async for page in _paginate(client, f"{GMAIL_BASE}/users/me/history", params=params, item_key="history"):
        yield page


###########################################################################
# Proof of Concept to grab specific data for LLM use
# --- helper: fetch base64url data for parts that are stored by attachmentId (large text parts) ---
async def fetch_attachment_data_b64(
    client: AsyncOAuth2Client, msg_id: str, attachment_id: str
) -> Optional[str]:
    resp = await _backoff_get(
        client,
        f"{GMAIL_BASE}/users/me/messages/{msg_id}/attachments/{attachment_id}",
    )
    if resp.status_code == 200:
        return resp.json().get("data")
    return None

# --- attachment-aware bodies extraction for LLM (text/plain & text/html only) ---
async def extract_bodies_attachment_aware(
    client: AsyncOAuth2Client, msg_id: str, payload: Dict
) -> Tuple[Optional[str], Optional[str]]:
    """
    Like extract_bodies(), but if a text part lacks body.data and has an attachmentId,
    fetch it via the attachments API so we still get the text.
    """
    async def walk(p) -> Tuple[List[bytes], List[bytes]]:
        txts, htmls = [], []
        mime = (p.get("mimeType") or "").lower()
        body = (p.get("body") or {})
        data_b64 = body.get("data")

        # If it's a single-part text and we don't have data inline, try attachmentId
        if not (mime.startswith("multipart/")) and (mime.startswith("text/plain") or mime.startswith("text/html")):
            if not data_b64 and body.get("attachmentId"):
                data_b64 = await fetch_attachment_data_b64(client, msg_id, body["attachmentId"])
            if data_b64:
                raw = _b64url_decode(data_b64)
                if mime.startswith("text/plain"):
                    txts.append(raw)
                else:
                    htmls.append(raw)

        # Recurse
        for child in (p.get("parts") or []):
            t, h = await walk(child)
            txts.extend(t)
            htmls.extend(h)
        return txts, htmls

    txt_parts, html_parts = await walk(payload or {})
    text = b"\n".join(txt_parts).decode("utf-8", errors="replace") if txt_parts else None
    html  = b"\n".join(html_parts).decode("utf-8", errors="replace") if html_parts else None
    return text, html

# --- LLM projection for a single message ---
async def fetch_llm_projection(
    client: AsyncOAuth2Client, msg_id: str, prefer_plain: bool = True
) -> Dict:
    """
    Return the minimal fields useful for an LLM prompt:
    date (header), from (header), subject (header), body (text preferred), body length.
    """
    full = await fetch_message_full(client, msg_id)
    payload = full.get("payload") or {}
    headers_list = payload.get("headers", []) or []
    h = _headers_to_map(headers_list)

    # attachment-aware bodies
    text, html = await extract_bodies_attachment_aware(client, full.get("id", ""), payload)

    # choose body
    body = (text if (prefer_plain and text) else (html or text) or "")
    body_format = "text/plain" if (prefer_plain and text) else ("text/html" if html else "text/plain")
    body_len = len(body)

    return {
        "id": full.get("id"),
        "thread_id": full.get("threadId"),
        "internal_date_ms": int(full.get("internalDate", "0")),
        "date": _pick(h, "Date"),
        "from": _pick(h, "From"),
        "subject": _pick(h, "Subject"),
        "body": body,
        "body_format": body_format,
        "body_chars": body_len,
    }

# --- iterator version (query + limit) ---
async def iter_llm_projection(
    client: AsyncOAuth2Client,
    query: Optional[str] = None,
    label_ids: Optional[List[str]] = None,
    page_size: int = 500,
    limit: Optional[int] = None,
    prefer_plain: bool = True,
) -> AsyncIterator[Dict]:
    count = 0
    async for msg_id in list_all_message_ids(client, query=query, label_ids=label_ids, page_size=page_size):
        yield await fetch_llm_projection(client, msg_id, prefer_plain=prefer_plain)
        count += 1
        if limit and count >= limit:
            break