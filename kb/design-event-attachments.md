# Event image attachments — Telegram photos and GitHub inline images

Status: shipped on 2026-07-09.

## The gap

Zero telegram-photo ingestion existed anywhere in `src/brr` before this
page — confirmed twice, independently, before this shipped: once
2026-07-07 while trying to check a live screenshot the maintainer had
sent (`kb/plan-loom-realtime-build.md` §Slice 1.5), and again the same
day in `kb/log.md`. Both times the workaround was a fresh Playwright
screenshot instead of the maintainer's own picture. GitHub's side had
the mirror gap in a different shape: an issue/PR/comment body already
carries any pasted screenshot as a markdown image link
(`![desc](https://github.com/user-attachments/assets/<uuid>)`), but
that URL was never resolved to anything a sandboxed run could actually
open — no browser, no guaranteed general-purpose network-fetch tool.

Direct ask that triggered the build: "make you able to see pictures
(from both telegram and GitHub gates)... an event file can have a
directory with attached images that you are able to access (the event
file would have them attachments listed as references, or some such).
Agreed?" — confirmed, with the refinement that both gates converge on
one mechanism rather than inventing two.

## The shape

`protocol.create_event` gained an `attachment_files: list[Path] | None`
parameter. When given, each file (already downloaded by the calling
gate) is moved into a directory sibling to the event file itself —
`attachments_dir_for_event(inbox_dir, event_id)` →
`<inbox_dir>/<event_id>.attachments/` — and the event's frontmatter
records a comma-joined `attachments:` field of bare filenames (index-
prefixed only when more than one file needs disambiguating, e.g.
`00-a.png,01-b.png`). `protocol.event_attachment_paths(event)` resolves
that field back to real paths, deriving the directory from the event's
own `_path` rather than requiring a separate `inbox_dir` argument —
every event dict `_read_event` produces already carries it. Paths that
no longer exist on disk are filtered out rather than handed back
dangling.

Both gates are pure I/O outside `protocol.py`, which stays network-free
by design (see `protocol.py`'s own module docstring and
`gates/README.md`):

- **Telegram** (`gates/telegram.py`): `_pick_image_file_id` reads a
  `photo` (the largest `PhotoSize` in Telegram's ascending-resolution
  array — photos have no filename, Telegram always transcodes to JPEG)
  or an image-MIME `document` (keeps its own filename — drag-and-drop,
  or "compress: off" in the client). `_download_telegram_file` does the
  two-call dance (`getFile` → a plain GET against Telegram's separate
  file-serving host) into a temp file, best-effort: any failure (expired
  file, network hiccup, oversized response — capped at Telegram's own
  20MB bot-API limit) degrades to "no attachment," never drops the whole
  message. `_loop_once`'s old `if not text: continue` — which silently
  ate every bare-photo message — is now `if not text and not image:
  continue`; a caption (or lack of one) is the event body, the image is
  the attachment.
- **GitHub** (`gates/github/attachments.py` + `client._download_url`):
  `extract_image_urls` regexes markdown `![alt]\(url\)` and HTML
  `<img src="url">` out of a body (capped at 6 per event — a body
  pasting a dozen screenshots shouldn't turn one comment into a dozen
  downloads; the live `github_html_url` stays the escape hatch past the
  cap or for a shape the regex misses). `download_images` fetches each
  through `client._download_url`, which sends the bot token as
  `Authorization` on the first hop only — `requests` strips it
  automatically on any cross-host redirect, which is exactly what
  GitHub's attachment URL does (302 to a signed, time-limited object),
  so the token never reaches whatever host actually serves the bytes.
  All 7 of the gate's `protocol.create_event` call sites (opened items,
  label trigger, mention trigger + its two review-comment variants, the
  `any` trigger + its review-comment variant) now funnel through one
  `_create_github_event` wrapper in `polling.py` rather than duplicating
  the extract-download-attach dance seven times.

Both prompt surfaces render the result the same way: `prompts.py`'s live
Run Context Bundle and `run_context.py`'s persisted `context.md` both
gained an "Attachments (local image files — open them with Read):"
bullet list under "Original event body" / "Original Event Body" —
`prompts.py` needed a new `event_attachments` parameter threaded through
`build_daemon_prompt` (daemon.py computes it via
`protocol.event_attachment_paths(event)`, since the bundle-builder only
ever received a body string, not the full event dict); `run_context.py`
already held the full event dict, so it calls
`protocol.event_attachment_paths(event)` directly with no new plumbing.
Either surface renders the section on `body or attachment_paths` — a
bare photo with no caption (empty body, real attachment) still needs to
show something, not silently vanish because the old code only checked
`if body:`.

`protocol.cleanup` removes the attachments directory alongside the
event/response/partials files it already deleted, so a delivered
event's downloaded image doesn't outlive the event itself.

## Why local files, not URLs

The resident's `Read` tool renders an image file directly when given a
local path — no separate viewer, no base64 round-trip, no network call
mid-thought. Handing back a `github_html_url` alone (already on every
GitHub-sourced event) would require the resident to fetch it itself,
which isn't guaranteed available or reliable inside every runner
sandbox. Downloading once, at ingestion time, in the gate that already
holds the credentials to do it, converges both channels on the one
primitive that's guaranteed to work: a file on disk.

## What's deliberately not built

- **Non-image attachments** (voice, video, generic documents, GitHub
  file-diff-only PRs). This is image support, named that way in the
  original ask; a general attachment pipeline is a different, larger
  scope with different tradeoffs (size, retention, format handling) not
  raised here.
- **A retention/quota ceiling on downloaded bytes.** Telegram's own
  20MB bot-API cap and GitHub's 6-per-event extraction cap bound a
  single event; nothing yet bounds total attachment storage across a
  long-lived inbox. Not a live problem today (events are cleaned up on
  delivery), worth revisiting if that stops holding.
- **HTML image variants past a plain `src=`** (srcset, background-image,
  reference-style markdown links `![x][ref]`). Rare enough in practice
  that a miss falls back to the live `html_url`, not a wrong answer.

## Read next

[`gates/README.md`](../src/brr/gates/README.md) — the file protocol
spec, now documenting the `attachments:` field shape.
