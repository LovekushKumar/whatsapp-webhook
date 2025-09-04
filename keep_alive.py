import requests

URL = "https://whatsapp-webhook-mqab.onrender.com/webhook"  # replace with your Render URL

try:
    r = requests.get(URL, timeout=10)
    print(f"Pinged {URL}, status: {r.status_code}")
except Exception as e:
    print(f"Error pinging {URL}: {e}")
