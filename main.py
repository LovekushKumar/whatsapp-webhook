from fastapi import FastAPI, Request
import os, requests, re, time

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# Session storage {user: {"data": {}, "last_active": timestamp}}
user_sessions = {}
SESSION_TIMEOUT = 300  # 5 minutes

required_fields = ["name", "phone", "date_of_issue", "reference_id", "issue_description"]

def extract_fields(message: str, session_data: dict):
    text = message.lower()

    # NAME
    name_match = re.search(r"(?:my name is|i am|this is)\s+([a-zA-Z ]+)", text)
    if name_match and not session_data.get("name"):
        session_data["name"] = name_match.group(1).strip().title()
    elif not session_data.get("name") and re.fullmatch(r"[a-zA-Z ]{3,}", message.strip()):
        session_data["name"] = message.strip().title()

    # PHONE
    phone_match = re.search(r"\b\d{10,}\b", message)
    if phone_match and not session_data.get("phone"):
        session_data["phone"] = phone_match.group(0)

    # DATE
    date_match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", message)
    if date_match and not session_data.get("date_of_issue"):
        session_data["date_of_issue"] = date_match.group(0)

    # REFERENCE ID
    ref_match = re.search(r"(?:reference id|ref id|id)\s*(?:is|:)?\s*([a-zA-Z0-9_-]+)", text)
    if ref_match and not session_data.get("reference_id"):
        session_data["reference_id"] = ref_match.group(1).strip()
    elif not session_data.get("reference_id"):
        # fallback: plain alphanumeric word if no other match
        plain_ref = re.search(r"\b[A-Z0-9]{3,}\b", message, re.I)
        if plain_ref:
            session_data["reference_id"] = plain_ref.group(0)

    # ISSUE DESCRIPTION
    issue_match = re.search(r"(?:my issue is|i am facing issue|problem is)\s*(.*)", text)
    if issue_match and not session_data.get("issue_description"):
        session_data["issue_description"] = issue_match.group(1).strip()
    elif not session_data.get("issue_description"):
        # fallback: take leftover free text if none of the above
        cleaned = re.sub(r"(my name is|i am|this is|reference id|ref id|id|problem is|issue is)", "", message, flags=re.I)
        if not any(session_data.get(f) is None for f in ["name","phone","date_of_issue","reference_id"]) and len(cleaned.split()) > 2:
            session_data["issue_description"] = cleaned.strip()

    return session_data

def build_reply(session_data: dict):
    missing = [f for f in required_fields if not session_data.get(f)]
    if not missing:
        return (
            f"Following data has been collected:\n"
            f"Name: {session_data['name']}\n"
            f"Phone: {session_data['phone']}\n"
            f"Date of Issue: {session_data['date_of_issue']}\n"
            f"Reference ID: {session_data['reference_id']}\n"
            f"Issue: {session_data['issue_description']}\n\n"
            "Thank you!"
        )
    else:
        return f"Please provide the following missing fields: {', '.join(missing)}"

@app.get("/webhook")
async def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)
    return "Verification failed", 403

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    try:
        msg = (data.get("entry",[{}])[0]
                  .get("changes",[{}])[0]
                  .get("value",{})
                  .get("messages",[{}])[0])
        if msg.get("type") != "text":
            return {"status": "ignored"}

        user_message = msg["text"]["body"]
        from_number = msg["from"]

        # Reset if command
        if user_message.strip().lower() in ["reset", "restart"]:
            user_sessions[from_number] = {"data": {}, "last_active": time.time()}
            reply = "Thanks for confirmation. Let's start fresh. Please provide your details."
        else:
            session = user_sessions.get(from_number, {"data": {}, "last_active": time.time()})
            # timeout reset
            if time.time() - session["last_active"] > SESSION_TIMEOUT:
                session = {"data": {}, "last_active": time.time()}
            session["data"] = extract_fields(user_message, session["data"])
            session["last_active"] = time.time()
            user_sessions[from_number] = session
            reply = build_reply(session["data"])

        # send reply
        url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "to": from_number, "type": "text", "text": {"body": reply}}
        requests.post(url, headers=headers, json=payload, timeout=10)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}
