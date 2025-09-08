from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import os, re, requests

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# Session store (in-memory)
SESSIONS = {}

# --- Helper to send WhatsApp reply ---
def send_whatsapp_message(to: str, message: str):
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    print("Meta response:", r.status_code, r.text)


@app.get("/webhook")
async def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(challenge, status_code=200)
    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    try:
        msg = (data.get("entry", [{}])[0]
                  .get("changes", [{}])[0]
                  .get("value", {})
                  .get("messages", [{}])[0])
        if msg.get("type") != "text":
            return {"status": "ignored"}

        user_message = msg["text"]["body"]
        from_number = msg["from"]

        # Initialize session if new user
        if from_number not in SESSIONS:
            SESSIONS[from_number] = {
                "step": "greet",
                "data": {}
            }

        session = SESSIONS[from_number]
        step = session["step"]

        # Step-by-step flow
        if step == "greet":
            send_whatsapp_message(from_number,
                "Hi 👋, In order to submit details, I will ask a few details. Please provide them correctly.\n\nPlease provide your *Name*:")
            session["step"] = "name"

        elif step == "name":
            match = re.search(r"(?:i am|my name is|this is)\s+(.*)", user_message, re.I)
            name = match.group(1).strip() if match else user_message.strip()
            session["data"]["name"] = name
            send_whatsapp_message(from_number, "Got it ✅\nPlease provide your *Phone Number*:")
            session["step"] = "phone"

        elif step == "phone":
            if re.fullmatch(r"\d{10,15}", user_message.strip()):
                session["data"]["phone"] = user_message.strip()
                send_whatsapp_message(from_number, "Thanks 📱\nPlease provide the *Date of Issue* (dd-mm-yyyy):")
                session["step"] = "date"
            else:
                send_whatsapp_message(from_number, "❌ Invalid phone. Please provide only numbers (10–15 digits).")

        elif step == "date":
            if re.fullmatch(r"\d{2}-\d{2}-\d{4}", user_message.strip()):
                session["data"]["date_of_issue"] = user_message.strip()
                send_whatsapp_message(from_number, "Got it 📅\nPlease provide your *Reference ID*:")
                session["step"] = "refid"
            else:
                send_whatsapp_message(from_number, "❌ Invalid date format. Please use dd-mm-yyyy.")

        elif step == "refid":
            refid = re.sub(r"[^a-zA-Z0-9]", "", user_message)
            if refid:
                session["data"]["reference_id"] = refid
                send_whatsapp_message(from_number, "Thanks 📝\nPlease describe your *Issue*:")
                session["step"] = "issue"
            else:
                send_whatsapp_message(from_number, "❌ Invalid Reference ID. Please provide alphanumeric only.")

        elif step == "issue":
            issue_text = re.sub(r"^(my issue is|i am facing issue|issue is)\s*", "", user_message, flags=re.I).strip()
            session["data"]["issue_description"] = issue_text

            # Final confirmation
            summary = (
                "✅ Following details are collected:\n\n"
                f"*Name*: {session['data']['name']}\n"
                f"*Phone*: {session['data']['phone']}\n"
                f"*Date of Issue*: {session['data']['date_of_issue']}\n"
                f"*Reference ID*: {session['data']['reference_id']}\n"
                f"*Issue*: {session['data']['issue_description']}\n\n"
                "All details submitted successfully. Thank you! 🎉"
            )
            send_whatsapp_message(from_number, summary)

            # Clear session
            del SESSIONS[from_number]

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}
