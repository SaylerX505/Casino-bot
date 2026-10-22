# Casino-bot

An advanced casino and social-games Discord bot for the Çukur server.

## Requirements

- Python 3.11 or newer
- A Discord bot application with the required privileged intents enabled
- A Discord bot token
- Optional API keys for enabled integrations

## Setup

1. Clone the repository and run commands from the repository root.
2. Install dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and provide the values for `DISCORD_TOKEN`, `DEEPSEEK_API_KEY`, and `REMBG_API_KEY` as needed. Keep the real `.env` file private.
4. In the Discord Developer Portal, enable the privileged intents used by the bot, including **Message Content** and **Server Members**.
5. Start the bot:

   ```bash
   python main.py
   ```

The bot uses the configured prefix `+`. See the in-bot help menu for the available commands and game rules. Runtime dependency pins are kept in `requirements.txt`; `requirements.lock` pins the complete Python 3.11 dependency resolution used by Docker and CI.

## Solo betting game

- `/coinflip bet:100` or `+coinflip 100` opens a choice panel in the channel; only the requester can use its controls.
- The player chooses Heads or Tails. A correct guess receives a **1.9x gross return** (0.9x net profit); a miss loses the stake.
- The panel edits in place instead of posting a second result message. The bet is debited only after the player chooses.
- Solo wagers and their outcomes are recorded transactionally with economy ledger entries. Settlement is idempotent; an interrupted active session is refunded during startup recovery.
- Configure the permitted channels through `/wizard`, just like the other games.

## Setup wizard

Run `/wizard` as the server owner or a Discord Administrator to configure the bot privately. The wizard lets you choose bot-manager roles and game-staff roles, set a separate allowed-channel list for each game, disable or reopen individual games, configure economy/error log destinations, manage the optional AI responder and its access-role allowlist, and review saved settings. Changes are persisted immediately in SQLite; restart is not required. Discord Administrator remains a trusted override for hosting games. Role selection grants only bot game-staff access, not new Discord permissions. The wizard cannot be managed by the staff roles it configures.

## Single-server configuration

This bot is designed to run in one Discord server. `HOME_GUILD_ID` is required: the process refuses to start when it is missing. When configured, prefix commands and slash commands are restricted to that guild, slash commands are registered as guild commands, and the bot leaves other guilds. `AI_GUILD_ID` can override the AI guild separately. Configure `CURRENCY_LOG_CHANNEL_ID` and, when needed, `AI_ALLOWED_ROLE_IDS` as comma-separated Discord IDs.

## Data and persistence

The economy and small application settings are stored in SQLite at the project-root `data/bank.db` by default. Schema changes are applied through ordered, transactional migrations recorded in `schema_migrations`; legacy tables are adopted without replacing their rows. Balance changes are written to the append-only `economy_ledger` in the same SQL transaction as the balance mutation, with correlated transaction IDs for transfers and wagers. Set `BANK_DB_FILE` only if the host requires a custom path. Mount persistent storage at `/app/data` when using Docker. Keep only one bot process connected to a given SQLite file.

Never commit the live database, SQLite journal files, user histories, activity snapshots, runtime logs, backups, or real environment secrets. Archived source snapshots are excluded from Docker images. Static game content such as `data/database.json` and `data/fazoora.json` remains in the repository because the games need it.

AI conversation history is kept **in memory only**, scoped by guild, channel, and user; it is not written to a JSON file and is cleared on restart. The bot no longer reads recent channel history to add other members' messages to an AI request. Only the invoking user's prompt and their own recent AI conversation are sent to the configured DeepSeek API. The `/ai-forget` or `+ai-forget` command clears that user's context in the current channel. Configure the AI role allowlist from `/wizard` (the host's `AI_ALLOWED_ROLE_IDS` is used until a server-specific override is saved) and provide the required API key only on the host. The wizard will not enable AI unless `DEEPSEEK_API_KEY` is present.

The small AI-enabled setting is stored in the SQLite database. The Git repository's earlier commits may still contain deleted data files; deleting files from current branch snapshots does not remove historical Git objects. A coordinated history rewrite and post-rewrite verification are still needed before claiming the historical copies have been purged.

The private `/wizard` also configures the economy-audit and command-error log channels independently for this server. Set each destination to a text/announcement channel the bot can view and send to, or explicitly disable that log type. Command and economy log delivery checks the destination guild before sending, and automated log panels disable mentions. Unexpected prefix and slash command errors use the same redacted, Components V2 reporting path.

## Docker deployment

The `Dockerfile` builds a Python 3.11 slim image, installs dependencies in a cache-friendly layer, and runs the bot as an unprivileged user. The image starts the bot with `python -u main.py`; it does not expose a web port because this is a Discord gateway bot.

1. Set `DISCORD_TOKEN` in the hosting panel's environment variables. Add `DEEPSEEK_API_KEY` and `REMBG_API_KEY` only if those integrations are used. Do not bake a real `.env` file into the image.
2. Attach a **persistent volume mounted at `/app/data`** before starting the container. The entrypoint can initialize missing files from the repository's tracked data snapshot, but it never overwrites files already present in the volume.
3. Before migrating an existing live instance, stop it and back up its entire active `data/` directory. Restore that data into the persistent volume before the new container starts—especially `data/bank.db`. Database files are deliberately excluded from the Docker build context and are not recoverable from the image. Do not point the new deployment at an empty volume if you need to keep existing balances or state.
4. Keep an independent backup outside the host. A persistent volume is not a substitute for backups.

Build the image locally with:

```bash
docker build -t casino-bot .
```

For a fresh, new installation only, a named volume can be used with `docker run -d --name casino-bot --restart unless-stopped --env-file .env -v casino-data:/app/data casino-bot`. Do not use this fresh-volume example to migrate a running bot without restoring the old data first.

## Checks and tests

Run the syntax check and the isolated test suite from the repository root:

```bash
python -m compileall -q main.py config.py cogs tests
python -m unittest discover -s tests -v
```

The tests use temporary SQLite databases and should not operate on the live economy database. Run `python -m compileall -q main.py config.py cogs tests`, then `coverage run --branch -m unittest discover -s tests -v` and `coverage report --show-missing` to check correctness and coverage. CI also runs a broader undefined-name/syntax scan and audits every package in `requirements.lock` with pip-audit. RPS wagers are recorded as escrow; unresolved settlements retry with bounded backoff while the process is alive, remaining orphaned wagers are refunded at startup, and settlement is idempotent. Run only one bot instance against a given economy database. GitHub Actions builds the Docker image as a deployment smoke check.

## Deployment notes

- Store tokens in environment variables, not in source code.
- Preserve persistent storage across host redeployments.
- Verify the bot's startup logs and test a small set of commands in a non-production channel after deploying a new version.
- The automated suite cannot replace an end-to-end test against a real Discord server. Use [`PRODUCTION_CHECKLIST.md`](PRODUCTION_CHECKLIST.md) as the release gate before switching a live host to a new image.
