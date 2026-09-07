import base64
import logging
import os
import re
import sqlite3
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
import time
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# ============================================================
# CONFIGURACIÓN
# ============================================================

BASE_DIR = Path("/")

TOKEN_FILE = Path("/credentials/token.json")

MAIL_DIR = Path("/mail")
ATTACHMENTS_DIR = Path("/attachments")
LOG_DIR = Path("/logs")
DB_FILE = Path("/data/index.db")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly"
]

# MODO DE PRUEBA
MAX_MESSAGES = None


# ============================================================
# LOGGING
# ============================================================

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / "archive.log",
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


# ============================================================
# BASE DE DATOS
# ============================================================

def initialize_database():
    connection = sqlite3.connect(DB_FILE)

    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            gmail_id TEXT PRIMARY KEY,
            thread_id TEXT,
            message_date TEXT,
            sender TEXT,
            recipients TEXT,
            subject TEXT,
            eml_path TEXT NOT NULL,
            size_bytes INTEGER,
            archived_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            mime_type TEXT,
            size_bytes INTEGER,
            FOREIGN KEY (gmail_id)
                REFERENCES messages(gmail_id)
        )
    """)

    connection.commit()
    connection.close()


# ============================================================
# UTILIDADES
# ============================================================

def sanitize_filename(filename):
    """
    Limpia nombres de archivos para que sean seguros
    dentro del sistema de archivos.
    """

    filename = filename.strip()

    filename = re.sub(
        r'[<>:"/\\|?*\x00-\x1F]',
        "_",
        filename
    )

    filename = filename.rstrip(". ")

    if not filename:
        filename = "attachment"

    return filename


def unique_path(directory, filename):
    """
    Evita sobrescribir archivos con el mismo nombre.
    """

    path = directory / filename

    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix

    counter = 1

    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


def get_header(message, name):
    """
    Obtiene un header del mensaje.
    """

    for header in message.get_all(name, []):
        return header

    return ""


def parse_message_date(date_value):
    """
    Convierte la fecha del correo en año y mes.
    Si no puede interpretarla, utiliza la fecha actual.
    """

    try:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(date_value)

        return (
            parsed.year,
            parsed.month
        )

    except Exception:
        now = datetime.now()

        return (
            now.year,
            now.month
        )


# ============================================================
# GMAIL
# ============================================================

def create_gmail_service():

    creds = Credentials.from_authorized_user_file(
        TOKEN_FILE,
        SCOPES
    )

    return build(
        "gmail",
        "v1",
        credentials=creds
    )

def get_message_ids(service, max_messages=None):

    if max_messages is None:
        logger.info(
            "Buscando todos los mensajes disponibles..."
        )
    else:
        logger.info(
            "Buscando hasta %s mensajes...",
            max_messages
        )

    messages = []
    page_token = None

    while True:

        if max_messages is None:
            request_max_results = 100
        else:
            remaining = max_messages - len(messages)

            if remaining <= 0:
                break

            request_max_results = min(remaining, 100)

        response = service.users().messages().list(
            userId="me",
            maxResults=request_max_results,
            pageToken=page_token
        ).execute()

        page_messages = response.get(
            "messages",
            []
        )

        messages.extend(page_messages)

        page_token = response.get(
            "nextPageToken"
        )

        if not page_token:
            break

    if max_messages is None:
        return messages

    return messages[:max_messages]

# ============================================================
# GUARDAR MENSAJE
# ============================================================

def save_message(
    service,
    message_id,
    connection
):

    cursor = connection.cursor()

    # --------------------------------------------------------
    # Comprobar si ya existe
    # --------------------------------------------------------

    cursor.execute(
        """
        SELECT gmail_id
        FROM messages
        WHERE gmail_id = ?
        """,
        (message_id,)
    )

    if cursor.fetchone():

        logger.info(
            "Ya archivado, se omite: %s",
            message_id
        )

        return False


    # --------------------------------------------------------
    # Descargar mensaje completo
    # --------------------------------------------------------

    logger.info(
        "Descargando mensaje: %s",
        message_id
    )

    max_retries = 6

    for attempt in range(max_retries):

        try:

            message = service.users().messages().get(
                userId="me",
                id=message_id,
                format="raw"
            ).execute()

            break

        except HttpError as error:

            if error.resp.status == 403:

                logger.warning(
                    "Límite de Gmail alcanzado para %s. "
                    "Intento %s/%s.",
                    message_id,
                    attempt + 1,
                    max_retries
                )

                if attempt == max_retries - 1:
                    raise

                wait_seconds = 5 * (2 ** attempt)

                logger.info(
                    "Esperando %s segundos antes de reintentar...",
                    wait_seconds
                )

                time.sleep(wait_seconds)

            else:
                raise

    # --------------------------------------------------------
    # Decodificar RAW
    # --------------------------------------------------------

    raw_message = message["raw"]

    email_bytes = base64.urlsafe_b64decode(
        raw_message + "=" * (
            -len(raw_message) % 4
        )
    )


    # --------------------------------------------------------
    # Parsear correo
    # --------------------------------------------------------

    email_message = BytesParser(
        policy=policy.default
    ).parsebytes(email_bytes)


    # --------------------------------------------------------
    # Metadatos
    # --------------------------------------------------------

    date_value = get_header(
        email_message,
        "Date"
    )

    sender = get_header(
        email_message,
        "From"
    )

    recipients = get_header(
        email_message,
        "To"
    )

    subject = get_header(
        email_message,
        "Subject"
    )


    year, month = parse_message_date(
        date_value
    )


    # --------------------------------------------------------
    # Directorio del correo
    # --------------------------------------------------------

    mail_directory = (
        MAIL_DIR
        / str(year)
        / f"{month:02d}"
    )

    mail_directory.mkdir(
        parents=True,
        exist_ok=True
    )


    eml_path = (
        mail_directory
        / f"{message_id}.eml"
    )


    # --------------------------------------------------------
    # Guardar EML
    # --------------------------------------------------------

    with open(
        eml_path,
        "wb"
    ) as file:

        file.write(email_bytes)


    # --------------------------------------------------------
    # Registrar mensaje
    # --------------------------------------------------------

    archived_at = datetime.now().isoformat(
        timespec="seconds"
    )

    cursor.execute(
        """
        INSERT INTO messages (
            gmail_id,
            thread_id,
            message_date,
            sender,
            recipients,
            subject,
            eml_path,
            size_bytes,
            archived_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            message.get("threadId"),
            date_value,
            sender,
            recipients,
            subject,
            str(eml_path),
            len(email_bytes),
            archived_at
        )
    )


    # --------------------------------------------------------
    # Extraer adjuntos
    # --------------------------------------------------------

    attachment_count = 0

    attachment_directory = (
        ATTACHMENTS_DIR
        / str(year)
        / f"{month:02d}"
        / message_id
    )

    for part in email_message.walk():

        filename = part.get_filename()

        if not filename:
            continue

        payload = part.get_payload(
            decode=True
        )

        if payload is None:
            continue

        filename = sanitize_filename(
            filename
        )

        attachment_directory.mkdir(
            parents=True,
            exist_ok=True
        )

        attachment_path = unique_path(
            attachment_directory,
            filename
        )

        with open(
            attachment_path,
            "wb"
        ) as file:

            file.write(payload)


        cursor.execute(
            """
            INSERT INTO attachments (
                gmail_id,
                filename,
                filepath,
                mime_type,
                size_bytes
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                message_id,
                attachment_path.name,
                str(attachment_path),
                part.get_content_type(),
                len(payload)
            )
        )

        attachment_count += 1

        logger.info(
            "  Adjunto: %s (%s bytes)",
            attachment_path.name,
            len(payload)
        )


    connection.commit()


    logger.info(
        "Archivado: %s | %s | %s | Adjuntos: %s",
        message_id,
        date_value,
        subject or "(sin asunto)",
        attachment_count
    )

    return True


# ============================================================
# PROGRAMA PRINCIPAL
# ============================================================

def main():

    logger.info("=" * 70)

    logger.info(
        "INICIANDO ARCHIVADOR DE GMAIL"
    )

    logger.info(
    "Límite de mensajes: %s",
    "TODOS" if MAX_MESSAGES is None else MAX_MESSAGES
    )

    logger.info("=" * 70)


    # Crear directorios
    MAIL_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    ATTACHMENTS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


    # Inicializar DB
    initialize_database()


    # Conectar Gmail
    service = create_gmail_service()

    logger.info(
        "Conexión con Gmail establecida."
    )


    # Obtener mensajes
    messages = get_message_ids(
        service,
        MAX_MESSAGES
    )

    logger.info(
        "Mensajes encontrados: %s",
        len(messages)
    )


    connection = sqlite3.connect(
        DB_FILE
    )


    processed = 0
    skipped = 0
    errors = 0


    for index, message in enumerate(
        messages,
        start=1
    ):

        message_id = message["id"]

        logger.info(
            "[%s/%s] Procesando %s",
            index,
            len(messages),
            message_id
        )

        try:

            result = save_message(
                service,
                message_id,
                connection
            )

            if result:
                processed += 1
            else:
                skipped += 1

        except Exception as error:

            errors += 1

            logger.exception(
                "Error procesando %s: %s",
                message_id,
                error
            )


    connection.close()


    logger.info("=" * 70)

    logger.info(
        "PROCESO TERMINADO"
    )

    logger.info(
        "Nuevos archivados: %s",
        processed
    )

    logger.info(
        "Ya existentes: %s",
        skipped
    )

    logger.info(
        "Errores: %s",
        errors
    )

    logger.info("=" * 70)


if __name__ == "__main__":
    main()
