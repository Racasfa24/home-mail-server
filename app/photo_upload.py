from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import mimetypes
import os
import re
import sqlite3
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename


LOCAL_TZ = ZoneInfo("America/Mazatlan")

DB_PATH = Path("/data/index.db")
INBOX_DIR = Path("/photos-inbox")
PHOTOS_DIR = Path("/photos")
THUMBNAILS_DIR = Path("/thumbnails")

THUMB_SIZE = (480, 480)
WEBP_QUALITY = 75


ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".heic",
    ".heif",
    ".mp4",
    ".mov",
    ".m4v",
    ".3gp",
    ".avi",
    ".mkv",
}


DATE_PATTERNS = [
    re.compile(
        r"(?<!\d)"
        r"(?P<year>20\d{2})"
        r"(?P<month>\d{2})"
        r"(?P<day>\d{2})"
        r"[_-]"
        r"(?P<hour>\d{2})"
        r"(?P<minute>\d{2})"
        r"(?P<second>\d{2})"
    ),

    re.compile(
        r"(?<!\d)"
        r"(?P<year>20\d{2})"
        r"[-_]"
        r"(?P<month>\d{2})"
        r"[-_]"
        r"(?P<day>\d{2})"
        r"[_ -]"
        r"(?P<hour>\d{2})"
        r"[-_:]"
        r"(?P<minute>\d{2})"
        r"[-_:]"
        r"(?P<second>\d{2})"
    ),

    re.compile(
        r"(?<!\d)"
        r"(?P<year>20\d{2})"
        r"(?P<month>\d{2})"
        r"(?P<day>\d{2})"
        r"(?!\d)"
    ),
]


def calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def parse_exif_datetime(path: Path):
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)

            exif = image.getexif()

            if not exif:
                return None

            for tag in (36867, 36868, 306):
                value = exif.get(tag)

                if not value:
                    continue

                try:
                    return datetime.strptime(
                        str(value),
                        "%Y:%m:%d %H:%M:%S"
                    )
                except ValueError:
                    continue

    except (
        UnidentifiedImageError,
        OSError,
        ValueError
    ):
        pass

    return None


def parse_facebook_timestamp(filename: str):
    match = re.search(
        r"FB_IMG_(\d{13})",
        filename,
        re.IGNORECASE
    )

    if not match:
        return None

    try:
        timestamp_ms = int(match.group(1))

        utc_dt = datetime.fromtimestamp(
            timestamp_ms / 1000,
            tz=timezone.utc
        )

        return utc_dt.astimezone(
            LOCAL_TZ
        ).replace(
            tzinfo=None
        )

    except (
        ValueError,
        OverflowError,
        OSError
    ):
        return None


def parse_filename_datetime(filename: str):
    for pattern in DATE_PATTERNS:
        match = pattern.search(filename)

        if not match:
            continue

        values = match.groupdict()

        try:
            return datetime(
                int(values["year"]),
                int(values["month"]),
                int(values["day"]),
                int(values.get("hour") or 0),
                int(values.get("minute") or 0),
                int(values.get("second") or 0),
            )

        except (
            ValueError,
            TypeError
        ):
            continue

    return None


def determine_taken_date(
    path: Path,
    filename: str,
    mime_type: str
):
    if mime_type.startswith("image/"):
        exif_date = parse_exif_datetime(path)

        if exif_date:
            return exif_date, "EXIF"

    facebook_date = parse_facebook_timestamp(
        filename
    )

    if facebook_date:
        return facebook_date, "FACEBOOK"

    filename_date = parse_filename_datetime(
        filename
    )

    if filename_date:
        return filename_date, "FILENAME"

    return (
        datetime.now(LOCAL_TZ).replace(
            tzinfo=None
        ),
        "UPLOAD_TIME"
    )


def to_archive_dates(local_naive: datetime):
    local_dt = local_naive.replace(
        tzinfo=LOCAL_TZ
    )

    utc_dt = local_dt.astimezone(
        timezone.utc
    )

    return (
        utc_dt.isoformat(),
        local_dt.isoformat()
    )


def create_thumbnail(
    source: Path,
    sha256: str
):
    destination = (
        THUMBNAILS_DIR
        / sha256[:2]
        / f"{sha256}.webp"
    )

    if destination.exists():
        return

    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(
                image
            )

            image.thumbnail(
                THUMB_SIZE,
                Image.Resampling.LANCZOS
            )

            if image.mode in ("RGBA", "LA"):
                image = image.convert("RGBA")
            else:
                image = image.convert("RGB")

            image.save(
                destination,
                format="WEBP",
                quality=WEBP_QUALITY,
                method=6
            )

    except (
        UnidentifiedImageError,
        OSError,
        ValueError
    ):
        pass


def build_destination(
    filename: str,
    sha256: str,
    date: datetime
):
    directory = (
        PHOTOS_DIR
        / f"{date.year:04d}"
        / f"{date.month:02d}"
    )

    directory.mkdir(
        parents=True,
        exist_ok=True
    )

    destination = directory / filename

    if not destination.exists():
        return destination

    source = Path(filename)

    return directory / (
        f"{source.stem}-{sha256[:8]}"
        f"{source.suffix.lower()}"
    )


def import_uploaded_file(file_storage):
    original_filename = secure_filename(
        file_storage.filename or ""
    )

    if not original_filename:
        return {
            "status": "error",
            "message": "Archivo sin nombre."
        }

    extension = Path(
        original_filename
    ).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        return {
            "status": "error",
            "message": (
                f"Formato no permitido: "
                f"{original_filename}"
            )
        }

    INBOX_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_path = (
        INBOX_DIR
        / f"{uuid.uuid4().hex}{extension}"
    )

    file_storage.save(temp_path)

    try:
        sha256 = calculate_sha256(
            temp_path
        )

        conn = sqlite3.connect(
            DB_PATH
        )

        conn.row_factory = sqlite3.Row

        existing = conn.execute(
            """
            SELECT
                id,
                filename
            FROM photos
            WHERE sha256 = ?
            """,
            (sha256,)
        ).fetchone()

        if existing:
            conn.close()
            temp_path.unlink(
                missing_ok=True
            )

            return {
                "status": "duplicate",
                "message": (
                    f"{original_filename} "
                    f"ya estaba archivado."
                ),
                "photo_id": existing["id"]
            }

        mime_type = (
            file_storage.mimetype
            or mimetypes.guess_type(
                original_filename
            )[0]
            or "application/octet-stream"
        )

        taken_date, date_source = (
            determine_taken_date(
                temp_path,
                original_filename,
                mime_type
            )
        )

        taken_at, taken_at_local = (
            to_archive_dates(
                taken_date
            )
        )

        destination = build_destination(
            original_filename,
            sha256,
            taken_date
        )

        os.replace(
            temp_path,
            destination
        )

        file_size = destination.stat().st_size

        imported_at = datetime.now(
            timezone.utc
        ).isoformat()

        cursor = conn.execute(
            """
            INSERT INTO photos (
                sha256,
                filename,
                filepath,
                source_path,
                mime_type,
                taken_at,
                taken_at_local,
                creation_time,
                file_size,
                description,
                favorite,
                google_photos_url,
                latitude,
                longitude,
                altitude,
                people_json,
                metadata_found,
                imported_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                sha256,
                destination.name,
                str(destination),
                "WEB_UPLOAD",
                mime_type,
                taken_at,
                taken_at_local,
                None,
                file_size,
                None,
                0,
                None,
                None,
                None,
                None,
                None,
                0,
                imported_at
            )
        )

        photo_id = cursor.lastrowid

        conn.commit()
        conn.close()

        if mime_type.startswith(
            "image/"
        ):
            create_thumbnail(
                destination,
                sha256
            )

        return {
            "status": "imported",
            "message": (
                f"{original_filename} "
                f"importado correctamente."
            ),
            "photo_id": photo_id,
            "date_source": date_source,
            "taken_at_local": (
                taken_at_local
            )
        }

    except Exception:
        temp_path.unlink(
            missing_ok=True
        )
        raise