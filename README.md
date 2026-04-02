# radio-record

Record and organise individual tracks from Icecast internet radio streams.

Connects to a stream, detects track boundaries via ICY metadata, and saves
each song as a separate audio file.  Handles partial recordings, deduplication,
and automatic reconnection.

## Quick start

```bash
# Install dependencies and run (Radio Hyrule is the default stream)
uv sync
uv run radio-record

# Custom stream and output directory
uv run radio-record http://listen.radiohyrule.com:8000/listen -o ~/Music/radio-hyrule

# Verbose logging
uv run radio-record -v
```

Tracks are saved to `./tracks/` by default.  Press **Ctrl+C** to stop
recording; the current (partial) track is saved before exit.

## How it works

### ICY metadata protocol

Icecast servers interleave metadata blocks in the audio stream at a fixed byte
interval (`icy-metaint`).  Each metadata block contains the current
`StreamTitle`.  When the title changes, the recorder knows a new track has
started.

### Track-boundary handling

This is the trickiest part and the most common source of bugs in similar tools.

The audio data in the stream is laid out like this:

```
[audio chunk A] [metadata: "Song 1"] [audio chunk B] [metadata: "Song 2"] [audio chunk C] ...
```

When the metadata changes from "Song 1" to "Song 2":

- **Audio chunk B** was transmitted *before* the new metadata — it still belongs
  to **Song 1**.  The recorder writes it to Song 1's file so the ending is
  preserved.
- **Audio chunk C** is the first data that belongs to **Song 2**.  The recorder
  starts a new file and writes chunk C there.
- Audio chunk B is **not** written to Song 2's file, preventing the "previous
  track's tail leaks into next track's head" bug.

This approach ensures:
- **No missing endings**: the transition chunk is included in the outgoing track.
- **No leaked beginnings**: the transition chunk is excluded from the incoming
  track.

### Partial vs complete recordings

| Scenario | Marked as |
|---|---|
| First track after connecting (joined mid-song) | **partial** |
| Recording interrupted by disconnect / Ctrl+C | **partial** |
| Track where both start and end boundaries were observed | **complete** |

### Deduplication and replacement

When a track with the same title already exists in the library:

| Existing | New | Decision |
|---|---|---|
| partial | complete | **Replace** (complete is always better) |
| complete | partial | **Keep existing** (never downgrade) |
| partial | partial (larger) | **Replace** (larger = more captured) |
| complete | complete (larger) | **Replace** (larger = better capture) |
| any | same/smaller, same completeness | **Keep existing** |

This means you can leave the recorder running and every partial capture will
eventually be upgraded to a complete one when the song plays again.

### ID3 tagging

If the stream sends titles in `Artist - Title` format (common for internet
radio), the recorder writes ID3v2 tags with the artist and title split
correctly.  This uses the `mutagen` library.

## CLI reference

```
usage: radio-record [-h] [-o OUTPUT] [-v] [--timeout TIMEOUT]
                    [--retry-delay RETRY_DELAY]
                    [url]

positional arguments:
  url                   Stream URL (default: Radio Hyrule)

options:
  -h, --help            show this help message and exit
  -o, --output OUTPUT   Output directory (default: ./tracks)
  -v, --verbose         Debug-level logging
  --timeout TIMEOUT     Connection/read timeout in seconds (default: 30)
  --retry-delay DELAY   Reconnect delay in seconds (default: 10)
```

## Project structure

```
src/radio_record/
├── __init__.py      # Package version
├── __main__.py      # CLI entry point and argument parsing
├── icy.py           # ICY protocol: socket connection, header parsing,
│                    #   audio/metadata demuxing
├── recorder.py      # Recording orchestration: track boundary detection,
│                    #   temp file management, reconnection loop
└── library.py       # Track library: filename sanitisation, dedup/replacement
                     #   logic, ID3 tagging
```

## Output structure

```
tracks/
├── .track_metadata/          # JSON sidecar files (partial/complete status)
│   ├── Artist - Song.json
│   └── ...
├── Artist - Song.mp3         # Recorded tracks
├── Another Artist - Title.mp3
└── ...
```

## Requirements

- Python >= 3.11
- [`mutagen`](https://mutagen.readthedocs.io/) (for ID3 tagging)
- No other dependencies — the ICY client uses raw sockets from the standard
  library for maximum compatibility with both HTTP and ICY server responses.
