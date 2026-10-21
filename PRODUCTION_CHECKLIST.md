# Production release checklist

This checklist is a release gate, not a claim that every item is already complete.

## Automated gate

- [ ] Latest commit on the release candidate has a green GitHub Actions run.
- [ ] Python compilation and the full isolated test suite pass.
- [ ] Docker build and startup smoke tests pass.
- [ ] Docker seed verification confirms that static data is present and runtime data/secrets are absent from the image.

## Data and secrets

- [ ] Keep real `.env` files, API keys, bot tokens, runtime histories, activity trackers, SQLite files, and backups out of Git and Docker images.
- [ ] Before migrating a live bot, stop it and create a consistent backup of the entire active `data/` directory, especially `bank.db`.
- [ ] Validate the backup and restore it into a test volume before production cutover.
- [ ] Attach a persistent Docker volume at `/app/data`. Do not rely on an anonymous temporary volume to preserve wallets.
- [ ] Keep one active bot process per shared SQLite database. Multiple replicas sharing the same database are not a supported topology.
- [x] Runtime AI history, activity snapshots, used-image trackers, legacy currency JSON, SQLite database/journal files, and bot logs are removed from current snapshots of the active branches and excluded from Docker build contexts. Static game content remains tracked.
- [ ] Older Git-history copies may still be retrievable by commit SHA. Coordinate a history rewrite and verify the result before claiming historical copies are purged.
- [ ] The bot is designed for one Discord server. `HOME_GUILD_ID` is required; startup refuses to proceed without it. Configure and verify the intended server ID on the production host.
- [ ] Wallet rows use Discord user IDs within the one-server deployment; economy reset remains disabled through Discord and requires a manual, backed-up maintenance procedure.

## Staging Discord validation

Run these against a staging server/channel before production:

- [ ] Verify prefix and slash commands both acknowledge success, permission failures, and unexpected errors without duplicate responses.
- [ ] Verify a regular member cannot host staff-restricted games, manage the bot-admin allowlist, or invoke global economy/AI controls.
- [ ] Verify a Discord Administrator without bot-admin allowlist access cannot run protected bot management actions until explicitly added.
- [ ] Exercise each game from lobby to completion, including lobby timeout, host cancellation, player departure, duplicate button clicks, and two simultaneous start attempts in one channel.
- [ ] For Rock-Paper-Scissors, test winner, draw, timeout, insufficient funds, database/host restart mid-match, and repeated settlement. Confirm no stake is charged or paid twice.
- [ ] Check every game result for correct balance receipts and verify that a failed game does not silently leave players charged.
- [ ] Verify required Discord intents and permissions from the hosting environment. Inspect startup and application-command logs.
- [ ] Confirm persistent inventory/economy views still respond after a process restart and after redeployment.

## UI and operational gates

- [ ] The main game modules still use legacy embeds/views; the requested consistent Components V2 migration is not complete. Finish and review this migration before declaring the UI work complete.
- [ ] Review channel noise, repeated messages, and failure recovery in every game—not only unit-tested paths.
- [ ] Confirm the host's disk/volume persistence, restart policy, log retention, and independent backup process.
- [ ] Keep the PR as draft until the staging checks and remaining UI/architecture work are reviewed. Do not merge or deploy based solely on CI success.
