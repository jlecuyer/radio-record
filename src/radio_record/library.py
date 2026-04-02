"""Track library -- naming, deduplication, and file management.

Recorded tracks are stored as audio files with sanitized filenames derived
from the ICY ``StreamTitle``.  A hidden ``.track_metadata/`` directory holds
one JSON sidecar per track that records whether the capture was *partial*
(joined mid-song or interrupted) or *complete* (both start and end boundaries
were observed).

Replacement rules (applied when a track with the same title already exists):

1. A **complete** recording always replaces a **partial** one.
2. A **partial** recording never replaces a **complete** one.
3. When completeness is the same, the **larger** file (by byte size) wins.
4. Otherwise the existing file is kept.

This ensures that the best available version of every track is retained,
partial captures are upgraded automatically when the song plays again, and
duplicates are suppressed.
"""

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CONTENT_TYPE_EXTENSIONS: dict[str, str] = {
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/aac": ".aac",
    "audio/aacp": ".aac",
    "audio/flac": ".flac",
}


def sanitize_filename(name: str) -> str:
    """Convert a track title into a filesystem-safe filename.

    Strips characters that are illegal or problematic on Windows/Linux/macOS,
    collapses runs of whitespace, and truncates to 200 characters.
    """
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    safe = re.sub(r"_+", "_", safe)
    safe = re.sub(r"\s+", " ", safe).strip(". ")
    return safe[:200] if safe else "unknown"


def extension_for_content_type(content_type: str) -> str:
    """Map an audio content-type to a file extension (with leading dot)."""
    base = content_type.split(";")[0].strip().lower()
    return CONTENT_TYPE_EXTENSIONS.get(base, ".mp3")


class TrackLibrary:
    """Manages the on-disk collection of recorded tracks."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self._meta_dir = base_dir / ".track_metadata"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._meta_dir.mkdir(exist_ok=True)

        # Session counters for the status line
        self.stats = {"new": 0, "replaced": 0, "skipped": 0}

    def ingest(
        self,
        title: str,
        temp_path: Path,
        content_type: str,
        is_partial: bool,
    ) -> Path | None:
        """Ingest a freshly recorded track into the library.

        Args:
            title:        Track title from the stream metadata.
            temp_path:    Path to the temporary recording file.
            content_type: MIME type of the audio (e.g. ``audio/mpeg``).
            is_partial:   ``True`` when the recording may be incomplete
                          (first track after connect, or recording was
                          interrupted before the next track started).

        Returns:
            The final path if the track was saved, or ``None`` if it was
            discarded (duplicate / smaller / partial replacing complete).
        """
        if not title:
            logger.warning("Discarding track with empty title")
            temp_path.unlink(missing_ok=True)
            return None

        new_size = temp_path.stat().st_size
        if new_size == 0:
            logger.warning("Discarding zero-length track: %s", title)
            temp_path.unlink(missing_ok=True)
            return None

        ext = extension_for_content_type(content_type)
        safe_name = sanitize_filename(title)
        target = self.base_dir / f"{safe_name}{ext}"
        meta_file = self._meta_dir / f"{safe_name}.json"

        # -- Decide whether to keep or replace --
        if target.exists():
            existing_meta = self._read_meta(meta_file)
            existing_size = target.stat().st_size
            existing_partial = existing_meta.get("is_partial", True)

            action = self._decide(existing_size, existing_partial, new_size, is_partial)

            if action == "keep_existing":
                reason = "complete>partial" if (not existing_partial and is_partial) else "larger/equal"
                logger.info(
                    "Skipped (%s): %s  [existing %dB vs new %dB]",
                    reason, title, existing_size, new_size,
                )
                temp_path.unlink(missing_ok=True)
                self.stats["skipped"] += 1
                return None

            logger.info(
                "Replacing: %s  [%dB %s -> %dB %s]",
                title,
                existing_size, "partial" if existing_partial else "complete",
                new_size, "partial" if is_partial else "complete",
            )
            self.stats["replaced"] += 1
        else:
            label = "partial" if is_partial else "complete"
            logger.info("New track (%s): %s  [%dB]", label, title, new_size)
            self.stats["new"] += 1

        # -- Persist --
        shutil.move(str(temp_path), str(target))

        self._write_meta(meta_file, {
            "title": title,
            "is_partial": is_partial,
            "size": new_size,
            "content_type": content_type,
            "recorded_at": datetime.now().isoformat(),
        })

        if ext == ".mp3":
            self._tag_mp3(target, title)

        return target

    # ------------------------------------------------------------------
    # Replacement logic
    # ------------------------------------------------------------------

    @staticmethod
    def _decide(
        existing_size: int,
        existing_partial: bool,
        new_size: int,
        new_partial: bool,
    ) -> str:
        """Return ``"replace"`` or ``"keep_existing"``."""
        # Complete always beats partial
        if existing_partial and not new_partial:
            return "replace"
        if not existing_partial and new_partial:
            return "keep_existing"
        # Same completeness: keep larger
        if new_size > existing_size:
            return "replace"
        return "keep_existing"

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------

    def _read_meta(self, path: Path) -> dict:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _write_meta(path: Path, data: dict):
        path.write_text(json.dumps(data, indent=2) + "\n")

    # ------------------------------------------------------------------
    # ID3 tagging (best-effort; requires mutagen)
    # ------------------------------------------------------------------

    @staticmethod
    def _tag_mp3(path: Path, title: str):
        """Write ID3v2 tags to an MP3 file.  Silently skipped if *mutagen*
        is not installed."""
        try:
            from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1
        except ImportError:
            return

        artist, track_title = None, title
        if " - " in title:
            artist, track_title = title.split(" - ", 1)
            artist, track_title = artist.strip(), track_title.strip()

        try:
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.add(TIT2(encoding=3, text=[track_title]))
            if artist:
                tags.add(TPE1(encoding=3, text=[artist]))
            tags.save(path)
        except Exception as exc:
            logger.debug("Could not write ID3 tags to %s: %s", path, exc)
