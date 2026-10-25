# Production release checklist

This checklist is a release gate, not a claim that every item is already complete.

## Automated gate

- [ ] Latest commit on the release candidate has a green GitHub Actions run.
- [x] Slash command metadata is preflight-validated; empty/invisible descriptions block guild sync and identify the offending command locally.
- [ ] Python compilation and the full isolated test suite pass, including diagnostic formatter, rotation, incident correlation, and command acknowledgement tests.
- [ ] Docker build and startup smoke tests pass.
- [ ] Docker seed verification confirms that static data is present and runtime data/secrets are absent from the image.

## Data and secrets

- [ ] Keep real `.env` files, API keys, bot tokens, runtime histories, activity trackers, local `data/logs/` diagnostics, SQLite files, and backups out of Git and Docker images.
- [ ] Before migrating a live bot, stop it and create a consistent backup of the entire active `data/` directory, especially `bank.db`.
- [ ] Validate the backup and restore it into a test volume before production cutover.
- [ ] Attach a persistent Docker volume at `/app/data`. Do not rely on an anonymous temporary volume to preserve wallets.
- [ ] Keep one active bot process per shared SQLite database. Multiple replicas sharing the same database are not a supported topology.
- [x] Runtime AI history, activity snapshots, used-image trackers, legacy currency JSON, SQLite database/journal files, and bot logs are removed from current snapshots of the active branches and excluded from Docker build contexts. Static game content remains tracked.
- [ ] Older Git-history copies may still be retrievable by commit SHA. Coordinate a history rewrite and verify the result before claiming historical copies are purged.
- [x] The bot is designed for one Discord server. `HOME_GUILD_ID` is required; startup refuses to proceed without it, and slash/prefix commands are scoped to that guild.
- [x] `/wizard` configures trusted manager roles, game-staff roles, and an independent channel allowlist (or all channels) for each game. Every change persists to SQLite without restart; manager roles cannot change their own manager-role allowlist.
- [x] `/commands` opens the private command menu; `/wizard` replaces the need to remember separate setup commands for routine role/channel configuration.
- [x] `/wizard` configures per-guild economy and command-error log destinations or disables either log type; prefix and slash failures use redacted V2 log panels and reject cross-guild destinations.
- [x] `/wizard` manages the optional AI responder and its per-guild access-role override; AI cannot be enabled through the wizard without `DEEPSEEK_API_KEY`.
- [ ] Wallet rows use Discord user IDs within the one-server deployment; economy reset remains disabled through Discord and requires a manual, backed-up maintenance procedure. Active RPS and solo casino wagers both block maintenance reset.

## Staging Discord validation

Run these against a staging server/channel before production:

- [ ] Verify prefix and slash commands both acknowledge success, permission failures, and unexpected errors without duplicate responses.
- [ ] Verify a regular member cannot host staff-restricted games, manage the bot-admin allowlist, or invoke global economy/AI controls.
- [ ] Verify a Discord Administrator without bot-admin allowlist access cannot run protected bot management actions until explicitly added.
- [ ] Exercise each game from lobby to completion, including lobby timeout, host cancellation, player departure, duplicate button clicks, and two simultaneous start attempts in one channel.
- [ ] Test Mafia scan with open and closed DMs; confirm the target is never revealed in public chat, the item is refunded after delivery failure, and refund failures are reported without leaking the hint.
- [ ] For instant coinflip, dice, and slots, test win/loss, insufficient funds, timeout/cancel before choosing, competing button presses, transaction rollback, duplicate game IDs, payout overflow, and interruption immediately after database commit. Confirm stake/result/payout commit together, no completed instant wager remains active, and recovery reads cannot mutate a settled balance. Verify dice has six choices and 1-in-6 odds with 5.7x gross return, and slots pays exactly the documented 1.7x pair / 5x–15x triple table.
- [ ] For Blackjack, test initial naturals (player, dealer, both), hard/soft aces, hit to bust, stand, dealer bust, push, normal win, natural payout, start timeout/cancel, settlement failure, and startup refund of an interrupted hand. Confirm action buttons close during settlement and stake/payout are recorded at most once.
- [ ] For Rock-Paper-Scissors, test winner, draw, timeout, insufficient funds, database/host restart mid-match, and repeated settlement. Confirm no stake is charged or paid twice; where channel/message references exist, confirm startup recovery replaces the old challenge controls with a receipt showing both stakes were refunded.
- [ ] Simulate several transient database failures during RPS settlement; verify one background recovery worker retries safely, updates the original message after recovery, and never double-pays. Confirm remaining escrow is refunded on restart.
- [ ] Check every game result for correct balance receipts and verify that a failed game does not silently leave players charged.
- [ ] Verify required Discord intents and permissions from the hosting environment. Inspect startup and application-command logs.
- [ ] Confirm persistent inventory/economy views still respond after a process restart and after redeployment.
- [ ] Set both log destinations through `/wizard`; verify command errors from prefix and slash commands arrive in the selected channel without pinging users, and that another guild's channel can never receive them.
- [ ] Verify `/wizard` can enable AI only when the key is configured, persists the toggle across restart, and applies the saved AI role override; verify unauthorized roles receive no AI response.

## UI and operational gates

- [x] Game and utility announcements use Components V2 panels; a CI AST guard rejects direct legacy \`embed=\`/non-empty \`embeds=\` send payloads and rejects obsolete disabled developer-promo buttons.
- [x] Game lobbies, round panels, and results remain separate events rather than being collapsed into one giant panel. The shared theme handles status colors, safe edit fallbacks, and branded receipts.
- [x] Fixed wall-clock deadlines are enforced for Mafia secret actions/voting, hide-and-seek choices, musical-chair rounds, and roulette targeting; callbacks reject late actions, and tests cover late selections and item-consumption safety.
- [x] Ordinary game mentions are silent by default; the Rock-Paper-Scissors invitation explicitly opts in to notifying only the challenged opponent.
- [ ] Review rendered results, channel noise, repeated messages, and failure recovery in the live staging server—not only unit-tested paths.
- [ ] Confirm the host's disk/volume persistence, restart policy, log retention, and independent backup process.
- [ ] Keep the PR as draft until the staging checks and remaining UI/architecture work are reviewed. Do not merge or deploy based solely on CI success.
