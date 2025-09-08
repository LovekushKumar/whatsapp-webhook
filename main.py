from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import os, requests, re, time

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# In-memory session store (key = user phone)
sessions = {}

REQUIRED_FIELDS = ["Name", "Phone", "Date of Issue", "Reference ID", "Issue Description"]

# Configurable inactivity timeout (in seconds)
SESSION_TIMEOUT = 5 * 60  # 5 minutes

def extract_fields(user_message: str):
    """Simple extractor: looks for known fields in free text."""
    data = {}
    if match := re.search(r"name\s*[:\-]?\s*([A-Za-z ]+)", user_message, re.I):
        data["Name"] = match.group(1).strip()
    if match := re.search(r"(?:phone|mobile)\s*[:\-]?\s*(\+?\d{7,15})", user_message, re.I):
        data["Phone"] = match.group(1).strip()
    if match := re.search(r"(?:date|issue date)\s*[:\-]?\s*([\w\s\-\/]+)", user_message, re.I):
        data["Date of Issue"] = match.group(1).strip()
    if match := re.search(r"(?:ref(?:erence)? id)\s*[:\-]?\s*([\w\-]+)", user_message, re.I):
        data["Reference ID"] = match.group(1).strip()
    if match := re.search(r"(?:issue|problem|desc(?:ription)?)\s*[:\-]?\s*(.+)", user_message, re.I):
        data["Issue Description"] = match.group(1).strip()
    return data

def build_reply(user_id: str, user_message: str):
    now = time.time()

    # Reset session on "restart"/"reset"
    if user_message.strip().lower() in ["restart", "reset"]:
        sessions.pop(user_id, None)
        return "Session reset. Let's start over. Please provide your Name."

    # Check existing session + timeout
    session = sessions.get(user_id)
    if session:
        last_active = session.get("last_active", now)
        if now - last_active > SESSION_TIMEOUT:
            sessions.pop(user_id, None)  # expired
            return "Session expired due to inactivity. Let's start over. Please provide your Name."

    # Initialize session if not exists
    if not session:
        session = {
            "data": {f: None for f in REQUIRED_FIELDS},
            "last_active": now
        }

    data = session["data"]

    # Try extracting fields
    extracted = extract_fields(user_message)
    for k, v in extracted.items():
        if v and not data.get(k):
            data[k] = v

    # If no keyword match, assign message to first missing field
    if not extracted:
        for field in REQUIRED_FIELDS:
            if not data[field]:
                data[field] = user_message.strip()
                break

    session["last_active"] = now
    sessions[user_id] = session

    # Check completeness
    missing = [f for f in REQUIRED_FIELDS if not data[f]]
    if not missing:
        reply = (
            "Following data has been collected:\n" +
            "\n".join([f"{f}: {data[f]}" for f in REQUIRED_FIELDS]) +
            "\nThank you!"
        )
        sessions.pop(user_id)  # clear session
    else:
        reply = (
            "I still need the following details:\n" +
            ", ".join(missing)
        )

    return reply

def send_whatsapp_message(to_number: str, message: str):
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message}
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=10)
    if resp.status_code >= 400:
        print("Meta response:", resp.status_code, resp.text)

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
        msg = (data.get("entry",[{}])[0]
                  .get("changes",[{}])[0]
                  .get("value",{})
                  .get("messages",[{}])[0])

        if msg.get("type") != "text":
            return {"status": "ignored_non_text"}

        user_message = msg["text"]["body"]
        from_number = msg["from"]

        reply = build_reply(from_number, user_message)
        send_whatsapp_message(from_number, reply)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}
