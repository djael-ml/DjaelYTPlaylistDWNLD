import threading
from ytmusicapi import YTMusic
from core.utils import get_similarity_ratio, clean_text

_yt = None
_yt_lock = threading.Lock()


def get_yt_client():
    """Thread-safe lazy singleton for YTMusic."""
    global _yt
    if _yt is None:
        with _yt_lock:
            if _yt is None:
                _yt = YTMusic()
    return _yt


def parse_duration_to_seconds(duration_str):
    """Parses a duration string (e.g. '3:45', '1:02:15') into seconds."""
    if not duration_str:
        return 0
    try:
        parts = str(duration_str).split(":")
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + int(part)
        return seconds
    except ValueError:
        return 0


def _extract_yt_artists(item):
    """Extract primary artist and combined artist string from a YTMusic search result."""
    yt_artists = item.get("artists") or []
    names = []
    if isinstance(yt_artists, list):
        for a in yt_artists:
            if isinstance(a, dict) and a.get("name"):
                names.append(a["name"])
    elif isinstance(yt_artists, dict) and yt_artists.get("name"):
        names.append(yt_artists["name"])
    primary = names[0] if names else ""
    return primary, ", ".join(names)


def _safe_substring_match(a, b, min_len=4):
    """Substring match guarded against empty or very short strings."""
    if len(a) < min_len or len(b) < min_len:
        return False
    return (a in b) or (b in a)


def find_best_match(track, similarity_threshold=0.72, max_time_delta=5):
    """
    Searches YouTube Music for the track and evaluates ALL candidates using a
    weighted multi-factor score: title similarity + artist similarity + duration proximity.
    Returns: (videoId | None, match_status_message)
    """
    yt = get_yt_client()

    title = track.get("title") or ""
    artist = track.get("primary_artist") or ""
    all_artists = track.get("all_artists") or artist
    source_duration = float(track.get("duration_sec") or 0)

    query = f"{artist} {title}".strip()
    if not query:
        return None, "Empty search query"

    try:
        search_results = yt.search(query, filter="songs", limit=5)
    except Exception as e:
        return None, f"Search failed: {str(e)}"

    if not search_results:
        try:
            search_results = yt.search(query, limit=5)
        except Exception:
            return None, "No results found on YouTube Music"

    if not search_results:
        return None, "No results found on YouTube"

    clean_src_title = clean_text(title)
    clean_src_artist = clean_text(artist)
    clean_src_all_artists = clean_text(all_artists)

    best_verified = None  # (score, video_id, title_sim, artist_sim, time_delta)
    best_fallback = None  # (score, video_id, yt_title, title_sim, artist_sim, time_delta)

    for item in search_results:
        video_id = item.get("videoId")
        if not video_id:
            continue

        yt_title = item.get("title") or ""
        yt_duration = item.get("duration_seconds")
        if yt_duration is None:
            yt_duration = parse_duration_to_seconds(item.get("duration") or "")
        yt_duration = float(yt_duration or 0)

        yt_primary_artist, yt_all_artists = _extract_yt_artists(item)

        clean_yt_title = clean_text(yt_title)
        clean_yt_artist = clean_text(yt_primary_artist)
        clean_yt_all_artists = clean_text(yt_all_artists)

        # 1. Title similarity
        title_sim = get_similarity_ratio(clean_src_title, clean_yt_title)
        title_substr = _safe_substring_match(clean_src_title, clean_yt_title)
        effective_title_sim = max(title_sim, 0.85 if title_substr else 0.0)

        # 2. Artist similarity
        if clean_src_artist and clean_yt_artist:
            artist_sim = max(
                get_similarity_ratio(clean_src_artist, clean_yt_artist),
                get_similarity_ratio(clean_src_all_artists, clean_yt_all_artists),
            )
            artist_substr = (
                _safe_substring_match(clean_src_artist, clean_yt_all_artists, min_len=3)
                or _safe_substring_match(clean_yt_artist, clean_src_all_artists, min_len=3)
            )
            effective_artist_sim = max(artist_sim, 0.85 if artist_substr else 0.0)
        else:
            effective_artist_sim = 0.5  # Neutral when artist metadata is unavailable

        # 3. Duration proximity
        has_duration = source_duration > 0 and yt_duration > 0
        if has_duration:
            time_delta = abs(source_duration - yt_duration)
            if time_delta <= max_time_delta:
                duration_score = 1.0 - (time_delta / (max_time_delta * 2.0))
            elif time_delta <= 15:
                duration_score = 0.5
            else:
                duration_score = 0.0
            has_good_duration = time_delta <= max_time_delta
        else:
            time_delta = 0.0
            # Guard against downloading 1-hour mixes/podcasts when source duration is unknown
            duration_score = 0.7 if (yt_duration == 0 or yt_duration <= 900) else 0.0
            has_good_duration = (yt_duration == 0 or yt_duration <= 900)

        # Weighted composite score
        composite_score = (
            0.55 * effective_title_sim
            + 0.30 * effective_artist_sim
            + 0.15 * duration_score
        )

        # Verified match requires strong title, reasonable artist, and valid duration
        has_good_title = effective_title_sim >= similarity_threshold
        has_good_artist = (effective_artist_sim >= 0.55) or (not clean_src_artist)

        if has_good_title and has_good_artist and has_good_duration:
            if best_verified is None or composite_score > best_verified[0]:
                best_verified = (composite_score, video_id, effective_title_sim, effective_artist_sim, time_delta)

        if best_fallback is None or composite_score > best_fallback[0]:
            best_fallback = (composite_score, video_id, yt_title, effective_title_sim, effective_artist_sim, time_delta)

    if best_verified:
        score, video_id, t_sim, a_sim, delta = best_verified
        return (
            video_id,
            f"Verified match (score={score:.2f}, title={t_sim:.2f}, artist={a_sim:.2f}, delta={delta:.1f}s)",
        )

    if best_fallback and best_fallback[0] >= 0.50:
        score, video_id, yt_title, t_sim, a_sim, delta = best_fallback
        return (
            video_id,
            f"Fallback match: '{yt_title}' (score={score:.2f}, title={t_sim:.2f}, artist={a_sim:.2f}, delta={delta:.1f}s)",
        )

    return None, "No sufficiently accurate match found on YouTube Music"
