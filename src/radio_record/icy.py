"""ICY (Icecast) protocol client.

The ICY protocol extends HTTP to deliver audio streams with interleaved metadata.
The client requests metadata by sending the ``Icy-MetaData: 1`` header.  The server
responds with an ``icy-metaint`` header indicating how many bytes of audio data
appear between each metadata block.

Stream layout (repeating)::

    [audio_data: metaint bytes] [meta_len: 1 byte] [metadata: meta_len*16 bytes]

Track-boundary semantics:
    When ``StreamTitle`` changes in a metadata block, the audio data read
    *before* that metadata block belongs to the **previous** track.  The audio
    data read *after* belongs to the **new** track.  The server inserts the
    metadata update at the point in the byte-stream where the transition occurs,
    so honouring this boundary is the key to capturing complete track endings
    and clean track beginnings.
"""

import logging
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class StreamInfo:
    """Static information about the connected stream."""

    content_type: str = "audio/mpeg"
    metaint: int = 0
    name: str = ""
    bitrate: int = 0
    headers: dict = field(default_factory=dict)


@dataclass
class StreamMetadata:
    """Parsed metadata from a single ICY metadata block."""

    stream_title: str | None = None

    @classmethod
    def parse(cls, raw: bytes) -> "StreamMetadata":
        """Parse a raw ICY metadata block.

        Format: ``StreamTitle='Artist - Title';StreamUrl='...';``
        The block is null-padded to a multiple of 16 bytes.
        """
        text = raw.decode("utf-8", errors="replace").rstrip("\x00")
        title = None
        match = re.search(r"StreamTitle='(.*?)';", text)
        if match:
            title = match.group(1).strip()
            if not title:
                title = None
        return cls(stream_title=title)


class ICYStream:
    """Low-level ICY stream reader using raw sockets.

    Uses raw sockets instead of ``requests`` / ``urllib3`` so that servers
    responding with ``ICY 200 OK`` (Shoutcast-style) instead of
    ``HTTP/1.x 200 OK`` are handled transparently.
    """

    def __init__(self, url: str, timeout: int = 30):
        self.url = url
        self.timeout = timeout
        self.info = StreamInfo()
        self._sock: socket.socket | None = None
        self._buffer = b""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connect(self):
        """Connect to the ICY stream and read response headers.

        Raises ``ConnectionError`` if the connection fails or the server
        does not advertise ``icy-metaint`` (metadata support).
        """
        parsed = urlparse(self.url)
        host = parsed.hostname
        port = parsed.port or 80
        path = parsed.path or "/"

        logger.info("Connecting to %s:%d%s", host, port, path)

        self._sock = socket.create_connection((host, port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)
        self._buffer = b""

        request = (
            f"GET {path} HTTP/1.0\r\n"
            f"Host: {host}\r\n"
            f"Icy-MetaData: 1\r\n"
            f"Accept: */*\r\n"
            f"User-Agent: radio-record/1.0\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        self._sock.sendall(request.encode("utf-8"))

        header_bytes, self._buffer = self._read_until_headers_end()
        self._parse_headers(header_bytes)

        if self.info.metaint == 0:
            raise ConnectionError(
                "Stream does not support ICY metadata (no icy-metaint header). "
                "Cannot detect track boundaries without metadata."
            )

        logger.info(
            "Connected: name=%s, type=%s, bitrate=%skbps, metaint=%d",
            self.info.name,
            self.info.content_type,
            self.info.bitrate,
            self.info.metaint,
        )

    def read_chunk(self) -> tuple[bytes, StreamMetadata | None]:
        """Read one audio chunk and its following metadata block.

        Returns ``(audio_data, metadata)`` where *metadata* is a parsed
        ``StreamMetadata`` when the metadata block was non-empty, or
        ``None`` otherwise.

        **Important:** the *audio_data* was transmitted *before* the metadata
        block, so if the metadata indicates a new track, this audio still
        belongs to the **previous** track.
        """
        audio = self._read_exact(self.info.metaint)

        length_byte = self._read_exact(1)
        meta_length = length_byte[0] * 16

        metadata = None
        if meta_length > 0:
            meta_raw = self._read_exact(meta_length)
            metadata = StreamMetadata.parse(meta_raw)

        return audio, metadata

    def close(self):
        """Close the underlying socket."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._buffer = b""

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_until_headers_end(self) -> tuple[bytes, bytes]:
        """Read until the ``\\r\\n\\r\\n`` header terminator.

        Returns ``(header_bytes, leftover_body_bytes)``.
        """
        buf = b""
        while b"\r\n\r\n" not in buf:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                raise ConnectionError("Timeout reading response headers")
            if not chunk:
                raise ConnectionError("Connection closed while reading headers")
            buf += chunk

        idx = buf.index(b"\r\n\r\n") + 4
        return buf[:idx], buf[idx:]

    def _parse_headers(self, raw: bytes):
        """Parse the HTTP / ICY response header block."""
        text = raw.decode("utf-8", errors="replace")
        lines = text.strip().split("\r\n")

        status_line = lines[0]
        if "200" not in status_line:
            raise ConnectionError(f"Server returned: {status_line}")

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()

        self.info = StreamInfo(
            content_type=headers.get("content-type", "audio/mpeg"),
            metaint=int(headers.get("icy-metaint", 0)),
            name=headers.get("icy-name", ""),
            bitrate=int(headers.get("icy-br", 0)),
            headers=headers,
        )

    def _read_exact(self, n: int) -> bytes:
        """Read exactly *n* bytes from the socket, buffering as needed."""
        while len(self._buffer) < n:
            try:
                chunk = self._sock.recv(max(8192, n - len(self._buffer)))
            except socket.timeout:
                raise ConnectionError("Timeout reading stream data")
            if not chunk:
                raise ConnectionError("Stream ended unexpectedly")
            self._buffer += chunk

        result = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return result
