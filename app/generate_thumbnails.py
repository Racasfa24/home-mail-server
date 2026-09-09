from pathlib import Path
import sqlite3

from PIL import Image, ImageOps, UnidentifiedImageError


DB_PATH = Path("/srv/mail-archive/data/index.db")
THUMBNAILS_DIR = Path("/srv/mail-archive/thumbnails")

THUMB_SIZE = (480, 480)
WEBP_QUALITY = 75


def get_thumbnail_path(sha256: str) -> Path:
    return (
        THUMBNAILS_DIR
        / sha256[:2]
        / f"{sha256}.webp"
    )


def create_thumbnail(source: Path, destination: Path):
    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)

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


def main():
    THUMBNAILS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    photos = conn.execute(
        """
        SELECT
            id,
            sha256,
            filename,
            filepath,
            mime_type
        FROM photos
        WHERE mime_type LIKE 'image/%'
        ORDER BY id
        """
    ).fetchall()

    conn.close()

    print(f"Imágenes encontradas: {len(photos)}")

    generated = 0
    existing = 0
    missing = 0
    errors = 0

    for index, photo in enumerate(photos, start=1):
        source = Path(photo["filepath"])
        destination = get_thumbnail_path(
            photo["sha256"]
        )

        if destination.exists():
            existing += 1
            continue

        if not source.exists():
            print(f"[FALTA] {source}")
            missing += 1
            continue

        try:
            create_thumbnail(
                source,
                destination
            )

            generated += 1

        except (
            UnidentifiedImageError,
            OSError,
            ValueError
        ) as exc:
            print(
                f"[ERROR] {photo['filename']}: {exc}"
            )

            errors += 1

        if index % 100 == 0:
            print(
                f"{index}/{len(photos)} procesadas "
                f"| nuevas={generated} "
                f"| existentes={existing} "
                f"| errores={errors}"
            )

    print()
    print("========== RESULTADO ==========")
    print(f"Generadas:  {generated}")
    print(f"Existentes: {existing}")
    print(f"Faltantes:  {missing}")
    print(f"Errores:    {errors}")
    print("===============================")


if __name__ == "__main__":
    main()