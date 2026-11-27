import io
import requests
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TRCK, TDRC, APIC, error

_http_session = requests.Session()


def _prepare_cover_bytes(raw_bytes, content_type=""):
    """
    Detect actual image MIME type and convert WebP/unusual formats to JPEG via Pillow
    for maximum compatibility across Windows Explorer, VLC, iTunes, and DJ decks.
    """
    ct = (content_type or "").lower()
    is_jpeg = raw_bytes[:3] == b"\xff\xd8\xff" or "jpeg" in ct or "jpg" in ct
    is_png = raw_bytes[:8] == b"\x89PNG\r\n\x1a\n" or "png" in ct

    if is_jpeg:
        return raw_bytes, "image/jpeg"
    if is_png:
        return raw_bytes, "image/png"

    # Convert WebP or other formats to JPEG using Pillow
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw_bytes)) as img:
            rgb = img.convert("RGB")
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=92)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        return raw_bytes, "image/jpeg"


def apply_metadata(file_path, track, log_cb=None):
    """
    Applies rich ID3v2.3 metadata (Title, Artist, Album Artist, Album, Track Number,
    Year, Cover Art) to the MP3 file in a single atomic save pass.
    """
    title = track.get("title") or ""
    artists = track.get("all_artists") or track.get("primary_artist") or ""
    album_artist = track.get("album_artist") or track.get("primary_artist") or artists
    album = track.get("album") or ""
    track_number = track.get("track_number")
    year = str(track.get("year") or "")
    cover_url = track.get("cover_url")

    try:
        audio = MP3(file_path, ID3=ID3)
    except error:
        audio = MP3(file_path)

    if audio.tags is None:
        audio.add_tags()
    else:
        audio.tags.clear()

    # encoding=1 (UTF-16) is universally supported in ID3v2.3 (Windows Explorer / iTunes / cars)
    if title:
        audio.tags.add(TIT2(encoding=1, text=title))
    if artists:
        audio.tags.add(TPE1(encoding=1, text=artists))
    if album_artist:
        audio.tags.add(TPE2(encoding=1, text=album_artist))
    if album:
        audio.tags.add(TALB(encoding=1, text=album))
    if track_number:
        audio.tags.add(TRCK(encoding=1, text=str(track_number)))
    if year:
        audio.tags.add(TDRC(encoding=1, text=year))

    if cover_url:
        try:
            response = _http_session.get(cover_url, timeout=10)
            if response.status_code == 200 and response.content:
                img_bytes, mime = _prepare_cover_bytes(
                    response.content, response.headers.get("Content-Type", "")
                )
                audio.tags.add(
                    APIC(
                        encoding=0,
                        mime=mime,
                        type=3,  # Front cover
                        desc="Cover",
                        data=img_bytes,
                    )
                )
            elif log_cb:
                log_cb(f"[WARNING] Cover art HTTP {response.status_code} for {title}")
        except Exception as e:
            if log_cb:
                log_cb(f"[WARNING] Could not embed cover art: {e}")

    # Save once in ID3v2.3 format for maximum Windows & hardware player compatibility
    audio.save(v2_version=3)
