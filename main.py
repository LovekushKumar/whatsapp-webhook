import os
import json
import requests
import traceback
import re
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

def extract_fields_with_ai(user_input: str, session: dict, user_id: str) -> dict:
    """
    Use AI to extract structured fields, but with rule-based fallback
    for single-field user replies (phone, date, ref ID).
    Sends WhatsApp interactive buttons once all fields are captured.
    """
    fields = session.get("fields", {})

    # ---------- RULE-BASED FALLBACKS ----------
    if "reference_id" not in fields and re.match(r"^[A-Za-z0-9_-]{3,15}$", user_input.strip()):
        fields["reference_id"] = user_input.strip()
        return fields

    if "phone" not in fields and re.match(r"^\+?\d{7,15}$", user_input.strip()):
        fields["phone"] = user_input.strip()
        return fields

    if "date_of_issue" not in fields and re.search(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", user_input):
        fields["date_of_issue"] = user_input.strip()
        return fields

    # ---------- AI EXTRACTION ----------
    try:
        client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
        prompt = f"""
        Extract the following fields from this user input:
        - Name
        - Phone
        - Date of Issue
        - Reference ID
        - Issue Description

        User input: "{user_input}"

        Return JSON with keys: name, phone, date_of_issue, reference_id, issue_description.
        If a field is not present, return null.
        """

        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )

        content = resp.choices[0].message.content.strip()
        parsed = json.loads(content)

        for key, value in parsed.items():
            if value and value.lower() != "null":
                fields[key] = value.strip()

    except Exception as e:
        print("AI extraction error:", e)

    # ---------- SEND CONFIRMATION WITH BUTTONS ----------
    required = ["name", "phone", "date_of_issue", "reference_id", "issue_description"]
    if all(f in fields and fields[f] for f in required):
        confirmation = (
            f"Here is what I captured:\n\n"
            f"Name: {fields.get('name')}\n"
            f"Phone: {fields.get('phone')}\n"
            f"Date of Issue: {fields.get('date_of_issue')}\n"
            f"Reference ID: {fields.get('reference_id')}\n"
            f"Issue Description: {fields.get('issue_description')}\n\n"
            f"Please confirm:"
        )

        url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": user_id,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": confirmation},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "confirm_yes", "title": "✅ Yes"}},
                        {"type": "reply", "reply": {"id": "confirm_no", "title": "❌ No"}},
                    ]
                },
            },
        }
        requests.post(url, headers=headers, json=payload)

    return fields




@app.get("/webhook")
async def verify(request: Request):
    if (request.query_params.get("hub.mode") == "subscribe" and
        request.query_params.get("hub.verify_token") == VERIFY_TOKEN):
        return int(request.query_params.get("hub.challenge", "0"))
    return JSONResponse({"status": "forbidden"}, status_code=403)
