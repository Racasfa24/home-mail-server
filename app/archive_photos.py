from pathlib import Path
from zipfile import ZipFile, ZipInfo
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import shutil
import sqlite3


ZIP_PATH = Path(
    "/srv/mail-archive/photos-import/"
    "takeout-20260907T164038Z-1-001.zip"
)

DEST_DIR = Path("/srv/mail-archive/photos")
DB_PATH = Path("/srv/mail-archive/data/index.db")

LOCAL_TZ = ZoneInfo("America/Mazatlan")

MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".mp4",
    ".mov",
    ".avi",
    ".webp",
    ".heic",
    ".gif",
}


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            sha256 TEXT NOT NULL UNIQUE,

            filename TEXT NOT NULL,
            filepath TEXT NOT NULL UNIQUE,

            source_path TEXT,
            mime_type TEXT,

            taken_at TEXT,
            taken_at_local TEXT,
            creation_time TEXT,

            file_size INTEGER,

            description TEXT,
            favorite INTEGER NOT NULL DEFAULT 0,

            google_photos_url TEXT,

            latitude REAL,
            longitude REAL,
            altitude REAL,

            people_json TEXT,

            metadata_found INTEGER NOT NULL DEFAULT 0,

            imported_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_photos_taken_at
        ON photos(taken_at)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_photos_filename
        ON photos(filename)
    """)

    conn.commit()


def sha256_stream(fileobj):
    digest = hashlib.sha256()

    while True:
        chunk = fileobj.read(1024 * 1024)

        if not chunk:
            break

        digest.update(chunk)

    return digest.hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()

    with open(path, "rb") as file:
        while True:
            chunk = file.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def get_metadata(zip_file, media_path):
    sidecar = media_path + ".supplemental-metadata.json"

    try:
        with zip_file.open(sidecar) as file:
            return json.load(file)
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def timestamp_to_datetime(timestamp):
    try:
        return datetime.fromtimestamp(
            int(timestamp),
            tz=timezone.utc
        )
    except (ValueError, TypeError, OverflowError):
        return None


def get_dates(metadata, zip_info: ZipInfo):
    taken_at = None
    creation_time = None

    if metadata:
        photo_taken = metadata.get("photoTakenTime", {})
        creation = metadata.get("creationTime", {})

        taken_at = timestamp_to_datetime(
            photo_taken.get("timestamp")
        )

        creation_time = timestamp_to_datetime(
            creation.get("timestamp")
        )

    if taken_at is None:
        taken_at = creation_time

    if taken_at is None:
        try:
            taken_at = datetime(
                *zip_info.date_time,
                tzinfo=timezone.utc
            )
        except Exception:
            taken_at = datetime.now(timezone.utc)

    taken_at_local = taken_at.astimezone(LOCAL_TZ)

    return taken_at, taken_at_local, creation_time


def get_geo(metadata):
    if not metadata:
        return None, None, None

    geo = metadata.get("geoData", {})

    latitude = geo.get("latitude")
    longitude = geo.get("longitude")
    altitude = geo.get("altitude")

    # Google Takeout suele usar 0,0 cuando no existe ubicación.
    if latitude == 0.0 and longitude == 0.0:
        return None, None, None

    return latitude, longitude, altitude


def build_destination(taken_at_local, filename, digest):
    year = f"{taken_at_local.year:04d}"
    month = f"{taken_at_local.month:02d}"

    target_dir = DEST_DIR / year / month
    target_dir.mkdir(parents=True, exist_ok=True)

    destination = target_dir / filename

    if not destination.exists():
        return destination

    # El archivo ya existe físicamente.
    try:
        existing_hash = sha256_file(destination)

        if existing_hash == digest:
            return destination

    except OSError:
        pass

    # Mismo nombre, contenido distinto.
    stem = destination.stem
    suffix = destination.suffix

    short_hash = digest[:12]

    return target_dir / f"{stem}_{short_hash}{suffix}"


def already_registered(conn, digest):
    row = conn.execute(
        """
        SELECT id, filepath
        FROM photos
        WHERE sha256 = ?
        """,
        (digest,)
    ).fetchone()

    return row


def detect_mime_type(path):
    extension = path.suffix.lower()

    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
    }

    return mime_types.get(extension, "application/octet-stream")


def insert_photo(
    conn,
    digest,
    filename,
    destination,
    source_path,
    metadata,
    taken_at,
    taken_at_local,
    creation_time,
    file_size
):
    description = None
    favorite = 0
    google_photos_url = None
    people = []

    if metadata:
        description = metadata.get("description")
        favorite = 1 if metadata.get("favorited") else 0
        google_photos_url = metadata.get("url")
        people = metadata.get("people", [])

    latitude, longitude, altitude = get_geo(metadata)

    conn.execute(
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            digest,
            filename,
            str(destination),
            source_path,
            detect_mime_type(Path(filename)),
            taken_at.isoformat(),
            taken_at_local.isoformat(),
            creation_time.isoformat()
            if creation_time else None,
            file_size,
            description,
            favorite,
            google_photos_url,
            latitude,
            longitude,
            altitude,
            json.dumps(
                people,
                ensure_ascii=False
            ),
            1 if metadata else 0,
            datetime.now(timezone.utc).isoformat()
        )
    )

    conn.commit()


def main():
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    if not ZIP_PATH.exists():
        print(f"❌ No existe el ZIP:")
        print(ZIP_PATH)
        return

    conn = sqlite3.connect(DB_PATH)

    try:
        init_db(conn)

        with ZipFile(ZIP_PATH) as zip_file:

            media_files = [
                info
                for info in zip_file.infolist()
                if not info.is_dir()
                and Path(info.filename).suffix.lower()
                in MEDIA_EXTENSIONS
            ]

            total = len(media_files)

            imported = 0
            duplicates = 0
            errors = 0
            without_metadata = 0

            print("=" * 70)
            print("IMPORTADOR GOOGLE PHOTOS TAKEOUT")
            print("=" * 70)
            print(f"Multimedia encontrada: {total}")
            print()

            for index, info in enumerate(
                media_files,
                start=1
            ):
                source_path = info.filename
                filename = Path(source_path).name

                try:
                    metadata = get_metadata(
                        zip_file,
                        source_path
                    )

                    if metadata is None:
                        without_metadata += 1

                    taken_at, taken_at_local, creation_time = (
                        get_dates(metadata, info)
                    )

                    # Calculamos primero el hash directamente desde ZIP.
                    with zip_file.open(info) as source:
                        digest = sha256_stream(source)

                    existing = already_registered(
                        conn,
                        digest
                    )

                    if existing:
                        duplicates += 1

                        print(
                            f"[{index}/{total}] "
                            f"⏭ Duplicado: {filename}"
                        )

                        continue

                    destination = build_destination(
                        taken_at_local,
                        filename,
                        digest
                    )

                    # Puede ser una de las 10 fotos de nuestra prueba.
                    if destination.exists():
                        existing_hash = sha256_file(
                            destination
                        )

                        if existing_hash != digest:
                            raise RuntimeError(
                                "El destino existe pero "
                                "su hash no coincide."
                            )

                        print(
                            f"[{index}/{total}] "
                            f"🗃 Ya estaba en disco: "
                            f"{filename}"
                        )

                    else:
                        with (
                            zip_file.open(info) as source,
                            open(destination, "wb") as target
                        ):
                            shutil.copyfileobj(
                                source,
                                target
                            )

                        print(
                            f"[{index}/{total}] "
                            f"✅ {filename}"
                        )

                    insert_photo(
                        conn=conn,
                        digest=digest,
                        filename=filename,
                        destination=destination,
                        source_path=source_path,
                        metadata=metadata,
                        taken_at=taken_at,
                        taken_at_local=taken_at_local,
                        creation_time=creation_time,
                        file_size=info.file_size
                    )

                    imported += 1

                except Exception as error:
                    errors += 1

                    print(
                        f"[{index}/{total}] "
                        f"❌ {filename}: {error}"
                    )

            print()
            print("=" * 70)
            print("RESUMEN")
            print("=" * 70)
            print(f"Multimedia total:       {total}")
            print(f"Registrados/importados: {imported}")
            print(f"Duplicados omitidos:    {duplicates}")
            print(f"Sin metadata directa:   {without_metadata}")
            print(f"Errores:                 {errors}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()