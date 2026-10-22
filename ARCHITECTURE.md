# Casino-bot architecture

The bot remains a single-process Discord application, but each subsystem should have explicit responsibility boundaries. Extract a module when a concern can be exercised without booting Discord or running a full game.

## Current boundaries

- `main.py`: Discord client setup, guild policy, command-error reporting, and extension loading.
- `cogs/casino/game_registry.py`: ownership of active lobby/game tasks by channel. A game must release only the entry it owns.
- `cogs/casino/bank.py`: atomic economy operations and the public persistence-facing API. Game modules should use domain operations instead of editing tables directly.
- `cogs/casino/bank_schema.py`: ordered, transactional SQLite migrations applied from `schema_migrations`; legacy tables are adopted with `CREATE TABLE IF NOT EXISTS` rather than replaced.
- `cogs/casino/bank_ledger.py`: append-only economy audit records and read queries, isolated from balance business operations; correlated multi-player actions share a transaction ID.
- `cogs/casino/solo_betting.py`: shared solo-betting panel for coinflip, dice, and slots, with configurable outcome rules/payout tables; requester-bound Components V2 controls, bounded wagers, disclosed odds, and recovery-aware settlement. Wallet changes go through the bank API, never direct SQL.
- `cogs/casino/blackjack.py`: multi-step single-player Blackjack lifecycle, hand-scoring rules, dealer policy, and persisted final outcomes; uses the same bank wager API while keeping its game-specific state machine separate from simple one-choice bets.
- `cogs/casino/geoguessr.py`: GeoGuessr round orchestration and Discord message flow; composes storage, artwork, and view modules.
- `cogs/casino/geoguessr_storage.py`: path-based read/write/validation for static round data and used-image tracking. It does not import Discord.
- `cogs/casino/geoguessr_art.py`: image-rendering helpers and optional avatar downloads. It does not manage lobbies, scoring, or Discord views.
- `cogs/casino/geoguessr_views.py`: Components V2 lobby, round, and results panels. The lobby receives its game-loop callable by dependency injection and does not import the coordinator.
- `cogs/casino/mafia.py`: Mafia game state, round orchestration, role resolution, and economy interactions.
- `cogs/casino/mafia_views.py`: Mafia lobby, secret selection, day voting, and role reveal Components V2 views; the lobby receives the game loop through dependency injection.
- `cogs/casino/mafia_art.py`: Mafia image generation, separate from role and round logic.
- `cogs/casino/roulette.py`: roulette lobby, round orchestration, scoring, and Discord result flow.
- `cogs/casino/roulette_views.py`: reusable Components V2 target selection and weapon-action panel.
- `cogs/casino/roulette_art.py`: optional artwork composition and avatar downloads, separated from roulette state and payout decisions.
- `cogs/casino/currency.py`: economy command handlers and the cog-facing bank adapter.
- `cogs/casino/currency_catalog.py`: the item catalog and shared currency formatting.
- `cogs/casino/currency_views.py`: shop, inventory, transfer modal, and persistent economy hub components; views use a narrow cog-facing interface instead of owning wallet storage.
- `cogs/casino/game_assets.py`: shared static asset URLs referenced by multiple games.

## Rules for new game work

1. Keep wallet and inventory mutations in the bank/storage layer; make transactions atomic wherever money or items are involved.
2. Move Discord component classes into a view module when they are no longer small. Pass callbacks or narrow services in instead of making the view import its coordinator.
3. Keep artwork/formatting helpers separate from state transitions. An optional artwork failure must never decide a winner or change a payout.
4. Register one owner task per game lobby/channel and release it only if that same task still owns the registry entry.
5. Persist solo stakes, outcomes, and payout receipts before settlement; keep settlement idempotent and refund orphaned sessions after interruption.
6. Test storage, scoring, and state transitions at their lowest useful boundary, then add component tests and a staging run.
7. Refactor incrementally: preserve command names and user-visible behavior unless a behavior change is intentional, and keep the regression suite green.

Avoid abstractions without a real boundary. Introduce shared interfaces when multiple games actually need the same lifecycle or domain behavior, rather than anticipating every possible future feature.
