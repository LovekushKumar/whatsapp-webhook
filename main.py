from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse
import os, re, requests, time

app = FastAPI()

SESSIONS: dict[str, dict] = {}

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# --- Config ---
SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT_SECONDS", "300"))  # seconds
REQUIRED_FIELDS = ["name", "phone", "date_of_issue", "reference_id", "issue_description"]
DISPLAY = {
    "name": "Name",
    "phone": "Phone",
    "date_of_issue": "Date of Issue",
    "reference_id": "Reference ID",
    "issue_description": "Issue Description",
}
GREETINGS = {"hi", "hello", "hey", "hiya", "greetings"}


def send_whatsapp_message(to_number: str, message: str):
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": message}}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code >= 400:
            print("Meta response:", resp.status_code, resp.text)
    except Exception as e:
        print("Send error:", e)


def _clean_name(raw: str) -> str:
    raw = re.split(r"\s+(?:and)\b|[,\.]", raw, maxsplit=1)[0]
    raw = re.sub(r"[^A-Za-z .'\-]", " ", raw)
    raw = re.sub(r"\s{2,}", " ", raw).strip()
    return raw.title()


def _clean_issue_description(text: str) -> str:
    t = re.sub(r'^(my\s+issue\s+is|issue\s+is|i\s+am\s+facing|i\'m\s+facing|facing\s+issue|problem\s+is|issue:)\s*', '', text, flags=re.I)
    t = re.sub(r'^\bthat\b\s+', '', t, flags=re.I)
    return t.strip()


def extract_fields(message: str, current_data: dict) -> dict:
    """
    Extracts fields from a free-form message.
    - Explicit patterns first (phone, date, explicit name, explicit ref)
    - Conservative fallbacks: reference-id fallback requires a digit (to avoid capturing names)
    - Issue description: explicit phrases or leftover text (cleaned)
    """
    data = dict(current_data or {})
    text = message.strip()
    lower = text.lower()

    # --- PHONE (10-15 digits)
    if not data.get("phone"):
        m = re.search(r"(?<!\d)(\+?\d{10,15})(?!\d)", text)
        if m:
            data["phone"] = m.group(1)

    # --- DATE (dd-mm-yyyy or dd/mm/yyyy)
    if not data.get("date_of_issue"):
        m = re.search(r"\b(\d{1,2}[\/-]\d{1,2}[\/-]\d{4})\b", text)
        if m:
            data["date_of_issue"] = m.group(1)

    # --- EXPLICIT REFERENCE ID (prefixed)
    if not data.get("reference_id"):
        m = re.search(r"\b(?:reference\s*id|ref(?:erence)?\s*id|ticket\s*id|case\s*id|ref|rid)\s*(?:is|:|#)?\s*([A-Za-z0-9_-]{2,})\b", text, re.I)
        if m:
            data["reference_id"] = m.group(1).strip()

    # --- EXPLICIT NAME (non-greedy, stops at 'and', ',', '.', or field keywords)
    if not data.get("name"):
        m = re.search(
            r"\b(?:my\s+name\s+is|i\s+am|this\s+is)\s+([A-Za-z][A-Za-z .'\-]{1,100}?)(?=\s+(?:and\b|\bphone\b|\bmobile\b|\bref\b|\breference\b|\bid\b|\bdate\b|\bissue\b|,|\.|$))",
            text,
            re.I,
        )
        if m:
            # Use original substring for casing
            start, end = m.start(1), m.end(1)
            name_raw = text[start:end]
            data["name"] = _clean_name(name_raw)

    # --- NAME label-style: "Name: Vijay Kumar"
    if not data.get("name"):
        m = re.search(r"\bname\s*(?:\:|-)\s*([A-Za-z][A-Za-z .'\-]{1,100})", text, re.I)
        if m:
            data["name"] = _clean_name(m.group(1))

    # --- NAME fallback: whole message might be a name (two+ words, not greeting)
    if not data.get("name"):
        only_letters_spaces = re.fullmatch(r"[A-Za-z .'\-]{3,}", text) is not None
        words = re.findall(r"[A-Za-z]+", text)
        is_greeting = lower.strip() in GREETINGS or any(lower.startswith(g + " ") for g in GREETINGS)
        if only_letters_spaces and len(words) >= 2 and not is_greeting:
            data["name"] = _clean_name(text)

    # --- ISSUE explicit phrases (prefer capturing after phrase)
    if not data.get("issue_description"):
        m = re.search(r"(?:my\s+issue\s+is|issue\s+is|i\s+am\s+facing|i'?m\s+facing|facing\s+issue|problem\s+is|not\s+working|unable\s+to|error[:\s])\s*(.*)", text, re.I)
        if m:
            issue_raw = m.group(1).strip()
            if issue_raw:
                data["issue_description"] = _clean_issue_description(issue_raw)
            else:
                # If nothing after the phrase, take remainder of message
                tail = text[m.end():].strip()
                if tail:
                    data["issue_description"] = _clean_issue_description(tail)

    # --- If still no issue_description, try to use leftover after removing explicit matches
    if not data.get("issue_description"):
        # remove explicit phone/date/ref/name substrings to compute leftover
        temp = text
        # remove found phone
        if data.get("phone"):
            temp = re.sub(re.escape(data["phone"]), " ", temp, flags=re.I)
        # remove found date
        if data.get("date_of_issue"):
            temp = re.sub(re.escape(data["date_of_issue"]), " ", temp, flags=re.I)
        # remove explicit reference tokens (if present in original)
        if data.get("reference_id"):
            temp = re.sub(re.escape(data["reference_id"]), " ", temp, flags=re.I)
        # remove name if present
        if data.get("name"):
            temp = re.sub(re.escape(data["name"]), " ", temp, flags=re.I | re.I)
        leftover = temp.strip()
        # fallback: if leftover looks like a sentence and not too short
        if len(leftover.split()) >= 3 and not any(w in lower for w in GREETINGS):
            # remove starting filler words like "that", "so", "then"
            leftover = re.sub(r'^(that|so|then)\s+', '', leftover, flags=re.I).strip()
            if len(leftover) >= 4:
                data["issue_description"] = _clean_issue_description(leftover)

    # --- REFERENCE ID fallback (conservative): require at least one digit to avoid picking names
    if not data.get("reference_id"):
        candidates = re.findall(r"\b[A-Za-z0-9_-]{2,}\b", text)
        for cand in candidates:
            if cand.lower() == "phone":
                continue
            if re.fullmatch(r"\+?\d{10,15}", cand):
                continue  # phone-like
            if re.fullmatch(r"\d{1,2}[\/-]\d{1,2}[\/-]\d{4}", cand):
                continue  # date-like
            # require candidate to contain at least one digit (conservative)
            if re.search(r"\d", cand):
                # also ensure it's not the name we captured
                if data.get("name") and cand.lower() in data["name"].lower():
                    continue
                data["reference_id"] = cand
                break

    return data


def build_reply(session_data: dict) -> str:
    missing_keys = [k for k in REQUIRED_FIELDS if not session_data.get(k)]
    if not missing_keys:
        # format final values in bold using *...*
        return (
            "Following data has been collected:\n"
            f"{DISPLAY['name']}: *{session_data['name']}*\n"
            f"{DISPLAY['phone']}: *{session_data['phone']}*\n"
            f"{DISPLAY['date_of_issue']}: *{session_data['date_of_issue']}*\n"
            f"{DISPLAY['reference_id']}: *{session_data['reference_id']}*\n"
            f"{DISPLAY['issue_description']}: *{session_data['issue_description']}*\n\n"
            "Thank you!"
        )
    else:
        # Present missing fields with display names
        missing_display = [DISPLAY[k] for k in missing_keys]
        return f"Please provide the following missing fields: {', '.join(missing_display)}"


@app.get("/webhook")
async def verify(request: Request):
    params = dict(request.query_params)
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""), status_code=200)
    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    try:
        entries = body.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                if not messages:
                    continue

                msg = messages[0]
                if msg.get("type") != "text":
                    continue

                user_text = msg["text"]["body"].strip()
                from_number = msg["from"]

                # RESET / RESTART
                if user_text.lower() in {"reset", "restart"}:
                    SESSIONS[from_number] = {"data": {k: None for k in REQUIRED_FIELDS}, "last_active": time.time()}
                    send_whatsapp_message(from_number, "Thanks for confirmation")
                    continue

                # SESSION timeout check
                now = time.time()
                session = SESSIONS.get(from_number)
                if session and (now - session.get("last_active", now) > SESSION_TIMEOUT):
                    # expired
                    SESSIONS[from_number] = {"data": {k: None for k in REQUIRED_FIELDS}, "last_active": now}
                    send_whatsapp_message(from_number, "Session expired due to inactivity. Let's start over. Please provide your details.")
                    continue

                # ensure session exists
                if not session:
                    session = {"data": {k: None for k in REQUIRED_FIELDS}, "last_active": now}
                    SESSIONS[from_number] = session

                # Extract and merge fields
                extracted = extract_fields(user_text, session["data"])
                # update only missing fields to avoid overriding earlier collected values
                for k, v in extracted.items():
                    if v and not session["data"].get(k):
                        session["data"][k] = v

                session["last_active"] = now
                SESSIONS[from_number] = session

                # Build reply and send
                reply = build_reply(session["data"])
                send_whatsapp_message(from_number, reply)

    except Exception as e:
        print("Error in webhook:", e)

    return JSONResponse({"status": "ok"})
