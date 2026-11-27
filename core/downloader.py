import os
import re
import glob
import time
import uuid
import importlib.util
import yt_dlp
from core.utils import find_ffmpeg, find_js_runtimes

WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

# Errors that indicate the video itself cannot be downloaded on any client
_PERMANENT_ERROR_MARKERS = (
    "private video",
    "this video has been removed",
    "video has been removed",
    "account associated with this video has been terminated",
    "copyright claim",
    "members-only content",
)


def _has_bundled_ejs():
    try:
        return importlib.util.find_spec("yt_dlp_ejs") is not None
    except Exception:
        return False


def _is_permanent_youtube_error(err_msg):
    low = (err_msg or "").lower()
    return any(marker in low for marker in _PERMANENT_ERROR_MARKERS)


def _build_client_strategies(has_js_runtime):
    """
    Ordered list of (label, player_client_list_or_None, bypass_cache) strategies.
    Starts with yt-dlp's best default clients (Opus 251), then rotates across
    alternative YouTube clients if googlevideo.com returns HTTP 403 / SABR errors.
    """
    strategies = [("default", None, False)]
    if has_js_runtime:
        strategies.append(("tv_samsung", ["tv_samsung"], True))
    strategies.append(("visionos", ["visionos"], True))
    strategies.append(("android", ["android"], True))
    if has_js_runtime:
        strategies.append(("tv_simply+web_music", ["tv_simply", "web_music"], True))
    strategies.append(("mweb+web_embedded", ["mweb", "web_embedded"], True))
    return strategies


class _SilentYdlLogger:
    """Captures yt-dlp errors/warnings cleanly without spamming stdout/stderr."""

    def __init__(self):
        self.warnings = []
        self.errors = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self.warnings.append(str(msg))

    def error(self, msg):
        self.errors.append(str(msg))


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
    log_cb=None,
):
    """
    Downloads a YouTube audio stream and transcodes it to MP3.
    Uses a unique temporary filename in output_dir, configures detected JS runtimes
    for YouTube's EJS challenge solver, automatically rotates YouTube player clients
    if YouTube returns HTTP 403 / SABR format errors, supports immediate cancellation,
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

    js_runtimes = find_js_runtimes()
    has_js = bool(js_runtimes)
    has_ejs = _has_bundled_ejs()
    strategies = _build_client_strategies(has_js)

    url = f"https://www.youtube.com/watch?v={video_id}"
    final_temp_mp3 = os.path.join(output_dir, f"{temp_filename}.mp3")
    last_error = None

    for attempt_idx, (strategy_label, player_clients, bypass_cache) in enumerate(strategies):
        if stop_check_cb and stop_check_cb():
            _cleanup_temp_files(output_dir, temp_filename)
            raise DownloadCancelled("Download cancelled by user.")

        if attempt_idx > 0:
            _cleanup_temp_files(output_dir, temp_filename)
            if log_cb:
                log_cb(
                    f"[{track_index + 1}] [FALLBACK] Retrying with YouTube client '{strategy_label}'..."
                )
            time.sleep(0.5)

        ydl_logger = _SilentYdlLogger()
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": temp_outtmpl,
            "ffmpeg_location": ffmpeg_dir,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": ydl_logger,
            "retries": 2,
            "fragment_retries": 2,
            "socket_timeout": 20,
            "progress_hooks": [ydl_progress_hook],
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(preferred_quality),
            }],
        }

        if js_runtimes:
            ydl_opts["js_runtimes"] = js_runtimes
            if not has_ejs:
                ydl_opts["remote_components"] = ["ejs:github"]

        if bypass_cache:
            ydl_opts["cachedir"] = False

        if player_clients:
            ydl_opts["extractor_args"] = {"youtube": {"player_client": player_clients}}

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            if os.path.exists(final_temp_mp3):
                return final_temp_mp3
            last_error = FileNotFoundError(
                f"Expected output file not found: {final_temp_mp3}. Conversion may have failed."
            )
        except DownloadCancelled:
            _cleanup_temp_files(output_dir, temp_filename)
            raise
        except Exception as e:
            _cleanup_temp_files(output_dir, temp_filename)
            last_error = e
            if _is_permanent_youtube_error(str(e)):
                break

    _cleanup_temp_files(output_dir, temp_filename)
    if isinstance(last_error, FileNotFoundError):
        raise last_error
    raise Exception(f"yt-dlp download failed: {str(last_error)}")
