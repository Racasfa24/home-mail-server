from flask import Flask, render_template, request, abort, send_file
from email import policy
from email.parser import BytesParser
from pathlib import Path
import sqlite3
from email.utils import parsedate_to_datetime

app = Flask(__name__)

BASE_DIR = Path("/srv/mail-archive")
DB_PATH = Path("/data/index.db")


def get_db():

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    return connection

def resolve_path(path):
    """
    Convierte rutas almacenadas en la BD del host
    a las rutas correspondientes dentro del container.
    """

    path = Path(path)

    old_mail = Path("/srv/mail-archive/mail")
    old_attachments = Path("/srv/mail-archive/attachments")

    if path.is_relative_to(old_mail):
        return Path("/mail") / path.relative_to(old_mail)

    if path.is_relative_to(old_attachments):
        return Path("/attachments") / path.relative_to(old_attachments)

    return path

def get_message(gmail_id):

    connection = get_db()

    message = connection.execute(
        """
        SELECT *
        FROM messages
        WHERE gmail_id = ?
        """,
        (gmail_id,)
    ).fetchone()

    connection.close()

    return message


def get_attachments(gmail_id):

    connection = get_db()

    attachments = connection.execute(
        """
        SELECT *
        FROM attachments
        WHERE gmail_id = ?
        ORDER BY filename
        """,
        (gmail_id,)
    ).fetchall()

    connection.close()

    return attachments


@app.route("/")
def index():

    search = request.args.get(
        "search",
        ""
    ).strip()

    sort = request.args.get(
        "sort",
        "newest"
    )

    date_from = request.args.get(
        "date_from",
        ""
    )

    date_to = request.args.get(
        "date_to",
        ""
    )

    page = request.args.get(
        "page",
        1,
        type=int
    )

    if page < 1:
        page = 1

    per_page = 25

    connection = get_db()

    # --------------------------------
    # Obtener correos
    # --------------------------------

    if search:

        search_value = f"%{search}%"

        messages = connection.execute(
            """
            SELECT *
            FROM messages
            WHERE sender LIKE ?
               OR recipients LIKE ?
               OR subject LIKE ?
            """,
            (
                search_value,
                search_value,
                search_value
            )
        ).fetchall()

    else:

        messages = connection.execute(
            """
            SELECT *
            FROM messages
            """
        ).fetchall()

    connection.close()

    # --------------------------------
    # Convertir fechas
    # --------------------------------

    processed_messages = []

    for message in messages:

        try:

            parsed_date = parsedate_to_datetime(
                message["message_date"]
            )

            message_date = parsed_date.date()

        except Exception:

            message_date = None

        processed_messages.append(
            (
                message,
                message_date
            )
        )

    # --------------------------------
    # Filtro desde
    # --------------------------------

    if date_from:

        try:

            from_date = parsedate_to_datetime(
                date_from
            ).date()

        except Exception:

            from datetime import date

            from_date = date.fromisoformat(
                date_from
            )

        processed_messages = [
            item
            for item in processed_messages
            if item[1] is not None
            and item[1] >= from_date
        ]

    # --------------------------------
    # Filtro hasta
    # --------------------------------

    if date_to:

        try:

            from datetime import date

            to_date = date.fromisoformat(
                date_to
            )

        except Exception:

            to_date = None

        if to_date:

            processed_messages = [
                item
                for item in processed_messages
                if item[1] is not None
                and item[1] <= to_date
            ]

    # --------------------------------
    # Ordenamiento
    # --------------------------------

    if sort == "oldest":

        processed_messages.sort(
            key=lambda item: item[1] or __import__("datetime").date.min
        )

    else:

        sort = "newest"

        processed_messages.sort(
            key=lambda item: item[1] or __import__("datetime").date.min,
            reverse=True
        )

    # --------------------------------
    # Total después de filtros
    # --------------------------------

    total = len(processed_messages)

    total_pages = max(
        1,
        (total + per_page - 1) // per_page
    )

    if page > total_pages:

        page = total_pages

    # --------------------------------
    # Paginación
    # --------------------------------

    offset = (page - 1) * per_page

    page_messages = processed_messages[
        offset:offset + per_page
    ]

    messages = [
        item[0]
        for item in page_messages
    ]

    return render_template(
        "index.html",
        messages=messages,
        search=search,
        sort=sort,
        date_from=date_from,
        date_to=date_to,
        page=page,
        total_pages=total_pages,
        total=total
    )


@app.route("/email/<gmail_id>")
def email(gmail_id):

    message = get_message(gmail_id)

    if message is None:
        abort(404)

    eml_path = resolve_path(
        message["eml_path"]
    )

    if not eml_path.exists():
        abort(404)

    with open(
        eml_path,
        "rb"
    ) as file:

        mail = BytesParser(
            policy=policy.default
        ).parse(file)

    body_text = ""
    body_html = ""

    if mail.is_multipart():

        for part in mail.walk():

            content_type = part.get_content_type()

            if content_type == "text/plain":

                try:

                    body_text = part.get_content()

                except Exception:

                    pass

            elif content_type == "text/html":

                try:

                    body_html = part.get_content()

                except Exception:

                    pass

    else:

        content_type = mail.get_content_type()

        try:

            if content_type == "text/html":

                body_html = mail.get_content()

            else:

                body_text = mail.get_content()

        except Exception:

            pass

    attachments = get_attachments(
        gmail_id
    )

    return render_template(
        "email.html",
        message=message,
        body_text=body_text,
        body_html=body_html,
        attachments=attachments
    )


@app.route("/download/<gmail_id>")
def download_eml(gmail_id):

    message = get_message(
        gmail_id
    )

    if message is None:
        abort(404)

    eml_path = resolve_path(
        message["eml_path"]
    )

    if not eml_path.exists():
        abort(404)

    return send_file(
        eml_path,
        as_attachment=True,
        download_name=f"{gmail_id}.eml"
    )


@app.route("/attachment/<int:attachment_id>")
def attachment(attachment_id):

    connection = get_db()

    attachment_file = connection.execute(
        """
        SELECT *
        FROM attachments
        WHERE id = ?
        """,
        (attachment_id,)
    ).fetchone()

    connection.close()

    if attachment_file is None:
        abort(404)

    filepath = resolve_path(
        attachment_file["filepath"]
    )

    if not filepath.exists():
        abort(404)

    return send_file(
        filepath,
        download_name=attachment_file["filename"]
    )


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8080,
        debug=True
    )