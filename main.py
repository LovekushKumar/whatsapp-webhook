import os
import re
import time
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# Required fields
REQUIRED_FIELDS = ["name", "phone", "date_of_issue", "reference_id", "issue_description"]

# Sessions { user_id: {"data": {}, "last_activity": timestamp} }
user_sessions = {}

# Session timeout in seconds (configurable)
SESSION_TIMEOUT = 300  # 5 minutes


def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "text": {"body": message},
    }
    response = requests.post(url, headers=headers, json=payload)
    print("Meta response:", response.status_code, response.text)
    return response.json()


def extract_fields(message):
    """Extracts any known field values from user input"""
    data = {}

    # Name
    name_match = re.search(r"(?:my name is|i am|this is)\s+([A-Za-z ]+)", message, re.I)
    if name_match:
        data["name"] = name_match.group(1).strip()

    # Phone
    phone_match = re.search(r"\b\d{10,}\b", message)
    if phone_match:
        data["phone"] = phone_match.group(0)

    # Date of issue (dd-mm-yyyy or dd/mm/yyyy)
    date_match = re.search(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", message)
    if date_match:
        data["date_of_issue"] = date_match.group(0)

    # Reference ID (simple alphanumeric, adjust as needed)
    ref_match = re.search(r"\b(?:ref|id)[: ]?([A-Za-z0-9_-]+)", message, re.I)
    if ref_match:
        data["reference_id"] = ref_match.group(1)

    # Issue description → if message contains "issue/problem/not working"
    if re.search(r"(issue|problem|not working|error)", message, re.I):
        data["issue_description"] = message

    return data


def get_missing_fields(collected):
    return [f for f in REQUIRED_FIELDS if f not in collected]


@app.get("/webhook")
async def verify(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return JSONResponse(content=int(params["hub.challenge"]))
    return JSONResponse(content="Invalid verification token", status_code=403)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("Incoming webhook:", data)

    try:
        if "entry" in data:
            for entry in data["entry"]:
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    messages = value.get("messages", [])
                    if not messages:
                        continue

                    msg = messages[0]
                    user_id = msg["from"]
                    user_msg = msg.get("text", {}).get("body", "")

                    # Reset/restart command
                    if user_msg.strip().lower() in ["reset", "restart"]:
                        user_sessions.pop(user_id, None)
                        send_whatsapp_message(user_id, "Thanks for confirmation. Session has been reset.")
                        continue

                    # Check session timeout
                    session = user_sessions.get(user_id)
                    now = time.time()
                    if session and now - session["last_activity"] > SESSION_TIMEOUT:
                        user_sessions.pop(user_id, None)
                        send_whatsapp_message(user_id, "Session expired due to inactivity. Please start again.")
                        continue

                    # Create new session if not exists
                    if user_id not in user_sessions:
                        user_sessions[user_id] = {"data": {}, "last_activity": now}

                    session = user_sessions[user_id]

                    # Extract fields from message
                    extracted = extract_fields(user_msg)
                    session["data"].update(extracted)
                    session["last_activity"] = now

                    # Check missing fields
                    missing = get_missing_fields(session["data"])

                    if not missing:
                        # All collected → send summary
                        collected = session["data"]
                        summary = (
                            f"Following data has been collected:\n"
                            f"Name: {collected['name']}\n"
                            f"Phone: {collected['phone']}\n"
                            f"Date of Issue: {collected['date_of_issue']}\n"
                            f"Reference ID: {collected['reference_id']}\n"
                            f"Issue: {collected['issue_description']}\n\n"
                            "Thank you!"
                        )
                        send_whatsapp_message(user_id, summary)
                    else:
                        # Ask for missing fields
                        send_whatsapp_message(user_id, f"Please provide the following missing details: {', '.join(missing)}")

    except Exception as e:
        print("Error:", str(e))

    return JSONResponse(content="EVENT_RECEIVED")
