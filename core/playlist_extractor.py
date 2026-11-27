import re
import json
import base64
from urllib.parse import urlparse
import requests
import yt_dlp

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}

SHORT_LINK_DOMAINS = (
    "spotify.link",
    "spoti.fi",
    "on.soundcloud.com",
    "deezer.page.link",
)


def _emit(log_cb, message):
    if log_cb:
        log_cb(message)


def detect_platform(url):
    """
    Detects the source platform from a URL, URI, or raw Spotify playlist ID.
    Returns: 'Spotify', 'SoundCloud', 'Apple Music', 'Deezer', 'YouTube Music', 'YouTube', or 'Unknown'.
    """
    if not url:
        return "Unknown"
    raw = url.strip()
    url_lower = raw.lower()

    if (
        "spotify.com" in url_lower
        or url_lower.startswith("spotify:")
        or "spotify.link" in url_lower
        or "spoti.fi" in url_lower
        or re.fullmatch(r"[A-Za-z0-9]{22}", raw)
    ):
        return "Spotify"
    if "soundcloud.com" in url_lower:
        return "SoundCloud"
    if "music.apple.com" in url_lower:
        return "Apple Music"
    if "deezer.com" in url_lower or "deezer.page.link" in url_lower:
        return "Deezer"
    if "music.youtube.com" in url_lower:
        return "YouTube Music"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "YouTube"
    return "Unknown"


def resolve_short_url(url, timeout=10):
    """Expand short redirect links (spotify.link, on.soundcloud.com, deezer.page.link)."""
    url_lower = url.lower().strip()
    if not any(domain in url_lower for domain in SHORT_LINK_DOMAINS):
        return url
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=timeout)
        if resp.url:
            return resp.url
    except Exception:
        pass
    return url


def extract_playlist_id(url_or_id):
    """Extracts the Spotify playlist ID from a URL, URI, or returns a raw ID directly."""
    url_or_id = url_or_id.strip()

    if url_or_id.startswith("spotify:playlist:"):
        return url_or_id.split(":")[-1]

    match = re.search(r"playlist/([a-zA-Z0-9]+)", url_or_id)
    if match:
        return match.group(1)

    return url_or_id


def _extract_spotify_resource(url_or_id):
    """Returns (resource_type, resource_id) where resource_type is 'playlist', 'album', or 'track'."""
    raw = url_or_id.strip()
    uri_match = re.match(r"^spotify:(playlist|album|track):([a-zA-Z0-9]+)$", raw)
    if uri_match:
        return uri_match.group(1), uri_match.group(2)

    url_match = re.search(r"/(playlist|album|track)/([a-zA-Z0-9]+)", raw)
    if url_match:
        return url_match.group(1), url_match.group(2)

    return "playlist", raw


def _parse_spotify_track_obj(track, fallback_album="Unknown Album", fallback_cover=None):
    if not track or not isinstance(track, dict):
        return None
    if track.get("is_local") or (track.get("uri") and str(track["uri"]).startswith("spotify:local")):
        return None

    name = track.get("name")
    if not name:
        return None

    artists_list = track.get("artists") or []
    artists = [a.get("name") for a in artists_list if isinstance(a, dict) and a.get("name")]
    if not artists:
        return None

    primary_artist = artists[0]
    all_artists_str = ", ".join(artists)

    album_obj = track.get("album") or {}
    album_name = album_obj.get("name") or fallback_album
    album_artists = [
        a.get("name") for a in (album_obj.get("artists") or []) if isinstance(a, dict) and a.get("name")
    ]
    album_artist = ", ".join(album_artists) if album_artists else primary_artist

    release_date = str(album_obj.get("release_date") or "")
    year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""

    duration_ms = track.get("duration_ms") or 0
    duration_sec = duration_ms / 1000.0

    images = album_obj.get("images") or []
    cover_url = images[0].get("url") if images and isinstance(images[0], dict) else fallback_cover

    return {
        "title": name,
        "primary_artist": primary_artist,
        "all_artists": all_artists_str,
        "album": album_name,
        "album_artist": album_artist,
        "track_number": track.get("track_number"),
        "year": year,
        "duration_ms": duration_ms,
        "duration_sec": duration_sec,
        "cover_url": cover_url,
    }


def _get_spotify_tracks_official_api(resource_type, resource_id, client_id, client_secret):
    """Fetch Spotify tracks using user-provided official Developer API Client Credentials."""
    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    token_resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {auth_header}"},
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    if token_resp.status_code != 200:
        raise ValueError(f"Spotify API authentication failed (HTTP {token_resp.status_code}). Check Client ID / Secret.")
    access_token = token_resp.json().get("access_token")
    api_headers = {"Authorization": f"Bearer {access_token}"}

    tracks = []
    if resource_type == "track":
        resp = requests.get(f"https://api.spotify.com/v1/tracks/{resource_id}", headers=api_headers, timeout=15)
        resp.raise_for_status()
        parsed = _parse_spotify_track_obj(resp.json())
        return [parsed] if parsed else []

    if resource_type == "album":
        album_resp = requests.get(f"https://api.spotify.com/v1/albums/{resource_id}", headers=api_headers, timeout=15)
        album_resp.raise_for_status()
        album_data = album_resp.json()
        album_name = album_data.get("name") or "Unknown Album"
        images = album_data.get("images") or []
        cover_url = images[0].get("url") if images else None
        release_date = str(album_data.get("release_date") or "")
        year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""

        page = album_data.get("tracks") or {}
        while True:
            for item in page.get("items") or []:
                parsed = _parse_spotify_track_obj(item, fallback_album=album_name, fallback_cover=cover_url)
                if parsed:
                    if not parsed["year"]:
                        parsed["year"] = year
                    tracks.append(parsed)
            next_url = page.get("next")
            if not next_url:
                break
            r = requests.get(next_url, headers=api_headers, timeout=15)
            r.raise_for_status()
            page = r.json()
        return tracks

    # Playlist pagination
    url = (
        f"https://api.spotify.com/v1/playlists/{resource_id}/tracks"
        "?additional_types=track&limit=100"
    )
    while url:
        resp = requests.get(url, headers=api_headers, timeout=15)
        resp.raise_for_status()
        page = resp.json()
        for item in page.get("items") or []:
            if item and item.get("track"):
                parsed = _parse_spotify_track_obj(item["track"])
                if parsed:
                    tracks.append(parsed)
        url = page.get("next")
    return tracks


def _get_spotify_tracks_embed_fallback(resource_type, resource_id):
    """Lightweight fallback scraping Spotify's public embed page (__NEXT_DATA__)."""
    embed_url = f"https://open.spotify.com/embed/{resource_type}/{resource_id}"
    resp = requests.get(embed_url, headers=DEFAULT_HEADERS, timeout=15)
    resp.raise_for_status()
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text, re.DOTALL)
    if not match:
        raise ValueError("Could not locate embedded Spotify track payload.")

    payload = json.loads(match.group(1))
    entity = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("data", {})
        .get("entity", {})
    )
    if not entity:
        return []

    album_name = entity.get("name") or "Unknown Album"
    cover_sources = (entity.get("visualIdentity") or {}).get("image") or []
    fallback_cover = cover_sources[0].get("url") if cover_sources else None

    if resource_type == "track":
        artists = [a.get("name") for a in (entity.get("artists") or []) if a.get("name")]
        if not artists:
            artists = [entity.get("subtitle") or "Unknown Artist"]
        dur_ms = entity.get("duration") or 0
        return [{
            "title": entity.get("name") or "Unknown Title",
            "primary_artist": artists[0],
            "all_artists": ", ".join(artists),
            "album": album_name,
            "album_artist": artists[0],
            "track_number": None,
            "year": "",
            "duration_ms": dur_ms,
            "duration_sec": dur_ms / 1000.0,
            "cover_url": fallback_cover,
        }]

    tracks = []
    for idx, item in enumerate(entity.get("trackList") or [], start=1):
        title = (item.get("title") or "").replace("\xa0", " ").strip()
        subtitle = (item.get("subtitle") or "Unknown Artist").replace("\xa0", " ").strip()
        if not title:
            continue
        artists = [a.strip() for a in subtitle.split(",") if a.strip()] or ["Unknown Artist"]
        dur_ms = item.get("duration") or 0
        tracks.append({
            "title": title,
            "primary_artist": artists[0],
            "all_artists": ", ".join(artists),
            "album": album_name.replace("\xa0", " ").strip(),
            "album_artist": artists[0],
            "track_number": idx,
            "year": "",
            "duration_ms": dur_ms,
            "duration_sec": dur_ms / 1000.0,
            "cover_url": fallback_cover,
        })
    return tracks


def get_spotify_client(client_id=None, client_secret=None):
    """Lazily imports and returns a SpotipyFree.Spotify client."""
    from SpotipyFree import Spotify

    return Spotify()


def get_spotify_playlist_tracks(sp, playlist_id, log_cb=None):
    """Fetches all tracks from a Spotify playlist using SpotipyFree with full pagination."""
    tracks = []
    try:
        results = sp.playlist_items(
            playlist_id,
            fields="items(track(name,duration_ms,is_local,uri,track_number,artists(name),album(name,release_date,images,artists(name)))),next",
            additional_types=("track",),
        )
    except Exception as e:
        raise Exception(f"Failed to fetch Spotify playlist items: {str(e)}")

    if not results:
        return tracks

    def process_items(items):
        for item in items or []:
            if not item or not item.get("track"):
                continue
            parsed = _parse_spotify_track_obj(item["track"])
            if parsed:
                tracks.append(parsed)

    process_items(results.get("items", []))

    while results.get("next"):
        try:
            results = sp.next(results)
            if results:
                process_items(results.get("items", []))
            else:
                break
        except Exception as e:
            _emit(log_cb, f"[WARNING] Error paginating Spotify playlist items: {e}")
            break

    return tracks


def get_deezer_playlist_tracks(url, log_cb=None):
    """
    Fetches tracks from Deezer playlists, albums, or single tracks using
    Deezer's official public REST API (zero credentials required).
    """
    url = resolve_short_url(url)
    match = re.search(r"deezer\.com/(?:[a-z]{2}/)?(playlist|album|track)/(\d+)", url, re.IGNORECASE)
    if not match:
        raise ValueError("Invalid Deezer URL. Expected a playlist, album, or track link.")

    res_type = match.group(1).lower()
    res_id = match.group(2)

    def _parse_deezer_track(item, fallback_album="Unknown Album", fallback_cover=None, track_num=None):
        title = item.get("title") or item.get("title_short")
        if not title:
            return None
        artist_obj = item.get("artist") or {}
        artist_name = artist_obj.get("name") or "Unknown Artist"
        contributors = item.get("contributors") or []
        all_artists = [c.get("name") for c in contributors if isinstance(c, dict) and c.get("name")]
        if not all_artists:
            all_artists = [artist_name]

        album_obj = item.get("album") or {}
        album_name = album_obj.get("title") or fallback_album
        cover_url = (
            album_obj.get("cover_xl")
            or album_obj.get("cover_big")
            or album_obj.get("cover_medium")
            or fallback_cover
        )
        duration_sec = item.get("duration") or 0
        release_date = str(item.get("release_date") or "")
        year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""
        return {
            "title": title,
            "primary_artist": all_artists[0],
            "all_artists": ", ".join(all_artists),
            "album": album_name,
            "album_artist": all_artists[0],
            "track_number": item.get("track_position") or track_num,
            "year": year,
            "duration_ms": duration_sec * 1000.0,
            "duration_sec": float(duration_sec),
            "cover_url": cover_url,
        }

    if res_type == "track":
        resp = requests.get(f"https://api.deezer.com/track/{res_id}", headers=DEFAULT_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise ValueError(f"Deezer API error: {data['error'].get('message', 'Unknown error')}")
        parsed = _parse_deezer_track(data)
        return [parsed] if parsed else []

    if res_type == "album":
        resp = requests.get(f"https://api.deezer.com/album/{res_id}", headers=DEFAULT_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise ValueError(f"Deezer API error: {data['error'].get('message', 'Unknown error')}")
        album_name = data.get("title") or "Unknown Album"
        cover_url = data.get("cover_xl") or data.get("cover_big")
        tracks = []
        for idx, item in enumerate((data.get("tracks") or {}).get("data") or [], start=1):
            parsed = _parse_deezer_track(item, fallback_album=album_name, fallback_cover=cover_url, track_num=idx)
            if parsed:
                tracks.append(parsed)
        return tracks

    # Playlist with full pagination
    tracks = []
    next_url = f"https://api.deezer.com/playlist/{res_id}/tracks?limit=100"
    while next_url:
        resp = requests.get(next_url, headers=DEFAULT_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise ValueError(f"Deezer API error: {data['error'].get('message', 'Unknown error')}")
        for idx, item in enumerate(data.get("data") or [], start=len(tracks) + 1):
            parsed = _parse_deezer_track(item, track_num=idx)
            if parsed:
                tracks.append(parsed)
        next_url = data.get("next")
    return tracks


def get_apple_music_playlist_tracks(url, log_cb=None):
    """
    Fetches tracks from Apple Music playlists, albums, or songs by parsing
    Apple Music's hydrated serialized-server-data / ld+json payloads.
    """
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text

    tracks = []
    seen = set()

    def _format_artwork(artwork_obj):
        if not isinstance(artwork_obj, dict):
            return None
        tpl = artwork_obj.get("url")
        if not tpl:
            return None
        return tpl.replace("{w}", "600").replace("{h}", "600").replace("{f}", "jpg")

    def _walk_server_data(node):
        if isinstance(node, list):
            for el in node:
                _walk_server_data(el)
        elif isinstance(node, dict):
            # Look for track lockups / song objects in Apple Music server data
            if node.get("type") in ("songs", "music-videos") and isinstance(node.get("attributes"), dict):
                attrs = node["attributes"]
                title = attrs.get("name")
                artist = attrs.get("artistName")
                if title and artist:
                    key = (title.lower(), artist.lower(), attrs.get("durationInMillis") or 0)
                    if key not in seen:
                        seen.add(key)
                        dur_ms = attrs.get("durationInMillis") or 0
                        release_date = str(attrs.get("releaseDate") or "")
                        year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""
                        tracks.append({
                            "title": title,
                            "primary_artist": artist.split(",")[0].split("&")[0].strip() or artist,
                            "all_artists": artist,
                            "album": attrs.get("albumName") or "Unknown Album",
                            "album_artist": artist,
                            "track_number": attrs.get("trackNumber"),
                            "year": year,
                            "duration_ms": dur_ms,
                            "duration_sec": dur_ms / 1000.0,
                            "cover_url": _format_artwork(attrs.get("artwork")),
                        })
            elif node.get("itemKind") == "trackLockup" and node.get("title"):
                title = node.get("title")
                artist = node.get("artistName") or "Unknown Artist"
                dur_ms = node.get("duration") or 0
                key = (title.lower(), artist.lower(), dur_ms)
                if key not in seen:
                    seen.add(key)
                    artwork = _format_artwork((node.get("artwork") or {}).get("dictionary") or node.get("artwork"))
                    tracks.append({
                        "title": title,
                        "primary_artist": artist.split(",")[0].split("&")[0].strip() or artist,
                        "all_artists": artist,
                        "album": node.get("tertiaryLinks", [{}])[0].get("title", "Unknown Album")
                        if isinstance(node.get("tertiaryLinks"), list) and node.get("tertiaryLinks")
                        else "Unknown Album",
                        "album_artist": artist,
                        "track_number": node.get("trackNumber"),
                        "year": "",
                        "duration_ms": dur_ms,
                        "duration_sec": dur_ms / 1000.0,
                        "cover_url": artwork,
                    })
            for v in node.values():
                if isinstance(v, (dict, list)):
                    _walk_server_data(v)

    # 1. Try Apple Music's serialized-server-data JSON
    server_data_match = re.search(
        r'<script[^>]+id="serialized-server-data"[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE
    )
    if server_data_match:
        try:
            data = json.loads(server_data_match.group(1))
            _walk_server_data(data)
        except Exception as e:
            _emit(log_cb, f"[WARNING] Could not parse Apple Music server data: {e}")

    # 2. Fallback to JSON-LD (MusicPlaylist / MusicAlbum / MusicRecording)
    if not tracks:
        for ld_match in re.finditer(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE
        ):
            try:
                ld = json.loads(ld_match.group(1))
            except Exception:
                continue
            items = ld.get("track") if isinstance(ld, dict) else None
            if isinstance(items, dict):
                items = [items]
            album_name = (ld.get("name") if isinstance(ld, dict) else None) or "Unknown Album"
            cover_url = ld.get("image") if isinstance(ld, dict) and isinstance(ld.get("image"), str) else None
            for idx, item in enumerate(items or [], start=1):
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                by_artist = item.get("byArtist")
                if isinstance(by_artist, list) and by_artist:
                    artist = ", ".join(a.get("name", "") for a in by_artist if isinstance(a, dict) and a.get("name"))
                elif isinstance(by_artist, dict):
                    artist = by_artist.get("name", "Unknown Artist")
                else:
                    artist = "Unknown Artist"
                dur_sec = _parse_iso8601_duration(item.get("duration") or "")
                tracks.append({
                    "title": item["name"],
                    "primary_artist": artist.split(",")[0].strip() or "Unknown Artist",
                    "all_artists": artist or "Unknown Artist",
                    "album": album_name,
                    "album_artist": artist or "Unknown Artist",
                    "track_number": idx,
                    "year": "",
                    "duration_ms": dur_sec * 1000.0,
                    "duration_sec": float(dur_sec),
                    "cover_url": cover_url,
                })

    if not tracks:
        raise ValueError("Could not extract tracks from Apple Music URL. Make sure the playlist/album is public.")
    return tracks


def _parse_iso8601_duration(duration_str):
    """Parse ISO-8601 duration like PT3M45S into seconds."""
    if not duration_str:
        return 0
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(duration_str))
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _pick_best_thumbnail(entry):
    thumbs = entry.get("thumbnails")
    if isinstance(thumbs, list) and thumbs:
        valid = [t for t in thumbs if isinstance(t, dict) and t.get("url")]
        if valid:
            best = max(valid, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
            return best.get("url")
    return entry.get("thumbnail")


def get_yt_dlp_playlist_tracks(url, is_youtube=False):
    """
    Fetches playlist or single-track entries using yt-dlp flat extraction.
    Preserves YouTube video_id when is_youtube=True so YouTube tracks skip redundant search.
    """
    ydl_opts = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise ValueError("Could not extract playlist information.")

    playlist_title = info.get("title") or "Unknown Album"
    entries = list(info.get("entries") or [])

    # Single video/track URL support
    if not entries and info.get("id"):
        entries = [info]

    tracks = []
    for idx, entry in enumerate(entries, start=1):
        if not entry:
            continue

        title = entry.get("title") or "Unknown Title"
        if title in ("[Deleted video]", "[Private video]"):
            continue

        artist = entry.get("artist") or entry.get("uploader") or entry.get("channel") or "Unknown Artist"
        if artist.endswith(" - Topic"):
            artist = artist[:-8].strip()

        if " - " in title:
            parts = title.split(" - ", 1)
            possible_artist = parts[0].strip()
            possible_title = parts[1].strip()
            if possible_artist and possible_title:
                artist = possible_artist
                title = possible_title

        duration_sec = entry.get("duration") or 0
        duration_ms = float(duration_sec) * 1000.0
        cover_url = _pick_best_thumbnail(entry)
        album = entry.get("album") or playlist_title

        track_dict = {
            "title": title,
            "primary_artist": artist,
            "all_artists": artist,
            "album": album,
            "album_artist": artist,
            "track_number": idx,
            "year": str(entry.get("release_year") or ""),
            "duration_ms": duration_ms,
            "duration_sec": float(duration_sec),
            "cover_url": cover_url,
        }
        if is_youtube and entry.get("id"):
            track_dict["video_id"] = entry.get("id")
        tracks.append(track_dict)

    return tracks


def get_soundcloud_playlist_tracks(url, log_cb=None):
    """
    Fetches SoundCloud playlist tracks using yt-dlp flat extraction and
    SoundCloud v2 API batch track resolution (with timeouts and graceful fallback).
    """
    url = resolve_short_url(url)

    ydl_opts = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise ValueError("Could not extract SoundCloud playlist information.")

    entries = list(info.get("entries") or [])
    playlist_title = info.get("title") or "Unknown Album"

    if not entries:
        ydl_opts_single = {
            "extract_flat": False,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts_single) as ydl_s:
            single_info = ydl_s.extract_info(url, download=False)
        if single_info:
            title = single_info.get("title") or "Unknown Title"
            artist = single_info.get("artist") or single_info.get("uploader") or "Unknown Artist"
            duration_sec = single_info.get("duration") or 0
            cover_url = _pick_best_thumbnail(single_info)
            album = single_info.get("album") or "Unknown Album"
            return [{
                "title": title,
                "primary_artist": artist,
                "all_artists": artist,
                "album": album,
                "album_artist": artist,
                "track_number": 1,
                "year": "",
                "duration_ms": float(duration_sec) * 1000.0,
                "duration_sec": float(duration_sec),
                "cover_url": cover_url,
            }]
        return []

    track_ids = [str(entry["id"]) for entry in entries if entry and entry.get("id")]

    client_id = None
    try:
        ydl_sc = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
        sc_ie = ydl_sc.get_info_extractor("Soundcloud")
        sc_ie._update_client_id()
        client_id = sc_ie._CLIENT_ID
    except Exception as e:
        _emit(log_cb, f"[WARNING] Failed to extract SoundCloud client_id: {e}")

    resolved_tracks = {}
    if client_id and track_ids:
        batch_size = 50
        for i in range(0, len(track_ids), batch_size):
            batch = track_ids[i : i + batch_size]
            ids_str = ",".join(batch)
            api_url = f"https://api-v2.soundcloud.com/tracks?ids={ids_str}&client_id={client_id}"
            try:
                resp = requests.get(api_url, headers=DEFAULT_HEADERS, timeout=15)
                resp.raise_for_status()
                for track_data in resp.json():
                    if isinstance(track_data, dict) and track_data.get("id"):
                        resolved_tracks[str(track_data["id"])] = track_data
            except Exception as e:
                _emit(log_cb, f"[WARNING] Failed to fetch SoundCloud metadata batch {i // batch_size + 1}: {e}")

    tracks = []
    for idx, entry in enumerate(entries, start=1):
        if not entry:
            continue

        tid = str(entry.get("id") or "")
        track_data = resolved_tracks.get(tid)

        if track_data:
            title = track_data.get("title") or "Unknown Title"
            user = track_data.get("user") or {}
            artist = (track_data.get("publisher_metadata") or {}).get("artist") or user.get("username") or "Unknown Artist"
            duration_ms = track_data.get("duration") or 0
            duration_sec = duration_ms / 1000.0

            cover_url = track_data.get("artwork_url") or user.get("avatar_url")
            if cover_url and "-large." in cover_url:
                cover_url = cover_url.replace("-large.", "-t500x500.")

            album = playlist_title
        else:
            title = entry.get("title") or "Unknown Title"
            artist = entry.get("artist") or entry.get("uploader") or entry.get("channel") or "Unknown Artist"

            entry_url = entry.get("url") or entry.get("webpage_url")
            if (artist in ("Unknown Artist", "Tracks")) and entry_url:
                try:
                    parsed_url = urlparse(entry_url)
                    path_parts = [p for p in parsed_url.path.split("/") if p]
                    if len(path_parts) >= 2 and path_parts[0] != "tracks":
                        artist_slug = path_parts[0].replace("-", " ").replace("_", " ").title()
                        track_slug = path_parts[1].replace("-", " ").replace("_", " ").title()
                        artist = artist_slug
                        if title == "Unknown Title" or title == track_slug or title.isdigit():
                            title = track_slug
                except Exception:
                    pass

            if " - " in title:
                parts = title.split(" - ", 1)
                possible_artist = parts[0].strip()
                possible_title = parts[1].strip()
                if possible_artist and possible_title:
                    artist = possible_artist
                    title = possible_title

            duration_sec = entry.get("duration") or 0
            duration_ms = float(duration_sec) * 1000.0
            cover_url = _pick_best_thumbnail(entry)
            album = entry.get("album") or playlist_title

        tracks.append({
            "title": title,
            "primary_artist": artist,
            "all_artists": artist,
            "album": album,
            "album_artist": artist,
            "track_number": idx,
            "year": "",
            "duration_ms": duration_ms,
            "duration_sec": float(duration_sec),
            "cover_url": cover_url,
        })

    return tracks


def get_playlist_tracks_general(url_or_id, client_id=None, client_secret=None, log_cb=None):
    """Extracts tracks from any supported platform playlist, album, or track."""
    resolved = resolve_short_url(url_or_id.strip())
    platform = detect_platform(resolved)

    if platform == "Spotify":
        res_type, res_id = _extract_spotify_resource(resolved)
        if client_id and client_secret:
            _emit(log_cb, "[SPOTIFY] Using custom Spotify API credentials...")
            return _get_spotify_tracks_official_api(res_type, res_id, client_id, client_secret)
        if res_type == "playlist":
            try:
                sp = get_spotify_client()
                return get_spotify_playlist_tracks(sp, res_id, log_cb=log_cb)
            except Exception:
                _emit(log_cb, "[SPOTIFY] Extraction publique sans clé API...")
                return _get_spotify_tracks_embed_fallback(res_type, res_id)
        return _get_spotify_tracks_embed_fallback(res_type, res_id)

    if platform == "SoundCloud":
        return get_soundcloud_playlist_tracks(resolved, log_cb=log_cb)

    if platform == "Deezer":
        return get_deezer_playlist_tracks(resolved, log_cb=log_cb)

    if platform == "Apple Music":
        return get_apple_music_playlist_tracks(resolved, log_cb=log_cb)

    if platform in ("YouTube", "YouTube Music"):
        return get_yt_dlp_playlist_tracks(resolved, is_youtube=True)

    return get_yt_dlp_playlist_tracks(resolved, is_youtube=False)
