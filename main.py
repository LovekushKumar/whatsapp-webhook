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

# -------------------------
# Session Store
# -------------------------
SESSIONS = {}

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
            data.get("name", ""),
            data.get("phone", ""),
            data.get("date_of_issue", ""),
            data.get("reference_id", ""),
            data.get("issue_description", "")
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
    data = await request.json()
    if "entry" in data:
        for entry in data["entry"]:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                if messages:
                    msg = messages[0]
                    from_number = msg["from"]
                    text = msg["text"]["body"]

                    # Initialize session if not exists
                    if from_number not in SESSIONS:
                        SESSIONS[from_number] = {
                            "step": 0,
                            "data": {}
                        }

                    session = SESSIONS[from_number]
                    step = session["step"]

                    if text.lower() in ["reset", "restart", "quit", "q"]:
                        del SESSIONS[from_number]
                        send_whatsapp_message(from_number, "Thanks for confirmation. Session has been reset.")
                        return {"status": "ok"}

                    # Sequential steps logic
                    if step == 0:
                        send_whatsapp_message(from_number, "Hi 👋, Please provide your *Name*:")
                        session["step"] = 1
                    elif step == 1:
                        session["data"]["Name"] = text.replace("my name is", "").replace("i am", "").replace("this is", "").strip()
                        send_whatsapp_message(from_number, "Please provide your *Phone Number*:")
                        session["step"] = 2
                    elif step == 2:
                        if text.isdigit():
                            session["data"]["Phone"] = text.strip()
                            send_whatsapp_message(from_number, "Please provide the *Date of Issue* (dd-mm-yyyy):")
                            session["step"] = 3
                        else:
                            send_whatsapp_message(from_number, "❌ Invalid phone number. Please enter digits only.")
                    elif step == 3:
                        session["data"]["Date of Issue"] = text.strip()
                        send_whatsapp_message(from_number, "Please provide *Reference ID* (alphanumeric):")
                        session["step"] = 4
                    elif step == 4:
                        if text.replace(" ", "").isalnum():
                            session["data"]["Reference ID"] = text.strip()
                            send_whatsapp_message(from_number, "Please describe the *Issue* you are facing:")
                            session["step"] = 5
                        else:
                            send_whatsapp_message(from_number, "❌ Invalid Reference ID. Please enter only alphanumeric value.")
                    elif step == 5:
                        session["data"]["Issue Description"] = text.strip()
                        save_to_sheet(session["data"])
                        summary = (
                            f"✅ Following data has been collected and submitted:\n\n"
                            f"*Name:* {session['data']['Name']}\n"
                            f"*Phone:* {session['data']['Phone']}\n"
                            f"*Date of Issue:* {session['data']['Date of Issue']}\n"
                            f"*Reference ID:* {session['data']['Reference ID']}\n"
                            f"*Issue Description:* {session['data']['Issue Description']}\n\n"
                            "Thank you! 🙏"
                        )
                        send_whatsapp_message(from_number, summary)
                        del SESSIONS[from_number]  # clear session
    return {"status": "ok"}
