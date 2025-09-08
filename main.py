from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import os, re, requests, time

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# Store sessions
SESSIONS = {}
SESSION_TIMEOUT = 300  # 5 minutes

REQUIRED_FIELDS = ["name", "phone", "date_of_issue", "reference_id", "issue_description"]

def clean_issue_description(text: str) -> str:
    text = re.sub(r'^(my issue is|issue is|i am facing issue|facing issue|issue:)\s*', '', text, flags=re.I)
    text = re.sub(r'^that\s+', '', text, flags=re.I)
    return text.strip()

def extract_fields(user_message: str):
    extracted = {}

    # Name
    match = re.search(r"\b(?:i am|my name is|this is)\s+([A-Za-z ]+)", user_message, re.I)
    if match:
        extracted["name"] = match.group(1).strip()
    elif re.fullmatch(r"[A-Za-z ]+", user_message.strip(), re.I):
        extracted["name"] = user_message.strip()

    # Phone
    match = re.search(r"\b(\+?\d{10,15})\b", user_message)
    if match:
        extracted["phone"] = match.group(1)

    # Date
    match = re.search(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{4})\b", user_message)
    if match:
        extracted["date_of_issue"] = match.group(1)

    # Reference ID (exclude “phone” word)
    match = re.search(r"(?:reference id|ref id|case id|ticket id)\s*[:\-]?\s*([A-Za-z0-9\-]+)", user_message, re.I)
    if match:
        extracted["reference_id"] = match.group(1).strip()
    else:
        tokens = re.findall(r"\b[A-Za-z0-9\-]{3,}\b", user_message)
        for token in tokens:
            if not re.fullmatch(r"\d{10,15}", token):  # not phone
                if not re.fullmatch(r"\d{1,2}[-/]\d{1,2}[-/]\d{4}", token):  # not date
                    if token.lower() != "phone":  # avoid "phone"
                        extracted["reference_id"] = token
                        break

    # Issue description (last fallback)
    if not any(k in extracted for k in ["issue_description"]):
        if re.search(r"(issue|problem|not working|error|fail)", user_message, re.I):
            extracted["issue_description"] = clean_issue_description(user_message)

    return extracted

def build_reply(session_data: dict):
    missing = [f for f in REQUIRED_FIELDS if not session_data.get(f)]
    if not missing:
        return (
            "Following data has been collected:\n"
            f"Name: *{session_data['name']}*\n"
            f"Phone: *{session_data['phone']}*\n"
            f"Date of Issue: *{session_data['date_of_issue']}*\n"
            f"Reference ID: *{session_data['reference_id']}*\n"
            f"Issue Description: *{session_data['issue_description']}*\n\n"
            "Thank you!"
        )
    else:
        return f"Please provide the following missing fields: {', '.join(missing).title()}"

@app.get("/webhook")
async def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(challenge, status_code=200)
    return PlainTextResponse("Verification failed", status_code=403)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    try:
        msg = (data.get("entry", [{}])[0]
                  .get("changes", [{}])[0]
                  .get("value", {})
                  .get("messages", [{}])[0])

        if msg.get("type") != "text":
            return {"status": "ignored_non_text"}

        user_message = msg["text"]["body"]
        from_number = msg["from"]

        # reset or restart command
        if user_message.strip().lower() in ["reset", "restart"]:
            SESSIONS[from_number] = {"last_updated": time.time()}
            for f in REQUIRED_FIELDS:
                SESSIONS[from_number][f] = None
            reply = "Thanks for confirmation"
        else:
            session = SESSIONS.get(from_number, {"last_updated": time.time(), **{f: None for f in REQUIRED_FIELDS}})
            if time.time() - session.get("last_updated", 0) > SESSION_TIMEOUT:
                session = {"last_updated": time.time(), **{f: None for f in REQUIRED_FIELDS}}

            extracted = extract_fields(user_message)
            for k, v in extracted.items():
                if v and not session.get(k):
                    session[k] = v.strip()

            session["last_updated"] = time.time()
            SESSIONS[from_number] = session

            reply = build_reply(session)

        url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "to": from_number, "type": "text", "text": {"body": reply}}
        requests.post(url, headers=headers, json=payload, timeout=10)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}
