# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

radio-record connects to Icecast internet radio streams, detects track boundaries via ICY metadata, and saves each song as a separate audio file with ID3 tags. It uses raw sockets (no HTTP library) to handle both HTTP and Shoutcast-style ICY responses.

## Commands

```bash
# Install dependencies
uv sync

# Run (default stream: http://192.168.1.50:8000/metal)
uv run radio-record

# Run with options
uv run radio-record http://example.com:8000/stream -o ~/Music/output -v

# Run as module
uv run python -m radio_record
```

There are no tests or linting configured yet.

## Architecture

The package lives in `src/radio_record/` with four modules forming a clear pipeline:

- **`__main__.py`** — CLI entry point. Parses args, sets up signal handlers for graceful Ctrl+C shutdown, creates and runs the `Recorder`.
- **`icy.py`** — Low-level ICY protocol client using raw sockets. `ICYStream.read_chunk()` returns `(audio_data, metadata)` pairs. The audio in each pair was transmitted *before* the metadata, so on a title change it belongs to the **previous** track.
- **`recorder.py`** — `Recorder` orchestrates the recording session with auto-reconnection. On a title change: writes the transition audio chunk to the *old* track's file (preserving endings), closes it, opens a new temp file, and does *not* write the transition chunk to the new file (preventing leaked beginnings).
- **`library.py`** — `TrackLibrary` handles filename sanitization, deduplication/replacement logic, JSON sidecar metadata in `.track_metadata/`, and MP3 ID3 tagging via mutagen.

### Key design decision: track-boundary handling

The transition audio chunk (read just before a metadata change) is written to the outgoing track and excluded from the incoming track. This is the most subtle part of the codebase — any change to `_record_session()` in `recorder.py` must preserve this invariant.

### Deduplication rules

Complete recordings replace partial ones. Partial never replaces complete. When completeness is equal, larger file wins. This is in `TrackLibrary._decide()`.

## Dependencies

- Python >= 3.11
- `mutagen` (ID3 tagging)
- No HTTP libraries — raw sockets for ICY protocol compatibility

## Environment

- `RADIO_RECORD_URL` — optional env var for default stream URL
- Default output directory is derived from the stream URL (e.g. `./192.168.1.50-metal` for `http://192.168.1.50:8000/metal`)
