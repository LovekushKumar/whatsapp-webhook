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
    try:
        body = await request.json()
        print("Incoming body:", body)  # 👈 log full payload

        entry = body.get("entry", [])
        if not entry:
            return PlainTextResponse("no entry", status_code=200)

        changes = entry[0].get("changes", [])
        if not changes:
            return PlainTextResponse("no changes", status_code=200)

        value = changes[0].get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return PlainTextResponse("no messages", status_code=200)

        msg = messages[0]
        from_number = msg.get("from")
        text = msg.get("text", {}).get("body", "").strip()

        print(f"Message from {from_number}: {text}")  # 👈 log message

    except Exception as e:
        print("Webhook error:", e)
        return PlainTextResponse("error", status_code=200)



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
