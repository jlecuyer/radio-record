"""Stream recording orchestration.

Connects to an ICY stream, detects track boundaries via metadata changes,
and writes each track to a separate file through the :class:`TrackLibrary`.

Track-boundary handling (the hard part)
---------------------------------------
The ICY protocol interleaves metadata between fixed-size audio chunks.
When ``StreamTitle`` changes in a metadata block:

* The audio chunk read **before** that metadata block belongs to the
  **old** (ending) track.
* The audio chunk read **after** that metadata block belongs to the
  **new** (starting) track.

This recorder therefore:

1. Writes the *current* audio chunk to the **old** track's temp file
   (capturing its final seconds).
2. Closes and finalises the old track.
3. Opens a new temp file for the incoming track.
4. Does **not** write the transition chunk to the new file -- the next
   ``read_chunk()`` call will deliver the first audio that genuinely belongs
   to the new track.

This avoids two classic bugs:
    - Losing the last few seconds of each track (by including the
      transition chunk in the old track).
    - Leaking the end of one track into the beginning of the next
      (by *not* including the transition chunk in the new track).

Partial-track tracking
----------------------
* The **first** track after a (re)connect is always marked *partial*:
  we joined mid-song.
* A track that is still recording when the connection drops or the user
  stops the recorder is marked *partial*.
* All other tracks -- where we observed both the metadata transition
  *into* and *out of* the song -- are marked *complete*.
"""

import logging
import tempfile
import threading
import time
from pathlib import Path

from .icy import ICYStream
from .library import TrackLibrary, extension_for_content_type

logger = logging.getLogger(__name__)


class Recorder:
    """Records individual tracks from an ICY internet radio stream."""

    def __init__(
        self,
        url: str,
        output_dir: Path,
        timeout: int = 30,
        retry_delay: int = 10,
    ):
        self.url = url
        self.output_dir = output_dir
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.library = TrackLibrary(output_dir)
        self._running = False
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self):
        """Main recording loop with automatic reconnection.

        Runs until :meth:`stop` is called.  Reconnects automatically
        after stream errors, waiting ``retry_delay`` seconds between
        attempts.
        """
        self._running = True
        self._stop_event.clear()

        while self._running:
            try:
                self._record_session()
            except ConnectionError as exc:
                if not self._running:
                    break
                logger.warning("Connection lost: %s", exc)
                self._wait_retry()
            except Exception as exc:
                if not self._running:
                    break
                logger.error("Unexpected error: %s", exc, exc_info=True)
                self._wait_retry()

        self._log_stats()
        logger.info("Recorder stopped.")

    def stop(self):
        """Signal the recorder to stop gracefully.

        The current audio chunk will be saved as a partial track before
        the recorder exits.
        """
        self._running = False
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Session loop
    # ------------------------------------------------------------------

    def _record_session(self):
        """Execute one connection lifecycle: connect, record, handle errors."""
        stream = ICYStream(self.url, timeout=self.timeout)
        stream.connect()

        ext = extension_for_content_type(stream.info.content_type)
        current_title: str | None = None
        current_file = None
        is_first_track = True

        try:
            while self._running:
                audio_data, metadata = stream.read_chunk()

                if metadata is not None and metadata.stream_title is not None:
                    new_title = metadata.stream_title

                    if new_title != current_title:
                        # ---- Track boundary ----

                        # 1) Flush this chunk to the OLD track.
                        #    This audio arrived before the metadata change,
                        #    so it contains the tail of the outgoing song.
                        if current_file is not None:
                            current_file.write(audio_data)
                            current_file.flush()
                            temp_path = Path(current_file.name)
                            current_file.close()
                            current_file = None

                            self.library.ingest(
                                title=current_title,
                                temp_path=temp_path,
                                content_type=stream.info.content_type,
                                is_partial=is_first_track,
                            )

                        # 2) Start a new recording.
                        current_title = new_title
                        is_first_track = False
                        current_file = tempfile.NamedTemporaryFile(
                            delete=False,
                            dir=str(self.output_dir),
                            suffix=ext,
                            prefix=".recording_",
                        )
                        logger.info("Now recording: %s", current_title)

                        # Do NOT write audio_data to the new file; it belongs
                        # to the old track (already written above) or is
                        # pre-first-track audio we intentionally discard.
                        continue

                # ---- Normal data: append to current track ----
                if current_file is not None:
                    current_file.write(audio_data)

        finally:
            # Save whatever we have as a partial track
            if current_file is not None:
                current_file.flush()
                temp_path = Path(current_file.name)
                current_file.close()

                if current_title:
                    logger.info("Saving partial track (disconnect): %s", current_title)
                    self.library.ingest(
                        title=current_title,
                        temp_path=temp_path,
                        content_type=stream.info.content_type,
                        is_partial=True,
                    )
                else:
                    temp_path.unlink(missing_ok=True)

            stream.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _wait_retry(self):
        """Wait before reconnecting, but wake up immediately on stop()."""
        logger.info("Reconnecting in %ds...", self.retry_delay)
        self._stop_event.wait(timeout=self.retry_delay)

    def _log_stats(self):
        s = self.library.stats
        logger.info(
            "Session stats: %d new, %d replaced, %d skipped",
            s["new"], s["replaced"], s["skipped"],
        )
