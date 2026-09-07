import base64
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


TOKEN_FILE = "/srv/mail-archive/credentials/token.json"
MAIL_DIR = "/srv/mail-archive/mail"

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

MESSAGE_ID = "1a0112ba88421833"


def main():
    # Cargar credenciales OAuth
    creds = Credentials.from_authorized_user_file(
        TOKEN_FILE,
        SCOPES
    )

    # Conectarnos a Gmail
    service = build(
        "gmail",
        "v1",
        credentials=creds
    )

    print(f"Descargando mensaje: {MESSAGE_ID}")

    # Obtener el correo completo
    message = service.users().messages().get(
        userId="me",
        id=MESSAGE_ID,
        format="raw"
    ).execute()

    # Gmail devuelve el mensaje codificado en base64url
    raw_message = message["raw"]

    email_bytes = base64.urlsafe_b64decode(
        raw_message + "=" * (-len(raw_message) % 4)
    )

    # Crear directorio de destino
    os.makedirs(MAIL_DIR, exist_ok=True)

    output_file = os.path.join(
        MAIL_DIR,
        f"{MESSAGE_ID}.eml"
    )

    # Guardar el correo original
    with open(output_file, "wb") as file:
        file.write(email_bytes)

    print("Correo descargado correctamente.")
    print(f"Archivo: {output_file}")
    print(f"Tamaño: {len(email_bytes):,} bytes")


if __name__ == "__main__":
    main()
