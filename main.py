import os
import json
import re
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import OpenAI

# ---------------- CONFIG ----------------
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "verifyme")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

app = FastAPI()

# Session store {user_id: {"fields": {...}}}
sessions = {}

# ---------------- UTIL: SEND MESSAGE ----------------
def send_whatsapp_message(to: str, message: str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }
    r = requests.post(url, headers=headers, json=payload)
    print("Outgoing:", payload, "Response:", r.status_code, r.text)
    return r.status_code, r.text


# ---------------- AI EXTRACTION ----------------
def extract_fields_with_ai(user_input: str, session: dict) -> dict:
    """
    Use AI to extract structured fields, but with rule-based fallback
    for single-field user replies (phone, date, ref ID).
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

        Return valid JSON with keys: name, phone, date_of_issue, reference_id, issue_description.
        If a field is not present, return null.
        """

        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )

        content = resp.choices[0].message.content.strip()

        # ---- Safe JSON parsing ----
        parsed = {}
        try:
            # Try direct JSON parse
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # If model added extra text, try to extract JSON block
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except Exception:
                    parsed = {}
            else:
                parsed = {}

        # Merge into fields
        for key, value in parsed.items():
            if value and str(value).lower() != "null":
                fields[key] = str(value).strip()

    except Exception as e:
        print("AI extraction error:", e)

    return fields


# ---------------- WEBHOOK ----------------
@app.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return JSONResponse(content=int(params.get("hub.challenge")))
    return JSONResponse(content="Verification failed", status_code=403)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("Incoming:", json.dumps(data))

    if "entry" in data:
        for entry in data["entry"]:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])

                if messages:
                    for msg in messages:
                        from_number = msg["from"]
                        session = sessions.setdefault(from_number, {"fields": {}})

                        # -------- Reset session --------
                        text = msg.get("text", {}).get("body", "").strip().lower()
                        if text in ["reset", "restart", "exit", "quit", "q"]:
                            sessions[from_number] = {"fields": {}}
                            send_whatsapp_message(from_number, "Session reset. Please start again.")
                            continue

                        # -------- Handle button reply --------
                        if msg.get("type") == "interactive":
                            reply_id = msg["interactive"]["button_reply"]["id"]
                            if reply_id == "confirm_yes":
                                send_whatsapp_message(from_number, "✅ Thanks! Your details are confirmed.")
                                sessions[from_number] = {"fields": {}}  # clear session
                                continue
                            elif reply_id == "confirm_no":
                                send_whatsapp_message(from_number, "❌ Okay, let's try again. Please re-enter your details.")
                                sessions[from_number] = {"fields": {}}
                                continue

                        # -------- Handle normal text --------
                        if msg.get("type") == "text":
                            user_input = msg["text"]["body"]
                            fields = extract_fields_with_ai(user_input, session, from_number)
                            session["fields"] = fields

                            required = ["name", "phone", "date_of_issue", "reference_id", "issue_description"]
                            missing = [f for f in required if f not in fields]

                            if missing:
                                send_whatsapp_message(from_number, f"Hi, please provide: {', '.join(missing)}")

    return JSONResponse(content={"status": "ok"})
