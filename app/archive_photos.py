from pathlib import Path
from zipfile import ZipFile
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil

ZIP_PATH = Path(
    "/srv/mail-archive/photos-import/"
    "takeout-20260907T164038Z-1-001.zip"
)

DEST_DIR = Path("/srv/mail-archive/photos")

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png",
    ".mp4", ".mov", ".avi",
    ".webp", ".heic", ".gif"
}

TEST_LIMIT = 10


def sha256_stream(fileobj):
    h = hashlib.sha256()

    while True:
        chunk = fileobj.read(1024 * 1024)

        if not chunk:
            break

        h.update(chunk)

    return h.hexdigest()


def get_metadata(z, media_path):
    sidecar = media_path + ".supplemental-metadata.json"

    try:
        with z.open(sidecar) as f:
            return json.load(f)
    except KeyError:
        return None


def get_taken_datetime(metadata, zip_info):
    if metadata:
        photo_taken = metadata.get("photoTakenTime", {})

        timestamp = photo_taken.get("timestamp")

        if timestamp:
            try:
                return datetime.fromtimestamp(
                    int(timestamp),
                    tz=timezone.utc
                )
            except (ValueError, TypeError):
                pass

        creation = metadata.get("creationTime", {})
        timestamp = creation.get("timestamp")

        if timestamp:
            try:
                return datetime.fromtimestamp(
                    int(timestamp),
                    tz=timezone.utc
                )
            except (ValueError, TypeError):
                pass

    # fallback: fecha registrada dentro del ZIP
    try:
        return datetime(
            *zip_info.date_time,
            tzinfo=timezone.utc
        )
    except Exception:
        return datetime.now(timezone.utc)


def safe_destination(base_dir, date, filename):
    year = f"{date.year:04d}"
    month = f"{date.month:02d}"

    target_dir = base_dir / year / month
    target_dir.mkdir(parents=True, exist_ok=True)

    target = target_dir / filename

    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix

    counter = 1

    while True:
        candidate = target_dir / f"{stem}_{counter}{suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


def main():
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    with ZipFile(ZIP_PATH) as z:
        media_files = [
            info
            for info in z.infolist()
            if not info.is_dir()
            and Path(info.filename).suffix.lower() in MEDIA_EXTENSIONS
        ]

        print(f"Multimedia detectada: {len(media_files)}")
        print(f"Modo prueba: primeros {TEST_LIMIT}")
        print()

        for index, info in enumerate(media_files[:TEST_LIMIT], start=1):
            media_path = info.filename
            filename = Path(media_path).name

            metadata = get_metadata(z, media_path)
            taken_at = get_taken_datetime(metadata, info)

            destination = safe_destination(
                DEST_DIR,
                taken_at,
                filename
            )

            print("=" * 70)
            print(f"[{index}/{TEST_LIMIT}]")
            print("Origen:     ", media_path)
            print("Destino:    ", destination)
            print("Fecha:      ", taken_at.isoformat())
            print("Metadata:   ", "sí" if metadata else "no")

            with z.open(info) as source:
                digest = sha256_stream(source)

            print("SHA256:     ", digest)

            with z.open(info) as source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target)

            print("Copiado:     sí")

    print()
    print("Prueba terminada correctamente.")


if __name__ == "__main__":
    main()