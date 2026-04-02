"""Tests for the recording orchestration."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from radio_record.icy import StreamInfo, StreamMetadata
from radio_record.recorder import Recorder


def _make_metadata(title):
    """Helper: create a StreamMetadata with the given title."""
    return StreamMetadata(stream_title=title)


class TestRecordSession:
    """Test _record_session with a mocked ICYStream."""

    def _run_session(self, tmp_path, chunks):
        """Run a single recording session with predetermined chunks.

        Args:
            chunks: list of (audio_bytes, StreamMetadata | None) tuples
                    that ICYStream.read_chunk() will yield in order.
        """
        recorder = Recorder(
            url="http://example.com:8000/listen",
            output_dir=tmp_path,
            timeout=5,
            retry_delay=0,
        )

        call_idx = 0

        def fake_read_chunk():
            nonlocal call_idx
            if call_idx >= len(chunks):
                recorder.stop()
                raise ConnectionError("end of test data")
            chunk = chunks[call_idx]
            call_idx += 1
            return chunk

        mock_stream = MagicMock()
        mock_stream.info = StreamInfo(content_type="audio/ogg", metaint=8192)
        mock_stream.read_chunk = fake_read_chunk

        with patch("radio_record.recorder.ICYStream", return_value=mock_stream):
            recorder._running = True
            try:
                recorder._record_session()
            except ConnectionError:
                pass

        return recorder

    def test_single_complete_track(self, tmp_path):
        chunks = [
            # First metadata: "Song A" — starts recording (first track = partial)
            (b"audio0", _make_metadata("Song A")),
            # Normal audio for Song A
            (b"audio1", None),
            (b"audio2", None),
            # Transition to Song B — audio3 belongs to Song A
            (b"audio3", _make_metadata("Song B")),
            # Audio for Song B
            (b"audio4", None),
        ]
        recorder = self._run_session(tmp_path, chunks)

        # Song A should exist (first track = partial)
        song_a = tmp_path / "Song A.ogg"
        assert song_a.exists()
        # Song A should contain audio1 + audio2 + audio3 (transition chunk)
        # audio0 is discarded (pre-first-track, see recorder logic)
        assert song_a.read_bytes() == b"audio1audio2audio3"

    def test_first_track_is_partial(self, tmp_path):
        import json

        chunks = [
            (b"audio0", _make_metadata("Song A")),
            (b"audio1", None),
            (b"audio2", _make_metadata("Song B")),
            (b"audio3", None),
        ]
        self._run_session(tmp_path, chunks)

        meta_a = tmp_path / ".track_metadata" / "Song A.json"
        assert meta_a.exists()
        assert json.loads(meta_a.read_text())["is_partial"] is True

    def test_second_track_is_complete(self, tmp_path):
        import json

        chunks = [
            (b"a0", _make_metadata("Song A")),
            (b"a1", None),
            (b"a2", _make_metadata("Song B")),
            (b"b1", None),
            (b"b2", _make_metadata("Song C")),
            (b"c1", None),
        ]
        self._run_session(tmp_path, chunks)

        meta_b = tmp_path / ".track_metadata" / "Song B.json"
        assert meta_b.exists()
        assert json.loads(meta_b.read_text())["is_partial"] is False

    def test_transition_chunk_goes_to_old_track(self, tmp_path):
        chunks = [
            (b"x", _make_metadata("Song A")),
            (b"body_a", None),
            # transition_chunk belongs to Song A
            (b"transition_chunk", _make_metadata("Song B")),
            (b"body_b", None),
            (b"end_chunk", _make_metadata("Song C")),
        ]
        self._run_session(tmp_path, chunks)

        song_a = tmp_path / "Song A.ogg"
        assert b"transition_chunk" in song_a.read_bytes()

        song_b = tmp_path / "Song B.ogg"
        song_b_data = song_b.read_bytes()
        assert b"transition_chunk" not in song_b_data
        assert b"body_b" in song_b_data
        assert b"end_chunk" in song_b_data

    def test_last_track_saved_as_partial_on_disconnect(self, tmp_path):
        import json

        chunks = [
            (b"a0", _make_metadata("Song A")),
            (b"a1", None),
            (b"a2", _make_metadata("Song B")),
            (b"b1", None),
            # Connection drops — Song B is in progress
        ]
        self._run_session(tmp_path, chunks)

        song_b = tmp_path / "Song B.ogg"
        assert song_b.exists()

        meta_b = tmp_path / ".track_metadata" / "Song B.json"
        assert json.loads(meta_b.read_text())["is_partial"] is True

    def test_no_title_before_first_metadata(self, tmp_path):
        """Audio received before any metadata should be discarded."""
        chunks = [
            (b"pre_meta_audio", None),
            (b"more_pre_meta", None),
            (b"first_titled", _make_metadata("Song A")),
            (b"body", None),
        ]
        self._run_session(tmp_path, chunks)

        # No file should contain pre-metadata audio
        song_a = tmp_path / "Song A.ogg"
        assert song_a.exists()
        assert b"pre_meta_audio" not in song_a.read_bytes()
        assert b"more_pre_meta" not in song_a.read_bytes()

    def test_repeated_same_title_no_new_file(self, tmp_path):
        """Metadata with the same title should not trigger a new track."""
        chunks = [
            (b"a0", _make_metadata("Song A")),
            (b"a1", _make_metadata("Song A")),  # same title repeated
            (b"a2", _make_metadata("Song A")),  # still the same
            (b"a3", None),
            (b"a4", _make_metadata("Song B")),
        ]
        self._run_session(tmp_path, chunks)

        song_a = tmp_path / "Song A.ogg"
        # All audio (except a0) should be in Song A
        assert song_a.read_bytes() == b"a1a2a3a4"


class TestRecorderRunReconnect:
    def test_reconnects_on_connection_error(self, tmp_path):
        recorder = Recorder(
            url="http://example.com:8000/listen",
            output_dir=tmp_path,
            timeout=5,
            retry_delay=0,
        )

        call_count = 0

        def fake_record_session():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("test disconnect")
            recorder.stop()

        recorder._record_session = fake_record_session
        recorder._wait_retry = MagicMock()
        recorder.run()

        assert call_count == 3
        assert recorder._wait_retry.call_count == 2

    def test_reconnects_on_unexpected_error(self, tmp_path):
        recorder = Recorder(
            url="http://example.com:8000/listen",
            output_dir=tmp_path,
            timeout=5,
            retry_delay=0,
        )

        call_count = 0

        def fake_record_session():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("unexpected")
            recorder.stop()

        recorder._record_session = fake_record_session
        recorder._wait_retry = MagicMock()
        recorder.run()

        assert call_count == 2


class TestRecorderStop:
    def test_stop_sets_event(self, tmp_path):
        recorder = Recorder(
            url="http://example.com:8000/listen",
            output_dir=tmp_path,
        )
        assert not recorder._stop_event.is_set()
        recorder.stop()
        assert recorder._stop_event.is_set()
        assert not recorder._running
