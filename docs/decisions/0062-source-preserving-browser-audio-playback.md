# ADR-0062: Source-preserving browser audio playback repair

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0001's replaceable derivations and ADR-0052's container-relative evidence timestamps
- Supersedes: None
- Superseded by: None

## Context

Suspiria's full-scene player displayed HEVC video but produced no audible audio,
although local decoding confirmed audible E-AC-3 tracks. The source's default
audio is Italian and a secondary English track is also present. The playback
failure does not justify replacing the source, changing its language preference
or rebuilding search evidence.

Converting a film inside a playback request would tie up the API and make seeks
depend on a long-running operation. Automatically switching the existing
`/video/{film_id}` URL from original bytes to MP4 bytes could also mix two
representations within an already-open player's range requests. The repair
needs a derived artifact and a stable choice for each playback session.

## Decision

Add the bounded `video-copy-aac-stereo-v1` playback profile for H.264/HEVC sources
whose selected default audio needs browser repair. Copy the video bitstream and
transcode that audio to stereo AAC in MP4. Preserve the source-default audio
language, including Suspiria's Italian default; do not select an English dub as
a side effect of compatibility repair. Already-supported audio and unsupported
video codecs keep original playback. This profile is not a universal browser
compatibility transcoder.

Keep the original film and every original track intact. Store the derived MP4
and a versioned manifest under its existing asset directory. Record source
identity, conversion profile and recipe, selected streams, FFmpeg version and
the artifact fingerprint. Validate source identity before and after preparation,
duration and container-relative decoded timestamp samples, and sampled video and
audio decoding before atomic publication. Preserve relative audio/video offsets
and the player-relative timeline used by shot evidence. Sampled decoding and
timestamp checks do not establish continuous synchronization. An incomplete
artifact or mismatched manifest is never served.

Normal ingestion attempts preparation after media extraction while already
holding its film operation lock. It is optional derived media: conversion or
validation failure is reported and original playback remains available while
search ingestion can finish. Preserve existing cancellation and changed-source
failure semantics. No GPU model, hosted call, new queue kind or worker role is
introduced. Use bounded CPU threads for local conversion.

Expose `python -m pipeline.ingest.playback <film_path>` to prepare an existing
film directly, serialized against operations on that film. This command does
not rerun annotation, embedding or publication and does not claim or interrupt
unrelated ingestion or editor jobs. Interrupted preparation can be repeated
from retained source evidence; a valid matching cache is reusable.

Keep unqualified `GET /video/{film_id}` as original-source streaming. Add
`GET /video/{film_id}/playback` as a read-only resolver that returns an original
URL or a prepared URL with a representation token. The full-scene modal resolves
once for the selected film before assigning its source, with a stale-response
guard. A prepared `GET /video/{film_id}?representation=<token>` validates the
token and cache before serving ranges. If it is no longer current, return HTTP
409 and require the player to reopen; never substitute another file under an
active representation. API playback requests do not start conversion or queue
background work. Existing Lab original-source URLs remain unchanged.

## Consequences

- Supported films can regain browser audio without video reencoding or changes
  to raw evidence, language preference, source identity or indexed timestamps.
- Prepared playback consumes additional derived storage and bounded CPU/I/O
  work. Its absence does not change search readiness or model activation.
- Representation pinning prevents a cache becoming available or being replaced
  from silently changing the bytes used by an open player.
- HEVC device support and untested codecs remain browser limitations; the
  profile addresses the demonstrated audio failure without adding a general
  transcoding service.
- Playback artifacts can be rebuilt independently without altering bookmarks,
  source-based Lab work or model/version-scoped search derivations.
