import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import clean_text, get_similarity_ratio, DEFAULT_CONFIG, load_config, save_config
from core.playlist_extractor import (
    detect_platform,
    extract_playlist_id,
    _extract_spotify_resource,
    _parse_iso8601_duration,
)
from core.downloader import sanitize_filename
from core.updater import parse_semver, is_newer_version, _select_windows_asset, check_for_updates
from core.yt_matcher import find_best_match
from core.orchestrator import orchestrate_download


class TestUtilsAndSanitization(unittest.TestCase):
    def test_clean_text_preserves_real_words_with_word_boundaries(self):
        self.assertEqual(clean_text("Take Credit (Credit)"), "take credit credit")
        self.assertEqual(clean_text("Still Alive (Alive)"), "still alive alive")
        self.assertEqual(clean_text("Track_Name (Remastered 2020)"), "track name")
        self.assertEqual(clean_text("Song (feat. Guest) - Radio Edit"), "song")

    def test_sanitize_filename_unicode_and_reserved_names(self):
        # Keeps valid Unicode instead of stripping it
        self.assertEqual(sanitize_filename("BTS - 봄날 (Spring Day)"), "BTS - 봄날 (Spring Day)")
        # Strips illegal Windows characters
        self.assertEqual(sanitize_filename('A/B:C*D?E"F<G>H|I'), "ABCDEFGHI")
        # Escapes Windows reserved names
        self.assertEqual(sanitize_filename("CON"), "_CON")
        self.assertEqual(sanitize_filename("nul"), "_nul")
        # Enforces max length
        long_name = "A" * 300
        self.assertLessEqual(len(sanitize_filename(long_name)), 160)

    def test_similarity_empty_string_guard(self):
        self.assertEqual(get_similarity_ratio("", "hello"), 0.0)
        self.assertEqual(get_similarity_ratio("   ", "hello"), 0.0)


class TestPlatformDetection(unittest.TestCase):
    def test_all_platforms_and_raw_spotify_id(self):
        self.assertEqual(detect_platform("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"), "Spotify")
        self.assertEqual(detect_platform("37i9dQZF1DXcBWIGoYBM5M"), "Spotify")
        self.assertEqual(detect_platform("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"), "Spotify")
        self.assertEqual(detect_platform("https://spotify.link/abc123"), "Spotify")
        self.assertEqual(detect_platform("https://soundcloud.com/artist/sets/my-set"), "SoundCloud")
        self.assertEqual(detect_platform("https://on.soundcloud.com/xyz"), "SoundCloud")
        self.assertEqual(detect_platform("https://www.deezer.com/fr/playlist/123456"), "Deezer")
        self.assertEqual(detect_platform("https://music.apple.com/fr/playlist/test/pl.u-123"), "Apple Music")
        self.assertEqual(detect_platform("https://music.youtube.com/playlist?list=PL123"), "YouTube Music")
        self.assertEqual(detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "YouTube")
        self.assertEqual(detect_platform("https://example.com/music"), "Unknown")

    def test_spotify_resource_parsing(self):
        self.assertEqual(_extract_spotify_resource("https://open.spotify.com/album/12345?si=abc"), ("album", "12345"))
        self.assertEqual(_extract_spotify_resource("spotify:track:99999"), ("track", "99999"))
        self.assertEqual(extract_playlist_id("37i9dQZF1DXcBWIGoYBM5M"), "37i9dQZF1DXcBWIGoYBM5M")

    def test_iso_duration(self):
        self.assertEqual(_parse_iso8601_duration("PT3M45S"), 225)
        self.assertEqual(_parse_iso8601_duration("PT1H2M3S"), 3723)


class TestYTMatcher(unittest.TestCase):
    @patch("core.yt_matcher.get_yt_client")
    def test_matcher_uses_artist_and_picks_best_candidate(self, mock_client_fn):
        mock_yt = MagicMock()
        mock_yt.search.return_value = [
            # Wrong artist (cover) with same title and close duration
            {
                "videoId": "wrong_cover_id",
                "title": "Blinding Lights",
                "artists": [{"name": "Random Karaoke Band"}],
                "duration_seconds": 201,
            },
            # Exact artist + exact duration
            {
                "videoId": "correct_song_id",
                "title": "Blinding Lights",
                "artists": [{"name": "The Weeknd"}],
                "duration_seconds": 200,
            },
        ]
        mock_client_fn.return_value = mock_yt

        vid, msg = find_best_match({
            "title": "Blinding Lights",
            "primary_artist": "The Weeknd",
            "all_artists": "The Weeknd",
            "duration_sec": 200,
        })
        self.assertEqual(vid, "correct_song_id")
        self.assertIn("Verified match", msg)

    @patch("core.yt_matcher.get_yt_client")
    def test_matcher_rejects_empty_title_false_positive(self, mock_client_fn):
        mock_yt = MagicMock()
        mock_yt.search.return_value = [
            {
                "videoId": "bad_id",
                "title": "Totally Different Song",
                "artists": [{"name": "Other Artist"}],
                "duration_seconds": 200,
            }
        ]
        mock_client_fn.return_value = mock_yt

        vid, _ = find_best_match({
            "title": "???",
            "primary_artist": "Someone",
            "all_artists": "Someone",
            "duration_sec": 200,
        })
        self.assertIsNone(vid)


class TestUpdater(unittest.TestCase):
    def test_semver_parsing_and_comparison(self):
        self.assertEqual(parse_semver("3.0.0"), (3, 0, 0))
        self.assertEqual(parse_semver("v2.3.0"), (2, 3, 0))
        self.assertIsNone(parse_semver("DYTPDWNLD V1"))
        self.assertTrue(is_newer_version("3.0.0", "2.0.1"))
        self.assertFalse(is_newer_version("2.0.1", "2.0.1"))
        self.assertFalse(is_newer_version("2.0.0", "2.0.1"))

    def test_select_windows_asset_handles_rename(self):
        assets = [
            {"name": "MusicDL_compressed.zip", "browser_download_url": "https://x/z.zip", "size": 100},
            {"name": "MusicDL.exe", "browser_download_url": "https://x/app.exe", "size": 200, "digest": "sha256:abcd"},
        ]
        url, name, size, sha = _select_windows_asset(assets)
        self.assertEqual(name, "MusicDL.exe")
        self.assertEqual(url, "https://x/app.exe")
        self.assertEqual(size, 200)
        self.assertEqual(sha, "abcd")

    @patch("core.updater.requests.get")
    def test_check_for_updates_detects_release(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"ETag": '"abc123"'}
        mock_resp.json.return_value = {
            "tag_name": "9.9.9",
            "name": "MusicDL 9.9.9",
            "body": "Big update",
            "html_url": "https://github.com/djael-ml/DjaelYTPlaylistDWNLD/releases/tag/9.9.9",
            "published_at": "2026-11-27T00:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "MusicDL.exe", "browser_download_url": "https://x/MusicDL.exe", "size": 50}],
        }
        mock_get.return_value = mock_resp

        cfg = dict(DEFAULT_CONFIG)
        info = check_for_updates(cfg, force=True)
        self.assertIsNotNone(info)
        self.assertEqual(info.latest_version, "9.9.9")
        self.assertEqual(info.asset_name, "MusicDL.exe")


class TestOrchestratorSafety(unittest.TestCase):
    @patch("core.orchestrator.ensure_ffmpeg", return_value=("ffmpeg.exe", "."))
    @patch("core.orchestrator.download_track")
    @patch("core.orchestrator.find_best_match")
    @patch("core.orchestrator.scan_local_mp3s")
    @patch("core.orchestrator.get_playlist_tracks_general")
    def test_failed_upgrade_does_not_delete_existing_local_file(
        self, mock_extract, mock_scan, mock_match, mock_dl, _mock_ffmpeg
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            existing_file = os.path.join(tmpdir, "Daft Punk - One More Time.mp3")
            with open(existing_file, "wb") as f:
                f.write(b"EXISTING_AUDIO_BYTES")

            mock_extract.return_value = [{
                "title": "One More Time",
                "primary_artist": "Daft Punk",
                "all_artists": "Daft Punk",
                "album": "Discovery",
                "duration_sec": 320,
                "cover_url": None,
            }]
            mock_scan.return_value = [{
                "filepath": existing_file,
                "title": "One More Time",
                "artist": "Daft Punk",
                "bitrate": 128,
            }]
            mock_match.return_value = ("vid123", "Verified match")
            mock_dl.side_effect = RuntimeError("Network failure")

            summary = orchestrate_download(
                playlist_url="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
                output_dir=tmpdir,
                bitrate="320",
                max_workers=1,
                overwrite_mode="upgrade",
            )

            # Critical invariant: the user's original file MUST still exist untouched!
            self.assertTrue(os.path.exists(existing_file))
            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.upgraded, 0)

    @patch("core.orchestrator.ensure_ffmpeg", return_value=("ffmpeg.exe", "."))
    @patch("core.orchestrator.apply_metadata")
    @patch("core.orchestrator.download_track")
    @patch("core.orchestrator.find_best_match")
    @patch("core.orchestrator.scan_local_mp3s")
    @patch("core.orchestrator.get_playlist_tracks_general")
    def test_skip_mode_never_overwrites(self, mock_extract, mock_scan, mock_match, mock_dl, mock_tag, _mock_ffmpeg):
        with tempfile.TemporaryDirectory() as tmpdir:
            existing_file = os.path.join(tmpdir, "Artist - Song.mp3")
            mock_extract.return_value = [{
                "title": "Song",
                "primary_artist": "Artist",
                "all_artists": "Artist",
                "album": "Album",
                "duration_sec": 180,
                "cover_url": None,
            }]
            mock_scan.return_value = [{
                "filepath": existing_file,
                "title": "Song",
                "artist": "Artist",
                "bitrate": 128,
            }]

            summary = orchestrate_download(
                playlist_url="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
                output_dir=tmpdir,
                bitrate="320",
                max_workers=2,
                overwrite_mode="skip",
            )
            self.assertEqual(summary.skipped, 1)
            mock_match.assert_not_called()
            mock_dl.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
