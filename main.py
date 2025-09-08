import os
import json
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = FastAPI()
logging.basicConfig(level=logging.INFO)

SESSIONS = {}

# Load Google credentials from ENV variable
def get_gsheet_service():
    try:
        creds_json = os.environ.get("GOOGLE_CREDS_JSON")
        if not creds_json:
            raise ValueError("GOOGLE_CREDS_JSON environment variable not set")

        creds_dict = json.loads(creds_json)

        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )

        service = build("sheets", "v4", credentials=creds)
        return service
    except Exception as e:
        logging.error(f"Failed to create Google Sheets service: {e}")
        raise

SPREADSHEET_ID = "1l3I0SOf2osFXA7iaBRd8d6qbS_S-cJW14__lspuEFts"
RANGE_NAME = "Sheet1!A:E"  # ✅ update if your sheet is not named "Sheet1"

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        from_number = data.get("from")
        message = data.get("message", "").strip()

        if not from_number:
            return JSONResponse(content={"response": "No sender ID provided"})

        if from_number not in SESSIONS:
            SESSIONS[from_number] = {"fields": {"Name": None, "Phone": None, "Date of Issue": None, "Reference Id": None, "Issue description": None}}

        session = SESSIONS[from_number]
        fields = session["fields"]

        # Reset logic
        if message.lower() in ["reset", "restart", "quit", "q"]:
            SESSIONS.pop(from_number, None)
            return JSONResponse(content={"response": "Session cleared. Start again by saying Hi."})

        # Fill missing fields
        for key in fields:
            if fields[key] is None:
                fields[key] = message
                break

        # Find next missing
        missing = [k for k, v in fields.items() if v is None]
        if missing:
            return JSONResponse(content={"response": f"Please provide: {', '.join(missing)}"})

        # All fields collected → Save to Google Sheet
        try:
            service = get_gsheet_service()
            sheet = service.spreadsheets()

            values = [[fields["Name"], fields["Phone"], fields["Date of Issue"], fields["Reference Id"], fields["Issue description"]]]
            body = {"values": values}

            result = sheet.values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=RANGE_NAME,
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()

            logging.info(f"Row appended to Google Sheet: {result}")

            # Clear session
            SESSIONS.pop(from_number, None)
            return JSONResponse(content={"response": "Thanks! Your details are saved."})

        except Exception as e:
            logging.error(f"Google Sheets append failed: {e}")
            return JSONResponse(content={"response": f"Error saving data: {e}"})

    except Exception as e:
        logging.error(f"Webhook failed: {e}")
        return JSONResponse(content={"response": f"Internal error: {e}"})
