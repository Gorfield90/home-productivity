"""Photos: checked and stored in the database. The page shrinks them on the phone first (photo.js), so a
photo is a few hundred KB and the nightly backup carries it along with everything else."""

from sqlalchemy.orm import Session

from .models import Photo, User
from .shopping import FormError

MAX_BYTES = 4 * 1024 * 1024  # a shrunk photo is far smaller; this only stops a huge original sent without the script
UPLOAD_LIMIT = MAX_BYTES + 1  # what the form parser lets through, so a too-big file gets our message, not a crash

# What a stored photo can be, recognised by its first bytes rather than what the browser claims.
_SIGNATURES = [(b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png")]


def sniff(data: bytes) -> str | None:
    for sig, ctype in _SIGNATURES:
        if data.startswith(sig):
            return ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def read_upload(upload) -> bytes:
    """The bytes of a form file field, or b"" when nothing was chosen."""
    if upload is None or isinstance(upload, str):
        return b""
    return await upload.read()


def check(data: bytes) -> str:
    """The photo's content type, or a FormError a person can act on."""
    if not data:
        raise FormError("Choose a photo first.")
    if len(data) > MAX_BYTES:
        raise FormError("That photo is too big. Try again, or take a smaller one.")
    ctype = sniff(data)
    if not ctype:
        raise FormError("That file isn't a photo the app can show (JPEG, PNG or WebP).")
    return ctype


def make(user: User, data: bytes, thumb: bytes = b"", **owner) -> Photo:
    """A new photo for its owner (plant_id=... or recipe_id=...). A thumbnail that isn't a valid image is dropped; the full photo is
    shown in its place."""
    ctype = check(data)
    if thumb and (len(thumb) > MAX_BYTES or not sniff(thumb)):
        thumb = b""
    return Photo(content_type=ctype, data=data, thumb=thumb or None, added_by=user, **owner)


def load(session: Session, photo_id: int) -> Photo | None:
    return session.get(Photo, photo_id)


def owner_url(photo: Photo) -> str:
    """The page a photo is shown on."""
    if photo.plant_id:
        return f"/plants/{photo.plant_id}"
    if photo.recipe_id:
        return f"/recipes/{photo.recipe_id}"
    return "/"
