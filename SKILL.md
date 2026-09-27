---
name: discord-progress-updates
description: Post frequent image-only progress updates to the configured Discord webhook through a persistent Python outbox watcher. Use when the user requests Discord updates, screenshot reporting, or invokes this skill during a task.
---

# Discord image updates

Invoking this skill or requesting Discord reporting authorizes task-related image uploads to the configured destination. Discovery alone does not authorize uploading unrelated work. Post **images only**: no captions, text messages, embeds, or text rendered into artificial status cards.

Use Python 3.10+ and `scripts/discord_images.py` relative to this skill. Resolve the script path once; use bundled Python if needed. The helper uses only the standard library.

1. Once per task, run `python <script> start`. This is idempotent. It prints the outbox and whether the watcher is running. Never load its source or poll status repeatedly during ordinary use.
2. At visible milestones, inspect a real screenshot, render, chart, or image deliverable, then run `python <script> enqueue <absolute-image-path>`. This atomically copies it into the shared outbox; the background process sends it. Enqueued is not confirmed delivery.
3. During sustained visual work, aim for a fresh image every 1–2 minutes at a natural tool boundary. Reuse captures already needed for QA. If there is no meaningful new image, continue work without a Discord post. Do not spend image-generation calls creating progress graphics or idle to satisfy cadence.
4. Enqueue the final useful image and check `python <script> status` once. Report any held/failed delivery locally. Name and link this skill in the final response when used to send updates.

Only enqueue images inspected for relevance and accidental credentials/private material. Generated deliverables are valid; fabricated evidence is not. Use unique filenames when saving directly into the printed outbox, preferably write `.part` then atomically rename. The watcher scans only that directory, not project folders or the computer.

The watcher is shared across tasks. Leave it running; it consumes no model tokens while idle. If the user requests all reporting to stop, run `python <script> stop`. If only this task stops reporting, simply stop enqueueing. It does not automatically start after Windows restart; `start` restores it on the next reporting task.

Credentials are read from `DISCORD_PROGRESS_WEBHOOK` or the Windows-account-encrypted `.local/webhook.dpapi`. Never print/read the credential into model context. `configure` prompts privately. Do not put webhook URLs into commands, source, logs, or packages.

Delivery is confirmed only by Discord message ID. Persistent SHA-256 deduplication survives restart; identical bytes are sent once per outbox. Timeouts, server errors, or interrupted sends are held without automatic replay. Explicit rate limits are retried at the stated time, at most five times. Authentication failure pauses the watcher. For setup, recovery, and implementation limits, read [operations](references/operations.md) only when needed.
