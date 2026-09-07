from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_FILE = "/srv/mail-archive/credentials/token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

creds = Credentials.from_authorized_user_file(
    TOKEN_FILE,
    SCOPES
)

service = build("gmail", "v1", credentials=creds)

# Obtener los últimos 5 mensajes
results = service.users().messages().list(
    userId="me",
    maxResults=5
).execute()

messages = results.get("messages", [])

print(f"Mensajes encontrados: {len(messages)}")
print("-" * 60)

for message in messages:
    msg = service.users().messages().get(
        userId="me",
        id=message["id"],
        format="metadata",
        metadataHeaders=["From", "To", "Subject", "Date"]
    ).execute()

    headers = msg["payload"].get("headers", [])

    data = {}

    for header in headers:
        data[header["name"]] = header["value"]

    print(f"ID:      {msg['id']}")
    print(f"Fecha:   {data.get('Date', 'N/A')}")
    print(f"De:      {data.get('From', 'N/A')}")
    print(f"Para:    {data.get('To', 'N/A')}")
    print(f"Asunto:  {data.get('Subject', 'N/A')}")
    print("-" * 60)
