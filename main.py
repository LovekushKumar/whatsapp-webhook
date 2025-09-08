import re
import time
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import requests
import os

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "testtoken")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

SESSIONS: dict[str, dict] = {}

REQUIRED_FIELDS = ["name", "phone", "date_of_issue", "reference_id", "issue_description"]
SESSION_TIMEOUT = 300  # 5 minutes


def extract_fields(message: str) -> dict:
    fields = {}

    # --- Name ---
    name_match = re.search(r"(?:my name is|i am|this is)\s+([A-Za-z ]+)", message, re.I)
    if name_match:
        fields["name"] = name_match.group(1).strip()

    # --- Phone ---
    phone_match = re.search(r"(\+?\d{10,15})", message)
    if phone_match:
        fields["phone"] = phone_match.group(1).strip()

    # --- Date of Issue ---
    date_match = re.search(r"(\d{2}[/-]\d{2}[/-]\d{4})", message)
    if date_match:
        fields["date_of_issue"] = date_match.group(1).strip()

    # --- Reference ID ---
    ref_match = re.search(r"(?:ref(?:erence)?(?: id)? is)\s*([A-Za-z0-9]+)", message, re.I)
    if ref_match:
        fields["reference_id"] = ref_match.group(1).strip()

    # --- Issue description ---
    issue_match = re.search(r"(?:issue (?:is|description is|facing is)[:\- ]*)(.*)", message, re.I)
    if issue_match:
        fields["issue_description"] = issue_match.group(1).strip()

    return fields


def get_missing_fields(data: dict) -> list:
    return [f for f in REQUIRED_FIELDS if not data.get(f)]


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    entry = body.get("entry", [])[0]
    changes = entry.get("changes", [])[0]
    value = changes.get("value", {})
    messages = value.get("messages", [])

    if not messages:
        return PlainTextResponse("ok", status_code=200)

    msg = messages[0]
    from_number = msg["from"]
    text = msg.get("text", {}).get("body", "").strip()

    # --- Reset session manually ---
    if text.lower() in ["reset", "restart"]:
        SESSIONS.pop(from_number, None)
        send_whatsapp_message(from_number, "Thanks for confirmation, session restarted.")
        return PlainTextResponse("ok", status_code=200)

    # --- Create or load session ---
    session = SESSIONS.get(from_number, {"data": {}, "last_active": time.time()})

    # --- Timeout session ---
    if time.time() - session["last_active"] > SESSION_TIMEOUT:
        session = {"data": {}, "last_active": time.time()}

    # --- Extract fields ---
    extracted = extract_fields(text)
    for k, v in extracted.items():
        if not session["data"].get(k):  # don’t overwrite existing
            session["data"][k] = v

    session["last_active"] = time.time()
    SESSIONS[from_number] = session

    missing = get_missing_fields(session["data"])

    if not missing:
        data = session["data"]
        reply = (
            f"Following data has been collected:\n"
            f"Name: *{data['name']}*\n"
            f"Phone: *{data['phone']}*\n"
            f"Date of Issue: *{data['date_of_issue']}*\n"
            f"Reference ID: *{data['reference_id']}*\n"
            f"Issue Description: *{data['issue_description']}*\n\n"
            "Thank you!"
        )
        send_whatsapp_message(from_number, reply)
        SESSIONS.pop(from_number, None)  # clear after submission
    else:
        send_whatsapp_message(from_number, f"Please provide the following missing fields: {', '.join(missing)}")

    return PlainTextResponse("ok", status_code=200)


def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }
    requests.post(url, headers=headers, json=payload)
