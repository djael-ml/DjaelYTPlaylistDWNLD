import os
import re
import glob
import uuid
import yt_dlp
from core.utils import find_ffmpeg

WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


class DownloadCancelled(Exception):
    """Raised inside yt-dlp progress hook when user requests immediate cancellation."""


def sanitize_filename(name, max_length=160):
    """
    Sanitizes a filename for Windows/POSIX filesystems while preserving valid Unicode
    characters, escaping Windows reserved names, removing control characters, and
    enforcing a safe maximum length to prevent MAX_PATH issues.
    """
    if not name:
        return "Unknown Track"

    # Remove Windows-illegal characters and ASCII control characters (0x00 - 0x1F)
    sanitized = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", str(name))
    # Collapse repeated whitespace
    sanitized = re.sub(r"\s+", " ", sanitized).strip().strip(".")

    if not sanitized:
        sanitized = "Unknown Track"

    # Truncate cleanly to stay well below Windows MAX_PATH
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip(" .")

    # Guard against Windows reserved device filenames (e.g. CON, NUL, AUX)
    stem = sanitized.split(".")[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        sanitized = f"_{sanitized}"

    return sanitized or "Unknown Track"


def _cleanup_temp_files(output_dir, temp_prefix):
    pattern = os.path.join(output_dir, f"{temp_prefix}.*")
    for path in glob.glob(pattern):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def download_track(
    video_id,
    output_dir,
    track_index,
    track_title,
    preferred_quality,
    progress_callback,
    stop_check_cb=None,
):
    """
    Downloads a YouTube audio stream and transcodes it to MP3.
    Uses a unique temporary filename in output_dir, supports immediate cancellation,
    and guarantees cleanup of partial files on error or cancellation.
    Returns: Path to the temporary MP3 file.
    """
    ffmpeg_exe, ffmpeg_dir = find_ffmpeg()
    if not ffmpeg_exe:
        raise FileNotFoundError(
            "FFmpeg was not found (checked local folder and system PATH). Please run download_ffmpeg.py first."
        )

    unique_token = uuid.uuid4().hex[:8]
    temp_filename = f"djaelyt_temp_{video_id}_{unique_token}"
    temp_outtmpl = os.path.join(output_dir, f"{temp_filename}.%(ext)s")

    def ydl_progress_hook(d):
        if stop_check_cb and stop_check_cb():
            raise DownloadCancelled("Download cancelled by user.")

        status = d.get("status")
        if status == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total > 0:
                percent = min(1.0, max(0.0, downloaded / total))
            else:
                percent_str = d.get("_percent_str", "0.0%").strip()
                clean_percent = re.sub(r"\x1b\[[0-9;]*m", "", percent_str).replace("%", "")
                try:
                    percent = min(1.0, max(0.0, float(clean_percent) / 100.0))
                except ValueError:
                    percent = 0.0

            speed = d.get("speed")
            if isinstance(speed, (int, float)) and speed > 0:
                if speed >= 1024 * 1024:
                    clean_speed = f"{speed / (1024 * 1024):.1f} MB/s"
                else:
                    clean_speed = f"{speed / 1024:.0f} KB/s"
            else:
                speed_str = d.get("_speed_str", "").strip()
                clean_speed = re.sub(r"\x1b\[[0-9;]*m", "", speed_str)

            if progress_callback:
                progress_callback(track_index, track_title, "downloading", percent, clean_speed)

        elif status == "finished":
            if progress_callback:
                progress_callback(track_index, track_title, "converting", 1.0, "")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": temp_outtmpl,
        "ffmpeg_location": ffmpeg_dir,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 20,
        "progress_hooks": [ydl_progress_hook],
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": str(preferred_quality),
        }],
    }

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except DownloadCancelled:
        _cleanup_temp_files(output_dir, temp_filename)
        raise
    except Exception as e:
        _cleanup_temp_files(output_dir, temp_filename)
        raise Exception(f"yt-dlp download failed: {str(e)}")

    final_temp_mp3 = os.path.join(output_dir, f"{temp_filename}.mp3")
    if not os.path.exists(final_temp_mp3):
        _cleanup_temp_files(output_dir, temp_filename)
        raise FileNotFoundError(
            f"Expected output file not found: {final_temp_mp3}. Conversion may have failed."
        )

    return final_temp_mp3
