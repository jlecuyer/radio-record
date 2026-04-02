"""Tests for the track library: naming, dedup, tagging."""

import json
from pathlib import Path

import pytest

from radio_record.library import (
    CONTENT_TYPE_EXTENSIONS,
    TrackLibrary,
    extension_for_content_type,
    sanitize_filename,
)


# ---- sanitize_filename ----

class TestSanitizeFilename:
    def test_normal_title(self):
        assert sanitize_filename("Artist - Song Title") == "Artist - Song Title"

    def test_strips_illegal_chars(self):
        assert sanitize_filename('A<B>C:D"E/F\\G|H?I*J') == "A_B_C_D_E_F_G_H_I_J"

    def test_collapses_underscores(self):
        assert sanitize_filename("A::B") == "A_B"

    def test_collapses_whitespace(self):
        assert sanitize_filename("A   B   C") == "A B C"

    def test_tab_treated_as_control_char(self):
        # \t is \x09, falls in \x00-\x1f range → replaced with _
        assert sanitize_filename("A\tB") == "A_B"

    def test_strips_leading_trailing_dots_spaces(self):
        assert sanitize_filename("  ..song.. ") == "song"

    def test_truncates_long_names(self):
        long = "A" * 300
        assert len(sanitize_filename(long)) == 200

    def test_empty_becomes_unknown(self):
        assert sanitize_filename("") == "unknown"

    def test_only_dots_and_spaces(self):
        assert sanitize_filename("... ...") == "unknown"

    def test_control_characters_removed(self):
        assert sanitize_filename("Song\x00\x1fName") == "Song_Name"


# ---- extension_for_content_type ----

class TestExtensionForContentType:
    @pytest.mark.parametrize("ct,ext", [
        ("audio/mpeg", ".mp3"),
        ("audio/ogg", ".ogg"),
        ("application/ogg", ".ogg"),
        ("audio/aac", ".aac"),
        ("audio/aacp", ".aac"),
        ("audio/flac", ".flac"),
    ])
    def test_known_types(self, ct, ext):
        assert extension_for_content_type(ct) == ext

    def test_unknown_defaults_to_mp3(self):
        assert extension_for_content_type("audio/unknown") == ".mp3"

    def test_strips_parameters(self):
        assert extension_for_content_type("audio/ogg; codecs=vorbis") == ".ogg"

    def test_case_insensitive(self):
        assert extension_for_content_type("Audio/MPEG") == ".mp3"


# ---- TrackLibrary._decide ----

class TestDecide:
    def test_complete_replaces_partial(self):
        assert TrackLibrary._decide(1000, True, 500, False) == "replace"

    def test_partial_never_replaces_complete(self):
        assert TrackLibrary._decide(500, False, 2000, True) == "keep_existing"

    def test_same_completeness_larger_wins(self):
        assert TrackLibrary._decide(1000, True, 2000, True) == "replace"
        assert TrackLibrary._decide(1000, False, 2000, False) == "replace"

    def test_same_completeness_smaller_keeps(self):
        assert TrackLibrary._decide(2000, True, 1000, True) == "keep_existing"
        assert TrackLibrary._decide(2000, False, 1000, False) == "keep_existing"

    def test_same_completeness_equal_size_keeps(self):
        assert TrackLibrary._decide(1000, True, 1000, True) == "keep_existing"
        assert TrackLibrary._decide(1000, False, 1000, False) == "keep_existing"


# ---- TrackLibrary.ingest ----

class TestTrackLibraryIngest:
    def test_new_track(self, tmp_path):
        lib = TrackLibrary(tmp_path)
        temp = tmp_path / ".recording_tmp.mp3"
        temp.write_bytes(b"\xff" * 100)

        result = lib.ingest("Artist - Song", temp, "audio/mpeg", is_partial=False)

        assert result is not None
        assert result.name == "Artist - Song.mp3"
        assert result.exists()
        assert not temp.exists()  # moved
        assert lib.stats["new"] == 1

        # Check metadata sidecar
        meta_file = tmp_path / ".track_metadata" / "Artist - Song.json"
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text())
        assert meta["title"] == "Artist - Song"
        assert meta["is_partial"] is False

    def test_empty_title_discarded(self, tmp_path):
        lib = TrackLibrary(tmp_path)
        temp = tmp_path / ".recording_tmp.mp3"
        temp.write_bytes(b"\xff" * 100)

        result = lib.ingest("", temp, "audio/mpeg", is_partial=False)
        assert result is None
        assert not temp.exists()

    def test_zero_length_discarded(self, tmp_path):
        lib = TrackLibrary(tmp_path)
        temp = tmp_path / ".recording_tmp.mp3"
        temp.write_bytes(b"")

        result = lib.ingest("Song", temp, "audio/mpeg", is_partial=False)
        assert result is None
        assert not temp.exists()

    def test_complete_replaces_partial(self, tmp_path):
        lib = TrackLibrary(tmp_path)

        # Use audio/ogg to avoid ID3 tagging modifying file sizes
        temp1 = tmp_path / ".rec1.ogg"
        temp1.write_bytes(b"\xff" * 50)
        lib.ingest("Song", temp1, "audio/ogg", is_partial=True)

        temp2 = tmp_path / ".rec2.ogg"
        temp2.write_bytes(b"\xff" * 30)
        result = lib.ingest("Song", temp2, "audio/ogg", is_partial=False)

        assert result is not None
        assert result.stat().st_size == 30
        assert lib.stats["replaced"] == 1

        meta = json.loads((tmp_path / ".track_metadata" / "Song.json").read_text())
        assert meta["is_partial"] is False

    def test_partial_does_not_replace_complete(self, tmp_path):
        lib = TrackLibrary(tmp_path)

        temp1 = tmp_path / ".rec1.ogg"
        temp1.write_bytes(b"\xff" * 50)
        lib.ingest("Song", temp1, "audio/ogg", is_partial=False)

        temp2 = tmp_path / ".rec2.ogg"
        temp2.write_bytes(b"\xff" * 100)
        result = lib.ingest("Song", temp2, "audio/ogg", is_partial=True)

        assert result is None
        assert lib.stats["skipped"] == 1
        target = tmp_path / "Song.ogg"
        assert target.stat().st_size == 50

    def test_larger_same_completeness_replaces(self, tmp_path):
        lib = TrackLibrary(tmp_path)

        temp1 = tmp_path / ".rec1.ogg"
        temp1.write_bytes(b"\xff" * 50)
        lib.ingest("Song", temp1, "audio/ogg", is_partial=True)

        temp2 = tmp_path / ".rec2.ogg"
        temp2.write_bytes(b"\xff" * 100)
        result = lib.ingest("Song", temp2, "audio/ogg", is_partial=True)

        assert result is not None
        assert result.stat().st_size == 100
        assert lib.stats["replaced"] == 1

    def test_smaller_same_completeness_skipped(self, tmp_path):
        lib = TrackLibrary(tmp_path)

        temp1 = tmp_path / ".rec1.ogg"
        temp1.write_bytes(b"\xff" * 100)
        lib.ingest("Song", temp1, "audio/ogg", is_partial=True)

        temp2 = tmp_path / ".rec2.ogg"
        temp2.write_bytes(b"\xff" * 50)
        result = lib.ingest("Song", temp2, "audio/ogg", is_partial=True)

        assert result is None
        assert lib.stats["skipped"] == 1

    def test_ogg_extension(self, tmp_path):
        lib = TrackLibrary(tmp_path)
        temp = tmp_path / ".rec.ogg"
        temp.write_bytes(b"\xff" * 10)

        result = lib.ingest("Song", temp, "audio/ogg", is_partial=False)
        assert result.name == "Song.ogg"

    def test_partial_track_label(self, tmp_path):
        lib = TrackLibrary(tmp_path)
        temp = tmp_path / ".rec.mp3"
        temp.write_bytes(b"\xff" * 10)

        result = lib.ingest("Song", temp, "audio/mpeg", is_partial=True)
        assert result is not None

        meta = json.loads((tmp_path / ".track_metadata" / "Song.json").read_text())
        assert meta["is_partial"] is True

    def test_corrupted_meta_file_treated_as_empty(self, tmp_path):
        lib = TrackLibrary(tmp_path)

        # Create existing track with corrupted metadata
        target = tmp_path / "Song.mp3"
        target.write_bytes(b"\xff" * 50)
        meta_file = tmp_path / ".track_metadata" / "Song.json"
        meta_file.write_text("not json{{{")

        temp = tmp_path / ".rec.mp3"
        temp.write_bytes(b"\xff" * 100)

        # Should succeed — corrupted meta treated as empty (partial=True by default)
        result = lib.ingest("Song", temp, "audio/mpeg", is_partial=False)
        assert result is not None


# ---- TrackLibrary._tag_mp3 ----

class TestTagMp3:
    def test_tags_artist_and_title(self, tmp_path):
        from mutagen.id3 import ID3

        track = tmp_path / "test.mp3"
        # Minimal valid MP3 frame (MPEG1 Layer3 128kbps)
        track.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 417)

        TrackLibrary._tag_mp3(track, "Artist Name - Song Title")

        tags = ID3(track)
        assert str(tags["TIT2"]) == "Song Title"
        assert str(tags["TPE1"]) == "Artist Name"

    def test_tags_title_only_no_dash(self, tmp_path):
        from mutagen.id3 import ID3

        track = tmp_path / "test.mp3"
        track.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 417)

        TrackLibrary._tag_mp3(track, "Just A Song Title")

        tags = ID3(track)
        assert str(tags["TIT2"]) == "Just A Song Title"
        assert "TPE1" not in tags

    def test_tags_handles_invalid_file(self, tmp_path):
        track = tmp_path / "test.mp3"
        track.write_bytes(b"not an mp3 file at all")

        # Should not raise — errors are caught and logged
        TrackLibrary._tag_mp3(track, "Song")
