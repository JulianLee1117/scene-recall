# Managed acquisition integration — 2026-09-14

Implemented under [ADR-0047](../decisions/0047-managed-film-acquisition.md).
README and the architecture contract describe the current behavior; this file
records integration evidence rather than a future backlog.

Films now has search, magnet and torrent entry, a durable progress queue,
explicit video/subtitle review, cancel and retry. The CLI uses the same API.
The separate acquisition monitor verifies and imports owned downloads, then
queues the existing ingestion worker. Shared manual-intake rules avoid a second
implementation of naming and subtitle selection. Completed release evidence is
archived intact only after publication, with restart recovery and no overwrite
or torrent-client file deletion.

## Local setup

- qBittorrent 5.2.3 and Prowlarr 2.5.2.5491 portable official distributions were
  checked against their published SHA256 hashes. Their executables are not
  Authenticode signed; no signature verification is claimed.
- Binaries reside under `%LOCALAPPDATA%/SceneRecall/tools`. Isolated profiles
  are under `V:/scene-recall/state/acquisition/integrations`, restricted to the
  local user and SYSTEM. Generated secrets stay in `.env` and those profiles.
- Management interfaces bind to `127.0.0.1:8085` and `127.0.0.1:9696`. Local
  qBittorrent authentication remains enabled; external-program hooks and UPnP
  are disabled in its dedicated profile. Existing µTorrent was unchanged.
- Prowlarr's supported The Pirate Bay indexer was configured and its live search
  and magnet resolution verified. Scene Recall owns the downloader connection;
  no duplicate Prowlarr download-client integration was created.
- `scripts/start-acquisition-services.ps1` can restart the isolated helpers and
  is idempotent while they run. No scheduled startup task was installed.
- The independent acquisition monitor and API were started with the completed
  integration. The Lab worker was not restarted. Live API status showed all
  connections configured, qBittorrent available, monitor running and no queued
  acquisitions. The existing Mirror bookmark remained the sole saved scene.

## Validation

- Full backend integration suite: **1,595 passed, 1 skipped**. Later focused
  API/CLI/store/recovery/manual-intake checks: **141 passed, 1 skipped**;
  final client/source checks: **77 passed**, including the real qBittorrent
  5.2 empty-successful-login response compatibility fix.
- Final frontend suite: **150 passed**; TypeScript and isolated production
  webpack build passed without touching the live `.next` directory.
- Browser checks with synthetic queue responses covered search/magnet entry,
  reviews, subtitle decisions, cancellation, retries, history, keyboard focus
  restoration and a 390px viewport, with no console errors. The file chooser
  opened but browser-extension file access blocked fixture selection; multipart
  uploads were exercised through API and frontend tests.
- A real qBittorrent test used a locally generated four-second movie, SRT and
  provenance notes in a private trackerless torrent. The client verified all
  preloaded pieces; real ffprobe and three ffmpeg samples passed. The engine
  stopped and detached the torrent, moved the canonical source, copied its SRT,
  created one isolated ingestion job and archived the remaining evidence intact.
  Reopening the ledger preserved completion without duplicating the job.
- This smoke test lived entirely under `.tmp/acquisition-live-smoke`; no real
  movie was acquired, no hosted inference ran, and publication was simulated.
  Existing pipeline tests cover the actual ingestion/publication boundary.

Snapshots, process output and generated fixtures remain in ignored local
scratch/log directories. They are not runtime architecture inputs.

## Real user flow: Challengers (2024)

The user subsequently authorized testing the full workflow with this film while
their other films were ingesting. The real Films interface searched configured
releases, selected a 1080p release, entered the canonical title/year and queued
acquisition `5f393db8ba3e4a718721b689be3ab0ae`. Browser console checks showed no
errors or warnings, and progress advanced without a manual API submission.

Download, stopped-client verification, three real decoded samples, canonical
import and automatic ingestion enqueue passed. The source is
`V:/scene-recall/films/Challengers (2024).mkv`, 1,725,075,975 bytes, 1920×1040
H.264 with AC3 audio, duration 2h11m38s. Its original file fingerprint and content
hash match after moving. All 39 embedded subtitle streams, including full
English and English SDH, remain intact; this release had no external SRT.
The client torrent was detached, and two original text notices totaling 550
bytes were held in owned staging with the marker until successful publication.

Ingestion job `ec22fd5b-2aa9-417a-872e-2569892174ba` appended behind the existing
running job and six waiting films. The browser showed **Waiting to ingest** and
**Queue position 7**; the original order and worker remained intact.
Film ID: `9e6507d015eceb67e96bb80793d77bb5e3daf274298dad70b08bf936157d163a`.

**Completed end-to-end on 2026-09-14.** The existing FIFO advanced normally;
Challengers waited 8h24m and its ingestion ran for 1h19m30s. The job completed
without errors, publishing 1,660 shots, 4,020 frames and 6,387 text features.
The acquisition reached `ready` at 08:08:36 PDT, 10.6 seconds after ingestion
completed. Final checks observed:

- `/library` reports `indexed`; Films shows one **Ready to search** row and no
  active acquisition. The completed entry remains in collapsed History.
- A real browser search for **tennis match on a court**, scoped to Challengers,
  returned 48 ranked results with 12 initially displayed, all from this film.
  Two results, near 2:05:03 and 25:12, played the source video at the retrieved
  moments. Both showed advancing playback, decoded frames and no media error.
- The first result's preview decoded in the browser. Source, preview and
  keyframe endpoints also returned HTTP 206 with correct media types and bytes;
  the preview is 422,289 bytes and the checked WebP is 82,802 bytes. No browser
  warnings or errors appeared during this final verification.
- The canonical source still matches its original fingerprint and pipeline
  content hash. Its torrent is absent from qBittorrent. Managed staging is gone.
  `V:/scene-recall/evidence/managed-releases/5f393db8ba3e4a718721b689be3ab0ae`
  contains exactly the matching ownership marker and the original 71-byte and
  479-byte notices, with their original fingerprints preserved.
- The sole saved Mirror burning-farm scene remains indexed at 920.906 seconds,
  with its original bookmark ID. No extra acquisition, saved scene, retry or
  cancellation was created by the final verification.

The `verify-challengers-ingestion` follow-up is paused after completion.
Separately, the pre-existing Sinners ingest failed because an annotation
returned one mood keyword where validation requires two to four. That job was
left untouched; no hosted processing was automatically replayed.

## Films flow simplification

The follow-up UI pass reduced online intake to search → choose → confirm →
queue. Release hints fill editable title/year fields; optional edition and raw
release names are expandable. Success closes the form and focuses the queue;
failed submissions preserve the user's corrections. Explicit edits clear stale
submission errors, while polling preserves actionable errors.

Managed downloads and manual ingestion now share one progress view without
duplicate active Library rows. Actual waiting positions and plain preparation
stages replace exposed worker logs; logs remain in Details. Other waiting films,
completed/stopped history and connection instructions are expandable. Library
has a title filter. Refresh covers both downloads and existing ingestion jobs.
Slow health checks no longer hold up the queue, and failed catalog updates can
recover without requiring another state transition.

Validation: **170 frontend tests passed**, including queue ownership, editable
release hints, failed-add recovery, asynchronous refresh and two-stage subtitle
review. TypeScript passed. Live read-only browser inspection showed Challengers
once at position 7 and the original running job still progressing. No additional
real film was queued, and the newly arrived manual incoming file was untouched.
The isolated production build passed compilation, TypeScript and all eight
static pages. Production-browser checks with mock API data covered successful
add/focus transfer, duplicate-error recovery, both subtitle-review stages,
refreshing unchanged downloads alongside updated manual jobs, stopped-item
resume, completion moving to Library, and a 390px viewport without horizontal
overflow. The final live verification recorded no new browser warnings/errors.

## Completion stop and cleanup verification

The user's follow-up requested that completed torrents cannot remain seeding
from the PC. Inspection found that the monitor already stopped and detached
completed downloads, but their unlimited client sharing limits allowed seeding
to continue if the monitor was unavailable. ADR-0049 adds zero ratio/time limits
with an explicit Stop action on new adds, exact-owned duplicate reconciliation,
and before app-controlled starts. Global preferences and unrelated torrents
remain untouched.

On 2026-09-14, a new private trackerless torrent of 258,393 locally generated
bytes exercised the real qBittorrent 5.2.3 client. After one submission tick,
the isolated monitor was not ticked again until the client independently
reached `stoppedUP`, 3.047 seconds after submission. Its ratio and seeding-time
limits were zero, its action was Stop, and uploaded bytes and upload speed
were both zero. The global sharing preferences matched their initial values.

Resuming the isolated monitor then verified media, detached the torrent,
imported the exact film bytes and selected SRT, created one ingestion job, and
archived the remaining evidence intact. Staging was absent at `ready`, and a
reopened ledger did not duplicate the job. Inference and publication were
simulated only for this local fixture; no real film was queued by this test.
The report and fixture remain in ignored
`.tmp/acquisition-completion-stop-20260914`.

All **196 acquisition tests passed**, including API, CLI, client, engine,
ownership, media, source and store coverage. An additional focused run passed
all three unrelated-torrent cases after strengthening their no-mutation
assertion. The acquisition monitor was restarted to load the policy; the API
and active Lab worker were not restarted. The final live check found **zero
qBittorrent torrents and zero upload bytes/second**. Challengers remained
`ready` with staging cleared; Hereditary was already detached and continued
ingesting, with its final evidence archival waiting for publication.
