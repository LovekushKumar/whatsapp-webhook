from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse
import os, requests, re, time

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# --- Sessions (in-memory) ---
user_sessions = {}  # { from_number: {"data": {...}, "last_active": ts} }
SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT_SECONDS", "300"))  # default 5 min

REQUIRED_FIELDS = ["name", "phone", "date_of_issue", "reference_id", "issue_description"]
DISPLAY = {
    "name": "Name",
    "phone": "Phone",
    "date_of_issue": "Date of Issue",
    "reference_id": "Reference ID",
    "issue_description": "Issue Description",
}

GREETINGS = {"hi", "hello", "hey", "hiya", "greetings", "good morning", "good evening", "good afternoon", "goodnight", "good night"}

def send_whatsapp_message(to_number: str, message: str):
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": message}}
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    if resp.status_code >= 400:
        print("Meta response:", resp.status_code, resp.text)

def _merge_spans(spans):
    if not spans: return []
    spans.sort()
    merged = [spans[0]]
    for s,e in spans[1:]:
        ls, le = merged[-1]
        if s <= le:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s,e))
    return merged

def _remove_spans(text, spans):
    if not spans: return text
    out, last = [], 0
    for s,e in spans:
        out.append(text[last:s])
        last = e
    out.append(text[last:])
    return "".join(out).strip()

def _clean_name(raw: str) -> str:
    # cut at conjunctions/punctuation like "and", ',', '.'
    raw = re.split(r"\s+(?:and)\b|[,\.]", raw, maxsplit=1)[0]
    # keep only letters, spaces, approved symbols
    raw = re.sub(r"[^A-Za-z .'\-]", " ", raw)
    raw = re.sub(r"\s{2,}", " ", raw).strip()
    return raw.title()

def extract_fields(message: str, current: dict) -> dict:
    """
    Extract name, phone, date_of_issue, reference_id, issue_description from message.
    Handles multiple fields in any order and avoids grabbing greetings as names.
    """
    data = dict(current or {})
    text = message.strip()
    lower = text.lower()
    spans = []  # parts of text we've consumed for specific fields

    # --- PHONE (10-15 digits, not inside longer digit sequences)
    if not data.get("phone"):
        m = re.search(r"(?<!\d)(\+?\d{10,15})(?!\d)", text)
        if m:
            data["phone"] = m.group(1)
            spans.append(m.span(1))

    # --- DATE (dd-mm-yyyy or dd/mm/yyyy)
    if not data.get("date_of_issue"):
        m = re.search(r"\b(\d{1,2}[\/-]\d{1,2}[\/-]\d{4})\b", text)
        if m:
            data["date_of_issue"] = m.group(1)
            spans.append(m.span(1))

    # --- REFERENCE ID (explicit phrases)
    if not data.get("reference_id"):
        m = re.search(r"\b(?:reference\s*id|ref\s*id|ticket\s*(?:id)?|case\s*(?:id)?|id)\s*(?:is|:|#)?\s*([A-Za-z0-9_-]{2,})\b", lower, re.I)
        if m:
            # span relative to original text; find group text in original safely
            ref_val = m.group(1)
            data["reference_id"] = ref_val
            # approximate span using search in original (case-insensitive)
            m2 = re.search(re.escape(ref_val), text, re.I)
            if m2: spans.append(m2.span())

    # --- NAME (explicit phrases)
    if not data.get("name"):
        m = re.search(r"\b(?:my\s+name\s+is|i\s*am|this\s*is)\s+([A-Za-z][A-Za-z .'\-]{1,50})", lower, re.I)
        if m:
            # map back to original substring for proper casing
            # find the captured phrase in original by length
            start = m.start(1); end = m.end(1)
            name_raw = message[start:end]
            data["name"] = _clean_name(name_raw)
            spans.append((start, end))

    # --- NAME (label style: "name: Sanjay Kumar")
    if not data.get("name"):
        m = re.search(r"\bname\s*[:\-]\s*([A-Za-z][A-Za-z .'\-]{1,50})", lower, re.I)
        if m:
            start = m.start(1); end = m.end(1)
            name_raw = message[start:end]
            data["name"] = _clean_name(name_raw)
            spans.append((start, end))

    # --- NAME fallback (only if the entire message is a likely name)
    # conditions: not a greeting phrase; at least TWO words of letters; no digits/symbols
    if not data.get("name"):
        only_letters_spaces = re.fullmatch(r"[A-Za-z .'\-]{3,}", text) is not None
        words = re.findall(r"[A-Za-z]+", text)
        is_greeting = lower in GREETINGS or any(lower.startswith(g + " ") for g in GREETINGS)
        if only_letters_spaces and len(words) >= 2 and not is_greeting:
            data["name"] = _clean_name(text)
            spans.append((0, len(text)))

    # --- After we’ve taken phone/date/ref/name, compute leftover for ISSUE
    spans_merged = _merge_spans(spans)
    leftover = _remove_spans(text, spans_merged)
    leftover_lower = leftover.lower()

    # ISSUE (explicit phrases first)
    if not data.get("issue_description"):
        m = re.search(r"(?:my\s+issue\s+is|issue\s+is|i\s*am\s*facing\s+issue|facing\s+issue|problem\s+is|not\s+working|unable\s+to|error)\s*(.*)", leftover_lower, re.I)
        if m:
            start = m.start(1); end = m.end(1)
            issue_raw = leftover[start:end].strip()
            # if nothing after the phrase, take the full leftover
            if not issue_raw:
                issue_raw = leftover.strip()
            data["issue_description"] = issue_raw

    # ISSUE fallback: if still empty and leftover looks like a sentence
    if not data.get("issue_description"):
        # Avoid capturing pure greetings or very short texts
        if len(leftover.split()) >= 4 and all(w not in GREETINGS for w in leftover_lower.split()):
            data["issue_description"] = leftover.strip()

    # --- REFERENCE ID fallback (generic token 3-12 chars, but not a phone/date)
    if not data.get("reference_id"):
        # remove digits that are likely phones/dates from consideration
        candidates = re.findall(r"\b([A-Za-z0-9][A-Za-z0-9_-]{2,11})\b", leftover)
        for cand in candidates:
            # skip if looks like phone (>=10 digits) or is a pure common word
            if re.fullmatch(r"\d{10,}", cand):  # phone-like
                continue
            if re.fullmatch(r"\d{1,2}[\/-]\d{1,2}[\/-]\d{4}", cand):  # date-like
                continue
            # pick the first reasonable candidate
            data["reference_id"] = cand
            break

    return data

def build_reply(session_data: dict) -> str:
    missing = [DISPLAY[k] for k in REQUIRED_FIELDS if not session_data.get(k)]
    if not missing:
        return (
            "Following data has been collected:\n"
            f"{DISPLAY['name']}: {session_data['name']}\n"
            f"{DISPLAY['phone']}: {session_data['phone']}\n"
            f"{DISPLAY['date_of_issue']}: {session_data['date_of_issue']}\n"
            f"{DISPLAY['reference_id']}: {session_data['reference_id']}\n"
            f"{DISPLAY['issue_description']}: {session_data['issue_description']}\n\n"
            "Thank you!"
        )
    else:
        return "Please provide the following missing fields: " + ", ".join(missing)

# --- WhatsApp verify ---
@app.get("/webhook")
async def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(challenge, status_code=200)
    return PlainTextResponse("Verification failed", status_code=403)

# --- WhatsApp inbound ---
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    try:
        changes = body.get("entry", [{}])[0].get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                continue

            msg = messages[0]
            if msg.get("type") != "text":
                continue

            user_text = msg["text"]["body"]
            from_number = msg["from"]

            # RESET / RESTART
            if user_text.strip().lower() in {"reset", "restart"}:
                user_sessions.pop(from_number, None)
                send_whatsapp_message(from_number, "Thanks for confirmation. Let's start over. Please provide your Name.")
                continue

            # SESSION timeout
            now = time.time()
            session = user_sessions.get(from_number)
            if session and now - session.get("last_active", now) > SESSION_TIMEOUT:
                user_sessions.pop(from_number, None)
                send_whatsapp_message(from_number, "Session expired due to inactivity. Let's start over. Please provide your Name.")
                continue

            # Ensure session
            if not session:
                session = {"data": {}, "last_active": now}
                user_sessions[from_number] = session

            # Extract & update
            session["data"] = extract_fields(user_text, session["data"])
            session["last_active"] = now

            # Reply
            reply = build_reply(session["data"])
            send_whatsapp_message(from_number, reply)

    except Exception as e:
        print("Error:", e)

    return JSONResponse({"status": "ok"})
