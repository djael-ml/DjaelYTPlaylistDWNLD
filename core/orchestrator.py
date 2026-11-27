import os
import threading
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.playlist_extractor import detect_platform, get_playlist_tracks_general
from core.yt_matcher import find_best_match
from core.downloader import download_track, sanitize_filename, DownloadCancelled
from core.tagger import apply_metadata
from core.utils import (
    scan_local_mp3s,
    clean_text,
    get_similarity_ratio,
    setup_logging,
    ensure_ffmpeg,
    ensure_js_runtime,
)

BITRATE_TOLERANCE_KBPS = 8


@dataclass
class DownloadSummary:
    total: int = 0
    downloaded: int = 0
    upgraded: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    failed_tracks: list = field(default_factory=list)  # list of (track_dict, error_reason)

    @property
    def completed(self) -> int:
        return self.downloaded + self.upgraded + self.skipped + self.failed

    @property
    def success(self) -> bool:
        return not self.cancelled


def _safe_substring(a, b, min_len=3):
    if len(a) < min_len or len(b) < min_len:
        return False
    return (a in b) or (b in a)


def _find_matching_local(track, local_tracks):
    clean_sp_title = clean_text(track.get("title") or "")
    clean_sp_artist = clean_text(track.get("primary_artist") or "")
    clean_sp_artists = clean_text(track.get("all_artists") or "")

    for lt in local_tracks:
        clean_lt_title = clean_text(lt.get("title") or "")
        clean_lt_artist = clean_text(lt.get("artist") or "")

        title_match = get_similarity_ratio(clean_sp_title, clean_lt_title) >= 0.85
        if not title_match:
            continue

        artist_match = (
            get_similarity_ratio(clean_sp_artist, clean_lt_artist) >= 0.85
            or _safe_substring(clean_sp_artist, clean_lt_artist)
            or _safe_substring(clean_lt_artist, clean_sp_artists)
        )
        if artist_match:
            return lt
    return None


def orchestrate_download(
    playlist_url,
    output_dir,
    bitrate,
    client_id=None,
    client_secret=None,
    max_workers=3,
    overwrite_mode="upgrade",  # "upgrade" | "replace" | "skip"
    tracks_override=None,      # Optional list of tracks (e.g. Retry Failed)
    log_cb=None,
    progress_cb=None,
    stats_cb=None,
    stop_check_cb=None,
):
    """
    Orchestrates playlist extraction, duplicate detection, YouTube Music matching,
    parallel audio downloading, atomic replacement, and ID3v2.3 metadata tagging.

    Returns a DownloadSummary instance.
    """
    logger = setup_logging()
    log_lock = threading.Lock()
    state_lock = threading.Lock()

    def log(message):
        logger.info(message)
        with log_lock:
            if log_cb:
                log_cb(message)
            else:
                try:
                    print(message)
                except UnicodeEncodeError:
                    print(message.encode("ascii", "replace").decode("ascii"))

    os.makedirs(output_dir, exist_ok=True)
    ensure_ffmpeg(log_cb=log, progress_cb=progress_cb)
    ensure_js_runtime(log_cb=log)
    target_bitrate = int(bitrate)
    workers = max(1, min(8, int(max_workers or 1)))
    mode = overwrite_mode if overwrite_mode in ("upgrade", "replace", "skip") else "upgrade"

    if tracks_override is not None:
        tracks = list(tracks_override)
        platform = "Retry"
        log(f"[RETRY] Retrying {len(tracks)} failed track(s)...")
    else:
        platform = detect_platform(playlist_url)
        log(f"[PLATFORM] Detected platform: {platform}")
        log(f"[{platform.upper()}] Retrieving playlist tracks...")
        tracks = get_playlist_tracks_general(
            playlist_url, client_id=client_id, client_secret=client_secret, log_cb=log
        )
        log(f"[{platform.upper()}] {len(tracks)} track(s) found.")

    total_tracks = len(tracks)
    if total_tracks == 0:
        raise ValueError("No valid tracks found in this playlist.")

    log(f"[CONFIG] Quality: {target_bitrate} kbps | Workers: {workers} | Duplicates: {mode}")
    log("[LOCAL] Scanning output directory for existing MP3s...")
    local_tracks = scan_local_mp3s(output_dir)
    log(f"[LOCAL] {len(local_tracks)} existing MP3 file(s) identified.")

    summary = DownloadSummary(total=total_tracks)
    track_progress = {}  # index -> float [0.0, 1.0]

    def emit_progress(status_text):
        with state_lock:
            overall = sum(track_progress.values()) / total_tracks if total_tracks > 0 else 0.0
            overall = min(1.0, max(0.0, overall))
            snapshot = DownloadSummary(
                total=summary.total,
                downloaded=summary.downloaded,
                upgraded=summary.upgraded,
                skipped=summary.skipped,
                failed=summary.failed,
                cancelled=summary.cancelled,
                failed_tracks=list(summary.failed_tracks),
            )
        if progress_cb:
            progress_cb(overall, status_text)
        if stats_cb:
            stats_cb(snapshot)

    def process_single_track(index, track):
        if stop_check_cb and stop_check_cb():
            return

        track_label = f"{track['primary_artist']} - {track['title']}"
        prefix = f"[{index + 1}/{total_tracks}]"
        log(f"\n{prefix} {track_label}")

        with state_lock:
            track_progress[index] = 0.05
        emit_progress(f"{prefix} Vérification : {track['title']}")

        # Check existing local match (thread-safe)
        with state_lock:
            matching_local = _find_matching_local(track, local_tracks)

        is_upgrade = False
        old_filepath = None

        if matching_local:
            local_bitrate = matching_local.get("bitrate", 0)
            old_filepath = matching_local.get("filepath")

            if mode == "skip":
                log(f"{prefix} [SKIP] Existing track found ({local_bitrate} kbps). Skipped per user setting.")
                with state_lock:
                    summary.skipped += 1
                    track_progress[index] = 1.0
                emit_progress(f"{prefix} Ignoré : {track['title']}")
                return

            if mode == "upgrade":
                if local_bitrate >= (target_bitrate - BITRATE_TOLERANCE_KBPS):
                    log(
                        f"{prefix} [SKIP] Existing track has equal/better quality "
                        f"({local_bitrate} kbps >= {target_bitrate} kbps)."
                    )
                    with state_lock:
                        summary.skipped += 1
                        track_progress[index] = 1.0
                    emit_progress(f"{prefix} Ignoré : {track['title']}")
                    return
                else:
                    log(
                        f"{prefix} [UPGRADE] Higher quality requested "
                        f"({target_bitrate} kbps > {local_bitrate} kbps). Downloading replacement safely..."
                    )
                    is_upgrade = True
            elif mode == "replace":
                log(f"{prefix} [REPLACE] Overwriting existing file safely after download...")
                is_upgrade = True

        # Resolve YouTube video_id (direct bypass if already extracted from YouTube/YT Music!)
        video_id = track.get("video_id")
        if video_id:
            log(f"{prefix} [MATCH] Direct YouTube ID used: {video_id}")
        else:
            log(f"{prefix} [SEARCH] Searching YouTube Music...")
            video_id, match_msg = find_best_match(track)
            log(f"{prefix} [SEARCH] {match_msg}")

        if not video_id:
            log(f"{prefix} [WARNING] No reliable match found. Skipping.")
            with state_lock:
                summary.failed += 1
                summary.failed_tracks.append((track, "No match on YouTube Music"))
                track_progress[index] = 1.0
            emit_progress(f"{prefix} Introuvable : {track['title']}")
            return

        if stop_check_cb and stop_check_cb():
            return

        def dl_progress(t_idx, t_title, state, percent, speed=""):
            # Map download phase to [0.1, 0.9] of this track's progress
            with state_lock:
                track_progress[t_idx] = 0.1 + (0.8 * percent)
            speed_part = f" · {speed}" if speed else ""
            emit_progress(f"{prefix} {t_title} ({state} {int(percent * 100)}%{speed_part})")

        log(f"{prefix} [DOWNLOAD] Downloading audio via yt-dlp...")
        try:
            temp_mp3 = download_track(
                video_id=video_id,
                output_dir=output_dir,
                track_index=index,
                track_title=track["title"],
                preferred_quality=target_bitrate,
                progress_callback=dl_progress,
                stop_check_cb=stop_check_cb,
                log_cb=log,
            )
        except DownloadCancelled:
            log(f"{prefix} [STOP] Track download cancelled.")
            return
        except Exception as dl_err:
            log(f"{prefix} [ERROR] Download failed: {dl_err}")
            with state_lock:
                summary.failed += 1
                summary.failed_tracks.append((track, str(dl_err)))
                track_progress[index] = 1.0
            emit_progress(f"{prefix} Échec : {track['title']}")
            return

        log(f"{prefix} [METADATA] Writing ID3v2.3 tags & cover art...")
        try:
            apply_metadata(temp_mp3, track, log_cb=log)
        except Exception as tag_err:
            log(f"{prefix} [WARNING] Failed to write ID3 tags: {tag_err}")

        final_filename = f"{track['all_artists']} - {track['title']}"
        sanitized_filename = sanitize_filename(final_filename) + ".mp3"
        final_path = os.path.join(output_dir, sanitized_filename)

        try:
            # Atomic replacement — old file is NEVER deleted before the new MP3 is ready!
            os.replace(temp_mp3, final_path)

            # If an upgraded/replaced track had a different filename, remove the old one now
            if old_filepath and os.path.abspath(old_filepath) != os.path.abspath(final_path):
                if os.path.exists(old_filepath):
                    try:
                        os.remove(old_filepath)
                    except OSError as rm_err:
                        log(f"{prefix} [WARNING] Could not remove old file: {rm_err}")

            with state_lock:
                if matching_local and matching_local in local_tracks:
                    local_tracks.remove(matching_local)
                local_tracks.append({
                    "filepath": final_path,
                    "title": track["title"],
                    "artist": track["all_artists"],
                    "bitrate": target_bitrate,
                })
                if is_upgrade:
                    summary.upgraded += 1
                else:
                    summary.downloaded += 1
                track_progress[index] = 1.0

            tag_action = "UPGRADED" if is_upgrade else "SAVED"
            log(f"{prefix} [{tag_action}] '{sanitized_filename}'")
            emit_progress(f"{prefix} Terminé : {track['title']}")
        except Exception as e:
            log(f"{prefix} [ERROR] Failed to finalize file: {e}")
            try:
                if os.path.exists(temp_mp3):
                    os.remove(temp_mp3)
            except OSError:
                pass
            with state_lock:
                summary.failed += 1
                summary.failed_tracks.append((track, f"Save error: {e}"))
                track_progress[index] = 1.0
            emit_progress(f"{prefix} Erreur : {track['title']}")

    emit_progress(f"Démarrage ({total_tracks} pistes · {workers} worker(s))...")

    if workers == 1:
        for idx, track in enumerate(tracks):
            if stop_check_cb and stop_check_cb():
                summary.cancelled = True
                break
            process_single_track(idx, track)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for idx, track in enumerate(tracks):
                if stop_check_cb and stop_check_cb():
                    summary.cancelled = True
                    break
                futures[executor.submit(process_single_track, idx, track)] = idx

            for future in as_completed(futures):
                if stop_check_cb and stop_check_cb():
                    summary.cancelled = True
                try:
                    future.result()
                except Exception as exc:
                    log(f"[ERROR] Worker error: {exc}")

    if stop_check_cb and stop_check_cb():
        summary.cancelled = True

    log(
        f"\n================ RÉSUMÉ ================\n"
        f"Total: {summary.total} | Téléchargés: {summary.downloaded} | "
        f"Remplacés/Améliorés: {summary.upgraded} | Ignorés: {summary.skipped} | Échecs: {summary.failed}"
    )
    if summary.failed_tracks:
        log("Pistes en échec :")
        for trk, reason in summary.failed_tracks:
            log(f"  - {trk.get('primary_artist')} - {trk.get('title')} ({reason})")

    emit_progress("Annulé." if summary.cancelled else "Terminé !")
    return summary
