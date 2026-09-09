from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import argparse
import re
import sqlite3

from PIL import Image, ImageOps, UnidentifiedImageError


DB_PATH = Path("/srv/mail-archive/data/index.db")
LOCAL_TZ = ZoneInfo("America/Mazatlan")


# Ejemplos soportados:
#
# 20250309_210202.MP4
# Screenshot_20250115_161239_Facebook(1).jpg
# IMG_20230112_120632294_BURST000_COVER_TOP(1).jpg
# 20190905_190540-editada.jpg
#
DATE_PATTERNS = [
    # YYYYMMDD_HHMMSS
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

    # YYYY-MM-DD_HH-MM-SS, por si aparece alguno
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

    # Solo fecha YYYYMMDD
    re.compile(
        r"(?<!\d)"
        r"(?P<year>20\d{2})"
        r"(?P<month>\d{2})"
        r"(?P<day>\d{2})"
        r"(?!\d)"
    ),
]


def resolve_photo_path(filepath: str) -> Path:
    """
    Los paths en SQLite fueron guardados con rutas del host.
    Este script también se ejecuta en el host, así que normalmente
    podemos usar la ruta directamente.
    """
    return Path(filepath)


def parse_exif_datetime(path: Path):
    """
    Intenta obtener la fecha original desde EXIF.
    Retorna datetime local sin timezone o None.
    """

    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)

            exif = image.getexif()

            if not exif:
                return None

            # 36867 = DateTimeOriginal
            # 36868 = DateTimeDigitized
            # 306   = DateTime
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


def parse_filename_datetime(filename: str):
    """
    Busca una fecha razonable dentro del nombre.
    """

    for pattern in DATE_PATTERNS:
        match = pattern.search(filename)

        if not match:
            continue

        values = match.groupdict()

        try:
            year = int(values["year"])
            month = int(values["month"])
            day = int(values["day"])

            hour = int(values.get("hour") or 0)
            minute = int(values.get("minute") or 0)
            second = int(values.get("second") or 0)

            dt = datetime(
                year,
                month,
                day,
                hour,
                minute,
                second
            )

            # Evita fechas absurdas
            if dt.year < 2000:
                continue

            return dt

        except (
            ValueError,
            TypeError
        ):
            continue

    return None


def to_archive_dates(local_naive: datetime):
    """
    Consideramos la hora inferida como hora local del archivo
    y guardamos tanto versión local como UTC.
    """

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


def determine_date(photo):
    """
    Prioridad:

    1. EXIF
    2. Nombre del archivo
    """

    filepath = resolve_photo_path(
        photo["filepath"]
    )

    mime_type = (
        photo["mime_type"] or ""
    ).lower()

    # Primero EXIF solo para imágenes
    if mime_type.startswith("image/") and filepath.exists():

        exif_date = parse_exif_datetime(
            filepath
        )

        if exif_date:
            return exif_date, "EXIF"

    # Después nombre
    filename_date = parse_filename_datetime(
        photo["filename"]
    )

    if filename_date:
        return filename_date, "FILENAME"

    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="Repara fechas incorrectas de fotos importadas."
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica realmente los UPDATE a SQLite."
    )

    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    photos = conn.execute(
        """
        SELECT
            id,
            filename,
            filepath,
            mime_type,
            taken_at,
            taken_at_local,
            metadata_found
        FROM photos
        WHERE metadata_found = 0
        ORDER BY id
        """
    ).fetchall()

    print()
    print(f"Registros a revisar: {len(photos)}")
    print(
        "Modo:",
        "APLICAR CAMBIOS"
        if args.apply
        else "SOLO PRUEBA"
    )
    print()

    repaired = 0
    exif_count = 0
    filename_count = 0
    unresolved = 0

    for photo in photos:

        detected, source = determine_date(
            photo
        )

        if detected is None:
            unresolved += 1

            print(
                f"[SIN FECHA] "
                f"{photo['id']} | "
                f"{photo['filename']}"
            )

            continue

        taken_at, taken_at_local = (
            to_archive_dates(detected)
        )

        print(
            f"[{source}] "
            f"{photo['id']} | "
            f"{photo['filename']}\n"
            f"    ANTES: {photo['taken_at_local']}\n"
            f"    AHORA: {taken_at_local}"
        )

        if args.apply:
            conn.execute(
                """
                UPDATE photos
                SET
                    taken_at = ?,
                    taken_at_local = ?
                WHERE id = ?
                """,
                (
                    taken_at,
                    taken_at_local,
                    photo["id"]
                )
            )

        repaired += 1

        if source == "EXIF":
            exif_count += 1

        elif source == "FILENAME":
            filename_count += 1

    if args.apply:
        conn.commit()

    conn.close()

    print()
    print("========== RESULTADO ==========")
    print(f"Reparables:       {repaired}")
    print(f"  Desde EXIF:     {exif_count}")
    print(f"  Desde filename: {filename_count}")
    print(f"Sin resolver:     {unresolved}")

    if not args.apply:
        print()
        print(
            "No se modificó SQLite."
        )
        print(
            "Si los resultados se ven bien, "
            "ejecuta nuevamente con --apply."
        )

    print("===============================")


if __name__ == "__main__":
    main()
    