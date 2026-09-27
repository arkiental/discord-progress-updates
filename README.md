# Discord progress updates

A Codex skill for image-only progress reporting through a persistent Python outbox watcher. Requires Python 3.10+; uses only the standard library.

## Install

Clone this private repository into your Codex skills directory as `discord-progress-updates`, using a GitHub account with access. Read [SKILL.md](SKILL.md) for the agent workflow and [operations](references/operations.md) for setup and recovery.

Configure your own Discord webhook locally:

```powershell
python scripts/discord_images.py configure
python scripts/discord_images.py start
```

Configuration prompts privately. Alternatively supply `DISCORD_PROGRESS_WEBHOOK` through your local environment. Credentials, local backups, outbox contents, and Python caches are excluded from this repository.

Invoke `$discord-progress-updates` in Codex when you want image updates. Only relevant, inspected images should be queued; the skill does not post text updates.

## Verify

```powershell
python -m unittest discover -s tests -v
```

The current watcher suite contains 12 offline tests. Live Discord delivery requires your local webhook and is confirmed by a Discord message ID.

`scripts/test_post_update.py` is preserved from the source skill as a legacy test suite for the removed text-posting API; it is not the current validation command. `scripts/post_update.py` is now an image-only compatibility entry point.
