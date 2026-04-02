"""Tests for the ICY protocol client."""

import socket
from unittest.mock import MagicMock, patch

import pytest

from radio_record.icy import ICYStream, StreamInfo, StreamMetadata


# ---- StreamMetadata.parse ----

class TestStreamMetadataParse:
    def test_normal_title(self):
        raw = b"StreamTitle='Artist - Song Title';\x00\x00\x00"
        meta = StreamMetadata.parse(raw)
        assert meta.stream_title == "Artist - Song Title"

    def test_empty_block(self):
        raw = b"\x00" * 16
        meta = StreamMetadata.parse(raw)
        assert meta.stream_title is None

    def test_empty_title(self):
        raw = b"StreamTitle='';\x00\x00"
        meta = StreamMetadata.parse(raw)
        assert meta.stream_title is None

    def test_whitespace_only_title(self):
        raw = b"StreamTitle='   ';\x00"
        meta = StreamMetadata.parse(raw)
        assert meta.stream_title is None

    def test_title_with_url(self):
        raw = b"StreamTitle='My Song';StreamUrl='http://example.com';\x00"
        meta = StreamMetadata.parse(raw)
        assert meta.stream_title == "My Song"

    def test_title_with_special_chars(self):
        raw = b"StreamTitle='Caf\xc3\xa9 del Mar - Sunset Mix';\x00"
        meta = StreamMetadata.parse(raw)
        assert meta.stream_title == "Café del Mar - Sunset Mix"

    def test_no_stream_title_key(self):
        raw = b"SomeOtherKey='value';\x00\x00"
        meta = StreamMetadata.parse(raw)
        assert meta.stream_title is None

    def test_null_padded_block(self):
        raw = b"StreamTitle='Test';" + b"\x00" * 50
        meta = StreamMetadata.parse(raw)
        assert meta.stream_title == "Test"


# ---- ICYStream._parse_headers ----

class TestICYStreamParseHeaders:
    def _make_stream(self):
        return ICYStream("http://example.com:8000/listen")

    def test_standard_icy_headers(self):
        stream = self._make_stream()
        raw = (
            b"ICY 200 OK\r\n"
            b"content-type: audio/mpeg\r\n"
            b"icy-metaint: 16000\r\n"
            b"icy-name: Test Radio\r\n"
            b"icy-br: 128\r\n"
            b"\r\n"
        )
        stream._parse_headers(raw)
        assert stream.info.content_type == "audio/mpeg"
        assert stream.info.metaint == 16000
        assert stream.info.name == "Test Radio"
        assert stream.info.bitrate == 128

    def test_http_style_headers(self):
        stream = self._make_stream()
        raw = (
            b"HTTP/1.0 200 OK\r\n"
            b"content-type: audio/ogg\r\n"
            b"icy-metaint: 8192\r\n"
            b"icy-name: OGG Stream\r\n"
            b"\r\n"
        )
        stream._parse_headers(raw)
        assert stream.info.content_type == "audio/ogg"
        assert stream.info.metaint == 8192

    def test_missing_metaint_defaults_to_zero(self):
        stream = self._make_stream()
        raw = (
            b"ICY 200 OK\r\n"
            b"content-type: audio/mpeg\r\n"
            b"\r\n"
        )
        stream._parse_headers(raw)
        assert stream.info.metaint == 0

    def test_non_200_raises(self):
        stream = self._make_stream()
        raw = b"ICY 404 Not Found\r\n\r\n"
        with pytest.raises(ConnectionError, match="404"):
            stream._parse_headers(raw)

    def test_case_insensitive_header_keys(self):
        stream = self._make_stream()
        raw = (
            b"ICY 200 OK\r\n"
            b"Content-Type: audio/aac\r\n"
            b"Icy-MetaInt: 4096\r\n"
            b"Icy-Name: AAC Stream\r\n"
            b"Icy-BR: 256\r\n"
            b"\r\n"
        )
        stream._parse_headers(raw)
        assert stream.info.content_type == "audio/aac"
        assert stream.info.metaint == 4096
        assert stream.info.bitrate == 256


# ---- ICYStream._read_exact ----

class TestICYStreamReadExact:
    def test_read_from_buffer(self):
        stream = ICYStream("http://example.com:8000/listen")
        stream._buffer = b"hello world"
        stream._sock = MagicMock()
        result = stream._read_exact(5)
        assert result == b"hello"
        assert stream._buffer == b" world"
        stream._sock.recv.assert_not_called()

    def test_read_requires_socket(self):
        stream = ICYStream("http://example.com:8000/listen")
        stream._buffer = b"he"
        mock_sock = MagicMock()
        mock_sock.recv.return_value = b"llo world"
        stream._sock = mock_sock
        result = stream._read_exact(5)
        assert result == b"hello"
        assert stream._buffer == b" world"

    def test_read_timeout_raises(self):
        stream = ICYStream("http://example.com:8000/listen")
        stream._buffer = b""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = socket.timeout("timed out")
        stream._sock = mock_sock
        with pytest.raises(ConnectionError, match="Timeout"):
            stream._read_exact(10)

    def test_read_connection_closed_raises(self):
        stream = ICYStream("http://example.com:8000/listen")
        stream._buffer = b""
        mock_sock = MagicMock()
        mock_sock.recv.return_value = b""
        stream._sock = mock_sock
        with pytest.raises(ConnectionError, match="ended unexpectedly"):
            stream._read_exact(10)


# ---- ICYStream.read_chunk ----

class TestICYStreamReadChunk:
    def test_read_chunk_with_metadata(self):
        stream = ICYStream("http://example.com:8000/listen")
        stream.info = StreamInfo(metaint=8)

        title_raw = b"StreamTitle='Song';\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        # Pad to exactly 32 bytes (meta_length_byte=2, so 2*16=32 bytes)
        title_raw = title_raw.ljust(32, b"\x00")

        # audio (8 bytes) + length_byte (2 = 32 bytes of meta) + metadata (32 bytes)
        stream._buffer = b"AAAABBBB" + bytes([2]) + title_raw
        stream._sock = MagicMock()

        audio, metadata = stream.read_chunk()
        assert audio == b"AAAABBBB"
        assert metadata is not None
        assert metadata.stream_title == "Song"

    def test_read_chunk_no_metadata(self):
        stream = ICYStream("http://example.com:8000/listen")
        stream.info = StreamInfo(metaint=4)

        # audio (4 bytes) + length_byte (0 = no metadata)
        stream._buffer = b"ABCD" + bytes([0])
        stream._sock = MagicMock()

        audio, metadata = stream.read_chunk()
        assert audio == b"ABCD"
        assert metadata is None


# ---- ICYStream.connect ----

class TestICYStreamConnect:
    def test_connect_no_metaint_raises(self):
        stream = ICYStream("http://example.com:8000/listen")

        headers = (
            b"ICY 200 OK\r\n"
            b"content-type: audio/mpeg\r\n"
            b"\r\n"
        )

        mock_sock = MagicMock()
        mock_sock.recv.return_value = headers

        with patch("radio_record.icy.socket.create_connection", return_value=mock_sock):
            with pytest.raises(ConnectionError, match="does not support ICY metadata"):
                stream.connect()

    def test_connect_success(self):
        stream = ICYStream("http://example.com:8000/listen")

        headers = (
            b"ICY 200 OK\r\n"
            b"content-type: audio/mpeg\r\n"
            b"icy-metaint: 16000\r\n"
            b"icy-name: Test\r\n"
            b"\r\n"
        )

        mock_sock = MagicMock()
        mock_sock.recv.return_value = headers

        with patch("radio_record.icy.socket.create_connection", return_value=mock_sock):
            stream.connect()

        assert stream.info.metaint == 16000
        assert stream.info.name == "Test"
        mock_sock.sendall.assert_called_once()


# ---- ICYStream.close ----

class TestICYStreamClose:
    def test_close_with_socket(self):
        stream = ICYStream("http://example.com:8000/listen")
        stream._sock = MagicMock()
        stream._buffer = b"leftover"
        stream.close()
        assert stream._sock is None
        assert stream._buffer == b""

    def test_close_without_socket(self):
        stream = ICYStream("http://example.com:8000/listen")
        stream._sock = None
        stream.close()  # should not raise

    def test_close_socket_oserror(self):
        stream = ICYStream("http://example.com:8000/listen")
        mock_sock = MagicMock()
        mock_sock.close.side_effect = OSError("already closed")
        stream._sock = mock_sock
        stream.close()  # should not raise
        assert stream._sock is None


# ---- ICYStream._read_until_headers_end ----

class TestICYStreamReadUntilHeaders:
    def test_headers_in_single_recv(self):
        stream = ICYStream("http://example.com:8000/listen")
        mock_sock = MagicMock()
        mock_sock.recv.return_value = b"ICY 200 OK\r\n\r\nleftover audio"
        stream._sock = mock_sock

        headers, leftover = stream._read_until_headers_end()
        assert headers == b"ICY 200 OK\r\n\r\n"
        assert leftover == b"leftover audio"

    def test_headers_across_multiple_recvs(self):
        stream = ICYStream("http://example.com:8000/listen")
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [
            b"ICY 200 OK\r\n",
            b"icy-metaint: 8192\r\n\r\naudiodata",
        ]
        stream._sock = mock_sock

        headers, leftover = stream._read_until_headers_end()
        assert b"icy-metaint: 8192" in headers
        assert leftover == b"audiodata"

    def test_timeout_raises(self):
        stream = ICYStream("http://example.com:8000/listen")
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = socket.timeout("timed out")
        stream._sock = mock_sock

        with pytest.raises(ConnectionError, match="Timeout"):
            stream._read_until_headers_end()

    def test_connection_closed_raises(self):
        stream = ICYStream("http://example.com:8000/listen")
        mock_sock = MagicMock()
        mock_sock.recv.return_value = b""
        stream._sock = mock_sock

        with pytest.raises(ConnectionError, match="closed"):
            stream._read_until_headers_end()
