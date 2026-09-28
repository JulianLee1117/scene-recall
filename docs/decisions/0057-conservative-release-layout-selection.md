# ADR-0057: Conservative release layout and feature selection

- Status: Accepted
- Date: 2026-09-14
- Supersedes: ADR-0047's automatic feature-selection and release-layout rules only
- Amends: ADR-0056's nested and single-video managed-root subtitle association
- Superseded by: None

## Context

The managed workflow must handle ordinary variations in release folders without
mistaking a fragment or another film for the requested feature. The previous
largest-file rule could choose an unequal CD1/CD2 fragment or a larger member
of a collection. Looking only at the video basename also missed identity in an
enclosing film folder. Generic English subtitles inside a wrapped release or
beside a single flat video could be missed despite clear local association.

These failures require stricter selection and better bounded association, not
an archive extractor, a disc reader or a second ingestion pipeline. File names,
metadata and sampled decoding remain evidence with limits, not proof of film
identity or whole-film completeness.

## Decision

Automatically choose a feature only when one non-extra supported video clearly
matches the requested title and compatible year evidence. Use the explicit
filename identity first; a generic filename can use a matching enclosing film
folder. A differently named film cannot borrow a collection folder's identity;
conflicting years in enclosing folders also require review. Compare nonempty
Unicode title tokens so different non-Latin titles cannot collapse into the
same empty match. Never use file size or a two-to-one size advantage to choose
between collection members.

Ambiguous collection members, unclear identities and additional CD, disc, part
or episode markers require explicit review of the complete main film. A marker
already present in the requested film title does not by itself imply a split
release. Remove the requested title from extra classification before checking
sample, trailer, interview and similar descriptors, so legitimate titles such
as *The Interview* do not become extras merely through their names. Review does
not join files or establish that a selected fragment is a complete film.

Accept MKV, MP4, AVI, MOV, M4V and WebM containers at the acquisition root or
inside nested folders. Require complete, size-matching files throughout the
bounded qBittorrent inventory and a stopped client before validation and import.
Keep existing ownership, safe-path, reparse, collision, fingerprint, bounded
media-check and frozen-import-plan guards. Unknown or partially downloaded
files cannot bypass those checks through a selected video alone.

Do not unpack compressed archives, mount disc images, ingest DVD/Blu-ray folder
structures or assemble multipart films automatically. If no supported video is
present, fail with an actionable format message and retain every downloaded
file. The operator can choose a standalone video release or prepare a supported
video through manual intake. Unsupported extras are preserved as evidence;
they are not executed or converted as a side effect of import.

Keep ADR-0056's subtitle validation and exclusions. Generic English SRT labels
may associate through a matching film ancestor inside outer wrapper folders,
but only within that film's subtree. A managed flat root containing exactly one
video may also associate a generic track such as `Subs/ENG.srt`. Do not infer
this relationship for a shared manual incoming root or an ambiguous managed
root with multiple videos. Explicitly associated subtitle names remain eligible
under the existing rules. External non-SRT tracks stay preserved but are not
automatically converted by intake.

## Consequences

- Ordinary nested releases and single-video managed roots can retain useful
  subtitles without relaxing ownership or crossing film subtrees.
- Size no longer silently substitutes a different collection member or a
  fragment for the requested film.
- Unusual naming and ambiguous releases can need review even when a person
  could recognize the film. The system does not invent missing identity.
- Archive, disc and multipart conversion remain explicit preparation work;
  failure or review preserves the original release.
- Existing inference, canonical source, publication and intact evidence-archive
  boundaries remain unchanged.
