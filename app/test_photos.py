from google.oauth2.credentials import Credentials
import requests

TOKEN_FILE = "/srv/mail-archive/credentials/token.json"

creds = Credentials.from_authorized_user_file(TOKEN_FILE)

if not creds.valid:
    print("❌ Las credenciales no son válidas.")
    exit(1)

headers = {
    "Authorization": f"Bearer {creds.token}"
}

url = "https://photoslibrary.googleapis.com/v1/mediaItems"

params = {
    "pageSize": 10
}

response = requests.get(
    url,
    headers=headers,
    params=params,
    timeout=30
)

print("HTTP:", response.status_code)

if response.status_code != 200:
    print(response.text)
    exit(1)

data = response.json()

media_items = data.get("mediaItems", [])

print(f"\n📸 Elementos encontrados: {len(media_items)}\n")

for item in media_items:
    metadata = item.get("mediaMetadata", {})

    print("=" * 60)
    print("ID:       ", item.get("id"))
    print("Archivo:  ", item.get("filename"))
    print("MIME:     ", item.get("mimeType"))
    print("Creación: ", metadata.get("creationTime"))
    print("Dimensiones:", metadata.get("width"), "x", metadata.get("height"))
    print("Base URL: ", item.get("baseUrl"))
