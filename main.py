import os
import json
import requests
import traceback
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
SPREADSHEET_ID = "1l3I0SOf2osFXA7iaBRd8d6qbS_S-cJW14__lspuEFts"  # fixed sheet id
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Load Google credentials from environment variable
creds_info_str = os.getenv("GOOGLE_CREDS_JSON")
if not creds_info_str:
    raise ValueError("GOOGLE_CREDS_JSON env var missing")
creds_info = json.loads(creds_info_str)
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
sheets_service = build("sheets", "v4", credentials=creds)

# -------------------------
# AI Config (Groq)
# -------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY)

# -------------------------
# Session Store
# -------------------------
SESSIONS = {}
REQUIRED_FIELDS = ["Name", "Phone", "Date of Issue", "Reference ID", "Issue Description"]
RESET_COMMANDS = {"reset", "restart", "q", "quit", "exit"}
GREETINGS = {"hi", "hello", "hey"}

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
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        print("Outgoing:", json.dumps(payload), "Response:", resp.status_code, resp.text)
    except Exception as e:
        print("WhatsApp send error:", e)
        traceback.print_exc()

# -------------------------
# AI Extraction
# -------------------------
def extract_fields_with_ai(user_input: str, current_fields: dict) -> dict:
    prompt = (
        f"Extract these fields into JSON: {REQUIRED_FIELDS}. "
        f"Already captured: {current_fields}. "
        f"User text: \"{user_input}\". "
        "Return JSON only."
    )
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = resp.choices[0].message.content
        parsed = json.loads(content)
        return {f: parsed.get(f) or None for f in REQUIRED_FIELDS}
    except Exception as e:
        print("AI extraction error:", e)
        traceback.print_exc()
        return {f: None for f in REQUIRED_FIELDS}

# -------------------------
# Google Sheets Save
# -------------------------
def save_to_sheet(fields: dict):
    values = [[
        fields.get("Name", ""),
        fields.get("Phone", ""),
        fields.get("Date of Issue", ""),
        fields.get("Reference ID", ""),
        fields.get("Issue Description", "")
    ]]
    body = {"values": values}
    sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Sheet1!A:E",
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

# -------------------------
# Webhook
# -------------------------
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    print("Incoming:", json.dumps(body))

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                continue

            msg = messages[0]
            from_number = msg.get("from")
            text = msg.get("text", {}).get("body", "").strip()
            contact_name = value.get("contacts", [{}])[0].get("profile", {}).get("name", from_number)

            session = SESSIONS.get(from_number, {"fields": {}, "pending_confirmation": False})

            # Reset commands
            if text.lower() in RESET_COMMANDS:
                SESSIONS.pop(from_number, None)
                send_whatsapp_message(from_number, f"Session cleared. Hi {contact_name}, please start again.")
                continue

            # Confirmation
            if session.get("pending_confirmation"):
                if text.lower() in {"yes", "y"}:
                    save_to_sheet(session["fields"])
                    send_whatsapp_message(from_number, "✅ Saved. Thank you!")
                    SESSIONS.pop(from_number, None)
                elif text.lower() in {"no", "n"}:
                    SESSIONS.pop(from_number, None)
                    send_whatsapp_message(from_number, "Okay, let's start again. Please send your details.")
                else:
                    send_whatsapp_message(from_number, "Please reply Yes or No.")
                continue

            # Greeting only
            if text.lower() in GREETINGS:
                send_whatsapp_message(from_number, f"Hi {contact_name}, please provide your details and query.")
                SESSIONS[from_number] = session
                continue

            # Extract with AI
            extracted = extract_fields_with_ai(text, session["fields"])
            for k, v in extracted.items():
                if v and not session["fields"].get(k):
                    session["fields"][k] = v

            missing = [f for f in REQUIRED_FIELDS if not session["fields"].get(f)]
            if not missing:
                summary = "\n".join(f"{f}: {session['fields'][f]}" for f in REQUIRED_FIELDS)
                send_whatsapp_message(from_number, f"Here is what I captured:\n\n{summary}\n\nIs this correct? (Yes/No)")
                session["pending_confirmation"] = True
            else:
                send_whatsapp_message(from_number, f"Hi {contact_name}, please provide: {', '.join(missing)}")

            SESSIONS[from_number] = session

    return JSONResponse({"status": "ok"})

@app.get("/webhook")
async def verify(request: Request):
    if (request.query_params.get("hub.mode") == "subscribe" and
        request.query_params.get("hub.verify_token") == VERIFY_TOKEN):
        return int(request.query_params.get("hub.challenge", "0"))
    return JSONResponse({"status": "forbidden"}, status_code=403)
