import os
import json
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import requests

app = FastAPI()

# -------------------------
# WhatsApp Config
# -------------------------
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "testtoken")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# -------------------------
# Google Sheets Config
# -------------------------
SPREADSHEET_ID = "1l3I0SOf2osFXA7iaBRd8d6qbS_S-cJW14__lspuEFts"  # your sheet id
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Load Google credentials from environment variable
creds_info_str = os.getenv("GOOGLE_CREDS_JSON")
if not creds_info_str:
    raise ValueError("GOOGLE_CREDS_JSON env var missing")

creds_info = json.loads(creds_info_str)
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)

sheets_service = build("sheets", "v4", credentials=creds)



# Global in-memory sessions
sessions = {}

# -------------------------
# WhatsApp Send Function
# -------------------------
def send_whatsapp_message(to: str, message: str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    requests.post(url, headers=headers, json=payload)

# -------------------------
# Save to Google Sheets
# -------------------------
def save_to_sheet(data):
    print("DEBUG Data to save:", data)  # 👈 Add this log
    
    body = {
        "values": [[
        data.get("Name", ""),
        data.get("Phone", ""),
        data.get("Date of Issue", ""),
        data.get("Reference ID", ""),
        data.get("Issue Description", "")
        ]]
    }
    sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Sheet1!A:E",
        valueInputOption="RAW",
        body=body
    ).execute()


# -------------------------
# Webhook Verify
# -------------------------
@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return JSONResponse(content={"error": "Invalid token"}, status_code=403)

# -------------------------
# Webhook Handler
# -------------------------
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.debug(f"Incoming webhook body: {body}")

    # Ignore cron/health-check requests (no WhatsApp payload)
    entry = body.get("entry", [])
    if not entry or "changes" not in entry[0]:
        return {"status": "ignored"}

    changes = entry[0]["changes"]
    if not changes or "value" not in changes[0]:
        return {"status": "ignored"}

    value = changes[0]["value"]
    messages = value.get("messages", [])

    if not messages:
        return {"status": "ignored"}

    msg = messages[0]
    from_number = msg["from"]

    # Extract contact name (fallback to number)
    contacts = value.get("contacts", [{}])
    contact_name = contacts[0].get("profile", {}).get("name", from_number)

    # Start session if new user
    if from_number not in sessions:
        sessions[from_number] = {"step": 0, "data": {}}

    session = sessions[from_number]

    # Conversation flow
    if session["step"] == 0:
        send_whatsapp_message(from_number, f"Hi {contact_name}, Please provide your Name")
        session["step"] = 1

    elif session["step"] == 1:
        session["data"]["Name"] = msg.get("text", {}).get("body", "")
        send_whatsapp_message(from_number, "Please provide your Phone number")
        session["step"] = 2

    elif session["step"] == 2:
        session["data"]["Phone"] = msg.get("text", {}).get("body", "")
        send_whatsapp_message(from_number, "Please provide the Date of Issue")
        session["step"] = 3

    elif session["step"] == 3:
        session["data"]["Date of Issue"] = msg.get("text", {}).get("body", "")
        send_whatsapp_message(from_number, "Please provide your Reference ID")
        session["step"] = 4

    elif session["step"] == 4:
        session["data"]["Reference ID"] = msg.get("text", {}).get("body", "")
        send_whatsapp_message(from_number, "Please provide Issue Description")
        session["step"] = 5

    elif session["step"] == 5:
        session["data"]["Issue Description"] = msg.get("text", {}).get("body", "")
        logger.debug(f"Data to save: {session['data']}")

        try:
            save_to_sheet(session["data"])
            send_whatsapp_message(from_number, "✅ Thank you! Your issue has been recorded.")
        except Exception as e:
            logger.error(f"Error saving to sheet: {e}")
            send_whatsapp_message(from_number, "⚠️ Sorry, there was an error saving your issue.")

        # Clean up session after completion
        del sessions[from_number]

    return {"status": "ok"}
