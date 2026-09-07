from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CREDENTIALS_FILE = "/srv/mail-archive/credentials/client_secret_185654323324-hjd9atmg2m5364s884emcnktvgk91r31.apps.googleusercontent.com.json"

flow = InstalledAppFlow.from_client_secrets_file(
    CREDENTIALS_FILE,
    SCOPES
)

creds = flow.run_local_server(
    port=0,
    access_type="offline",
    prompt="consent"
)

with open("/srv/mail-archive/credentials/token.json", "w") as token:
    token.write(creds.to_json())

print("Autenticación completada correctamente.")

