import os
import json
import re
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build
from groq import Groq

app = FastAPI()

# Sessions { from_number: {...} }
sessions = {}

# WhatsApp
WHATSAPP_API_URL = "https://graph.facebook.com/v17.0"
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")

# Google Sheets
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
if not GOOGLE_CREDS_JSON:
    raise ValueError("Missing GOOGLE_CREDS_JSON env var")
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = service_account.Credentials.from_service_account_info(
    creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
sheets_service = build("sheets", "v4", credentials=creds)

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("Missing GROQ_API_KEY env var")
groq_client = Groq(api_key=GROQ_API_KEY)

# Required fields
REQUIRED_FIELDS = ["Name", "Phone", "Date of Issue", "Reference ID", "Issue Description"]

# Exit commands
EXIT_COMMANDS = {"reset", "restart", "q", "quit", "exit"}


def send_whatsapp_message(to: str, message: str):
    url = f"{WHATSAPP_API_URL}/{WHATSAPP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "text": {"body": message}}
    httpx.post(url, headers=headers, json=data, timeout=10)


def extract_fields_with_ai(user_input: str, session_data: dict) -> dict:
    """Use Groq LLM to extract structured fields."""
    prompt = f"""
You are an AI assistant. Extract the following fields from the text:
- Name
- Phone
- Date of Issue
- Reference ID
- Issue Description

Text: {user_input}

Already captured: {session_data}
Return JSON with keys: {REQUIRED_FIELDS}.
If missing, keep value as null.
    """
    resp = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    try:
        parsed = json.loads(resp.choices[0].message.content)
    except Exception:
        parsed = {k: None for k in REQUIRED_FIELDS}
    return parsed


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    entry = data.get("entry", [])[0]
    changes = entry.get("changes", [])[0]
    value = changes.get("value", {})

    # Message details
    messages = value.get("messages", [])
    if not messages:
        return JSONResponse(content={"status": "ignored"})

    msg = messages[0]
    from_number = msg.get("from")
    user_text = msg.get("text", {}).get("body", "").strip()

    # Contact name
    contacts = value.get("contacts", [{}])
    contact_name = contacts[0].get("profile", {}).get("name", from_number)

    # Session
    session = sessions.get(from_number, {"fields": {}, "confirmed": False})

    # Exit commands
    if user_text.lower() in EXIT_COMMANDS:
        sessions.pop(from_number, None)
        send_whatsapp_message(from_number, f"Session reset, {contact_name}. Please start again.")
        return JSONResponse(content={"status": "reset"})

    # Confirmation step
    if session.get("pending_confirmation"):
        if user_text.lower() in ["yes", "y"]:
            values = [[session["fields"].get(f, "") for f in REQUIRED_FIELDS]]
            sheets_service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range="Sheet1!A:E",
                valueInputOption="RAW",
                body={"values": values},
            ).execute()
            send_whatsapp_message(from_number, "✅ Your details have been saved. Thank you!")
            sessions.pop(from_number, None)
        else:
            send_whatsapp_message(from_number, "❌ Details discarded. Please start again.")
            sessions.pop(from_number, None)
        return JSONResponse(content={"status": "confirmation"})

    # AI extraction
    extracted = extract_fields_with_ai(user_text, session["fields"])
    for k, v in extracted.items():
        if v and not session["fields"].get(k):
            session["fields"][k] = v

    missing = [f for f in REQUIRED_FIELDS if not session["fields"].get(f)]

    if not missing:
        summary = "\n".join([f"{k}: {v}" for k, v in session["fields"].items()])
        send_whatsapp_message(
            from_number,
            f"Here is what I captured:\n\n{summary}\n\nIs this correct? (yes/no)"
        )
        session["pending_confirmation"] = True
    else:
        send_whatsapp_message(
            from_number,
            f"Hi {contact_name}, please provide missing details: {', '.join(missing)}"
        )

    sessions[from_number] = session
    return JSONResponse(content={"status": "ok"})


@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == os.getenv("VERIFY_TOKEN"):
        return JSONResponse(content=int(challenge))
    return JSONResponse(content={"status": "forbidden"}, status_code=403)
