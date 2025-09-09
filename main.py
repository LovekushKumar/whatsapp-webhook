import os
import json
import traceback
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from groq import Groq

app = FastAPI()

# -------------------------
# WhatsApp Config
# -------------------------
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# -------------------------
# Google Sheets Config
# -------------------------
SPREADSHEET_ID = "1l3I0SOf2osFXA7iaBRd8d6qbS_S-cJW14__lspuEFts"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds_info_str = os.getenv("GOOGLE_CREDS_JSON")
if not creds_info_str:
    raise ValueError("GOOGLE_CREDS_JSON env var missing")
creds_info = json.loads(creds_info_str)
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
sheets_service = build("sheets", "v4", credentials=creds)

# -------------------------
# Groq Config
# -------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY env var missing")
groq_client = Groq(api_key=GROQ_API_KEY)

# -------------------------
# Session Store
# -------------------------
SESSIONS = {}
REQUIRED_FIELDS = ["Name", "Phone", "Date of Issue", "Reference ID", "Issue Description"]

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
    resp = requests.post(url, headers=headers, json=payload)
    print("WA response:", resp.status_code, resp.text)
    return resp

# -------------------------
# Helpers
# -------------------------
def extract_fields_with_ai(user_input: str, current_fields: dict) -> dict:
    prompt = (
        f"Extract the following fields from the user text into JSON with keys {REQUIRED_FIELDS}. "
        "If a field is missing, return null for it. "
        f"Already have: {current_fields}\n\n"
        f"User text: \"{user_input}\""
    )
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        content = resp.choices[0].message.content
        parsed = json.loads(content)
        return {k: parsed.get(k) if parsed.get(k) not in [None, "", "null"] else None for k in REQUIRED_FIELDS}
    except Exception as e:
        print("AI extraction failed:", e)
        traceback.print_exc()
        return {k: None for k in REQUIRED_FIELDS}

def save_to_sheet(fields: dict):
    values = [[
        fields.get("Name", ""),
        fields.get("Phone", ""),
        fields.get("Date of Issue", ""),
        fields.get("Reference ID", ""),
        fields.get("Issue Description", "")
    ]]
    body = {"values": values}
    resp = sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Sheet1!A:E",
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()
    print("Saved to Sheets:", resp)

# -------------------------
# Webhook
# -------------------------
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    print("Incoming webhook:", json.dumps(body))

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                continue

            msg = messages[0]
            from_number = msg.get("from")
            text = msg.get("text", {}).get("body", "").strip()
            contacts = value.get("contacts", [{}])
            contact_name = contacts[0].get("profile", {}).get("name", from_number)

            session = SESSIONS.get(from_number, {"fields": {}, "pending_confirmation": False})

            # Reset commands
            if text.lower() in {"reset", "restart", "q", "quit", "exit"}:
                SESSIONS.pop(from_number, None)
                send_whatsapp_message(from_number, f"Hi {contact_name}, session reset. Please provide your details again.")
                continue

            # Confirmation step
            if session.get("pending_confirmation"):
                if text.lower() in {"yes", "y"}:
                    save_to_sheet(session["fields"])
                    send_whatsapp_message(from_number, "✅ Saved. Thank you!")
                    SESSIONS.pop(from_number, None)
                elif text.lower() in {"no", "n"}:
                    SESSIONS.pop(from_number, None)
                    send_whatsapp_message(from_number, "Okay — let's start over. Please send your details.")
                else:
                    send_whatsapp_message(from_number, "Please reply Yes or No.")
                continue

            # Greeting
            if text.lower() in {"hi", "hello", "hey"}:
                send_whatsapp_message(from_number, f"Hi {contact_name}, please provide your details and query.")
                SESSIONS[from_number] = session
                continue

            # Extract fields with AI
            extracted = extract_fields_with_ai(text, session["fields"])
            for k, v in extracted.items():
                if v and not session["fields"].get(k):
                    session["fields"][k] = v

            missing = [f for f in REQUIRED_FIELDS if not session["fields"].get(f)]
           
