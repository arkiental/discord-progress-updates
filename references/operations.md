# Operations

Commands: `start`, `stop`, `status`, `enqueue PATH`, `watch` (foreground), `configure` (hidden credential prompt). Optional `--outbox PATH` goes before the command. Default: `~/Documents/Codex/discord-image-outbox`.

The outbox has `pending/` for intentional submissions and `.state/` for the SQLite ledger, process lock, heartbeat, and stop signal. No recursive scanning. Keep this folder private. Symlinks/reparse points are rejected. Supported signatures: PNG, JPEG, GIF, WebP; maximum 8 MiB per image. Signature checks are not a full image decoder or privacy scanner; agents must inspect images first.

Every two seconds, scan file size/mtime and wait until unchanged for three seconds. Atomic enqueue is preferred because a paused writer can appear stable. Read each image into memory and upload those exact hashed bytes. One attachment per post, at least 20 seconds apart, no captions or embeds. Filenames sent to Discord are generic. The ledger deduplicates byte-identical images across filenames and restarts. Submitted files remain in pending for local evidence; tracked unchanged files are not rehashed. `status` lists aggregate states and ten recent outcomes without credentials.

After ambiguous delivery, inspect the Discord channel before deciding whether to resubmit. There is intentionally no blind retry command: Discord webhooks do not provide an exactly-once delivery guarantee. A crash after claiming a file holds it on restart, even if it was never delivered. This favors avoiding duplicates over guaranteed delivery. A failed/held image can be manually replaced by a newly captured image; do not delete the database just to bypass deduplication.

`stop` requests cooperative shutdown (an in-flight network request may take up to 30 seconds); it never kills unrelated processes. Pending images remain queued and resume on `start`. The watcher is a detached local process, not a boot service or Codex scheduled automation. It runs only while the computer is awake and the process is alive. `start` after a reboot or crash resumes the queue. No global settings are changed.

# Research and decisions

Problem: frequent visual reporting with minimal agent overhead. Chosen design: standard-library Python polling plus SQLite. Native file events/watchdog offer lower idle polling but add installation and event-coalescing complexity; a dedicated small outbox makes a two-second scan inexpensive. Agents only capture/inspect/enqueue; the uploader needs no model calls.

[Discord Execute Webhook](https://docs.discord.com/developers/resources/webhook#execute-webhook) supports attachment-only multipart requests and `wait=true` returns confirmation. The uploader omits content/embeds/components entirely and disables mentions. [Discord rate limits](https://docs.discord.com/developers/topics/rate-limits) specify retry timing on HTTP 429; retry only explicit rate-limit rejections. Forum/media channels need a thread destination; this helper targets an ordinary webhook channel and treats incompatible destination errors as failures.

Validation: unit/integration tests exercise image-only wire payloads, stable files, deduplication/restart, competing watchers, uncertain sends, rate limits, invalid images, and atomic enqueue. Live delivery must be checked separately; local tests do not establish Discord connectivity.
