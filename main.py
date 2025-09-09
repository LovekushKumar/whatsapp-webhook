import os
import logging
import requests
from fastapi import FastAPI, Request
from pydantic import BaseModel
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from datetime import datetime
from typing import Dict

# -----------------------
# Config
# -----------------------
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

REQUIRED_FIELDS = ["Name", "Phone", "Date of Issue", "Reference ID", "Issue Description"]

sessions: Dict[str, dict] = {}

app = FastAPI()
logging.basicConfig(level=logging.DEBUG)

# -----------------------
# Google Sheets Setup
# -----------------------
SERVICE_ACCOUNT_FILE = "service_account.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

credentials = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
service = build("sheets", "v4", credentials=credentials)

def save_to_sheet(data: dict):
    logging.debug(f"Saving data to sheet: {data}")
    values = [[
        data.get("Name", ""),
        data.get("Phone", ""),
        data.get("Date of Issue", ""),
        data.get("Reference ID", ""),
        data.get("Issue Description", "")
    ]]
    body = {"values": values}
    service.spreadsheets().values().append(
        spreadsheetId=GOOGLE_SHEET_ID,
        range="Sheet1!A:E",
        valueInputOption="RAW",
        body=body
    ).execute()

# -----------------------
# WhatsApp helper
# -----------------------
def send_whatsapp_message(to: str, text: str):
    url = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    payload = {"messaging_product": "whatsapp", "to": to, "text": {"body": text}}
    requests.post(url, headers=headers, json=payload)

# -----------------------
# AI helper (Groq)
# -----------------------
def extract_fields_with_ai(user_message: str, current_data: dict) -> dict:
    """
    Use Groq to extract structured fields from unstructured user text.
    """
    import openai
    openai.api_key = GROQ_API_KEY
    openai.api_base = "https://api.groq.com/openai/v1"

    prompt = f"""
    You are an information extraction assistant.
    Extract the following fields from the user input. 
    Already collected: {current_data}.
    User input: {user_message}.
    Required fields: {REQUIRED_FIELDS}.
    Return a JSON with only the fields and values found. 
    Do not hallucinate missing values.
    """

    resp = openai.ChatCompletion.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "system", "content": "You are an extractor."},
                  {"role": "user", "content": prompt}],
        temperature=0
    )

    try:
        extracted = eval(resp["choices"][0]["message"]["content"])  # quick parse
        return extracted if isinstance(extracted, dict) else {}
    except Exception as e:
        logging.error(f"AI parsing error: {e}")
        return {}

# -----------------------
# Webhook handler
# -----------------------
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    logging.debug(f"Incoming data: {data}")

    entry = data.get("entry", [])[0]
    changes = entry.get("changes", [])[0]
    value = changes.get("value", {})
    messages = value.get("messages", [])
    if not messages:
        return {"status": "ignored"}

    message = messages[0]
    from_number = message["from"]
    user_text = message.get("text", {}).get("body", "").strip()

    # Extract contact name
    contacts = value.get("contacts", [{}])
    contact_name = contacts[0].get("profile", {}).get("name", from_number)

    # Session init
    session = sessions.get(from_number, {"data": {}, "confirmed": False})

    # If already confirmed, reset session
    if session.get("confirmed"):
        sessions.pop(from_number, None)

    # Pass user text to AI
    extracted = extract_fields_with_ai(user_text, session["data"])
    session["data"].update(extracted)

    # Check if all fields collected
    missing = [f for f in REQUIRED_FIELDS if f not in session["data"] or not session["data"][f]]

    if not missing:
        # Ask for confirmation
        summary = "\n".join([f"{k}: {v}" for k, v in session["data"].items()])
        send_whatsapp_message(from_number, f"Here’s what I collected:\n{summary}\n\nIs this correct? (Yes/No)")
        session["awaiting_confirmation"] = True
    elif "awaiting_confirmation" in session and session["awaiting_confirmation"]:
        if user_text.lower() == "yes":
            save_to_sheet(session["data"])
            send_whatsapp_message(from_number, "✅ Your details have been saved successfully. Thank you!")
            session["confirmed"] = True
        elif user_text.lower() == "no":
            send_whatsapp_message(from_number, "❌ Okay, let's try again. Please provide your details.")
            sessions.pop(from_number, None)
        else:
            send_whatsapp_message(from_number, "Please reply Yes or No.")
    else:
        # Ask for missing fields (fallback safety)
        send_whatsapp_message(from_number, f"Hi {contact_name}, I still need: {', '.join(missing)}")

    sessions[from_number] = session
    return {"status": "success"}
