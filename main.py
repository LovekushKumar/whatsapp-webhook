from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import os, requests

app = FastAPI()

# VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
# WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
# WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

VERIFY_TOKEN = "LifeChatBot"
WHATSAPP_ACCESS_TOKEN = "EAAJ8gohressBPAFtWQqrwfTtHXXzhaIg36Tju5jjQ2DrI72C2NOXtqZAM5BGaB31h04ZCfaxZBNofVj2D3xXUVIjCZAZBwCQsfiLeIwPUZCj9U2tFsXtY4p28jhMDgYMgggCHkMOEKVPlCFK3bLtMLsgdiiMk6sWjvTaByFkn2saYIZBodLZCcuu7omZBcNfqvPno7VyJD2VnDAJOAhrZBmz8qKh0uffnEdv5jDmE8hb483rRz7ZAQZD"
WHATSAPP_PHONE_NUMBER_ID = "700118286525971"

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
        msg = (data.get("entry",[{}])[0]
                  .get("changes",[{}])[0]
                  .get("value",{})
                  .get("messages",[{}])[0])

        if msg.get("type") != "text":
            return {"status": "ignored_non_text"}

        user_message = msg["text"]["body"]
        from_number = msg["from"]

        reply = f"Hi {user_message}"
        url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": from_number,
            "type": "text",
            "text": {"body": reply}
        }
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}
