# Casino Bot UI/UX & Gameplay Benchmark

**Review date:** 2026-10-25  
**Target branch:** `codex/premium-betting-foundation`  
**Scope:** user-facing commands, Components V2 panels, lobby lifecycle, private actions, economy, game results, permission failures, and recovery states.

This is a behavior-and-pattern benchmark, not a pixel-perfect clone. The goal is to borrow successful interaction patterns from established Discord products without copying their proprietary assets, names, or visual identity. Since this bot must use Components V2 for message panels, all message-level layouts are built with `LayoutView`, `Container`, `TextDisplay`, component rows, and media galleries.

## Benchmark set

These products are not all the same kind of bot. Economy bots are used to compare wallets, shops, and receipts; collection/RPG bots for progression and game state; trivia bots for timed group play; and multipurpose bots for help, setup, permissions, and recovery.

| Product | Public surface reviewed | Pattern worth benchmarking for Çukur |
|---|---|---|
| [UnbelievaBoat](https://unbelievaboat.com/) | Per-server economy, shop items, balance and leaderboards | Clear item pricing, configurable currency, rankings that are easy to scan, transparent economy actions |
| [Dank Memer](https://dankmemer.lol/) | Economy, items, profile, shop, progression and game commands | Group related actions, provide item-specific detail, make inventory and progression discoverable rather than expecting command memorization |
| [OwO](https://owobot.com/) | Pet collection, teams and battles | Separate setup, team state, combat state and result summaries; show meaningful stats when a game has them |
| [Mudae](https://mudae.net/bot/help) | Command categories, collection, profile and server configuration | Navigable help categories and a consistent vocabulary for collection, settings and user-facing actions |
| [Pokétwo](https://poketwo.net/) | Catching, collection, trading, marketplace and battles | Make inventory navigable, expose filters/actions near the collection, keep battle states and outcomes explicit |
| [Karuta](https://karuta.com/) | Collectible cards, trading, upgrades and global economy | Distinguish collection browsing from purchase/trade actions; communicate rarity, ownership and item identity clearly |
| [Tatsu](https://tatsu.gg/) | Social profiles, leveling, pets and economy | Give a profile a stable identity and make the economy feel connected to the member, not a disconnected command set |
| [Mimu](https://docs.mimu.bot/) | Customizable per-server currency and shop | Favor a simple economy entry point and minimize the number of separate commands required for routine actions |
| [EPIC RPG](https://epic-rpg.fandom.com/wiki/Commands) | RPG profile, inventory, progression and command guides (community-maintained reference) | Explain the next useful action, show the required items and make progression understandable to a returning player |
| [TriviaBot](https://triviabot.co.uk/help/) | Timed questions, scoreboards, teams and profiles | Use predictable round timing, score receipts, team/player standings and clear rules for a timeout |
| [Nadeko](https://nadeko.bot/) | Modular multipurpose bot and game commands | Keep command families separable and permission boundaries explicit |
| [YAGPDB](https://github.com/botlabs-gg/yagpdb-docs/blob/master/commands/custom-commands.md) | Role/channel restrictions and custom interaction configuration | Fail closed when a restriction is configured; expose where commands work and why an action was blocked |
| [Pancake](https://pancake.gg/) | Multipurpose commands, fun features and help | Keep the main feature families simple to discover, even when the bot has many capabilities |
| [MEE6](https://mee6.xyz/) | Leveling, rewards and server dashboard | Make progress and rewards legible; keep privileged configuration separate from routine player actions |
| [ProBot](https://docs.probot.io/) | Dashboard, modules, detailed logs, buttons and role tools | Treat permissions, configuration, troubleshooting and logs as first-class product surfaces |
| [Loritta](https://bestdiscord.org/bots/loritta/commands) | Large command catalog and entertainment features | Group commands by user intent and avoid a help page that simply dumps every command into one message |
| [Arcane](https://docs.arcane.bot/plugins/leveling/) | Rank cards, multiple leaderboards and role rewards | Give leaderboards a clear scope and give users a useful personal state alongside community rankings |
| [Carl-bot](https://carl.gg/) | Configurable role/panel workflows | Use predictable controls for setup and guard interactions by the required permissions |
| [Ticket Tool](https://tickettool.xyz/) | Button-driven entry points and guided flows | Let a single, readable entry panel route a user into a short task-specific flow instead of explaining every branch at once |
| [Wick](https://wickbot.com/) | Security, setup and permission-focused workflows | Explain a denial safely and clearly; do not leak internal errors or sensitive state in public channels |

The table is a design reference, not a claim that every product has the same architecture or that its entire UI has been exhaustively reverse-engineered. The references above are public product/help surfaces and are useful for the patterns listed in the right column.

## Standards applied to this branch

### 1. One visual system, multiple message purposes

- **Lobby:** game name, participation rules, player count, remaining time, host action and join/leave controls.
- **Active round:** only the state and controls relevant to that phase.
- **Result/receipt:** winner/outcome, stake, gross payout, net change, fees/refunds when applicable.
- **Private status:** ownership/permission errors and personal economy results.
- Ongoing game messages are edited in place when the update represents the same event. Distinct game events remain separate messages so a busy channel is not collapsed into one giant panel.
- UI buttons use secondary styling consistently unless a truly exceptional action needs a stronger visual signal.
- Legacy developer-promo buttons have been removed. Branding is handled by the shared theme/assets rather than a disabled button pasted onto a panel.

### 2. Interaction behavior

- A component handler acknowledges/deferes when it may wait on I/O, then sends a private V2 receipt or edits the active panel.
- Sensitive actions (secret Mafia selections, personal wallet/inventory, transfers and purchase receipts) stay private.
- Unauthorized clicks get a private, task-specific explanation instead of mutating state.
- Invitations explicitly opt into the one notification they need. Routine results show member references without pinging every player.
- A timed phase has a wall-clock deadline from view creation. Repeated clicks must not extend a round indefinitely, and callbacks arriving after the deadline cannot change the result.
- If an action fails, the bot must not announce success before the persistence layer confirms it. Errors should have a correlation/log record, but the player-facing message must not leak exception contents.

### 3. Economy behavior

- Wallet, shop, inventory, profile, daily reward, leaderboard and transfer are reachable through the economy hub.
- The same profile renderer is reused for command and hub entry points.
- Purchases and transfers acknowledge the interaction before database work and use atomic ledger operations; uncertain outcomes are reported as uncertain instead of being presented as successful.
- Database reads/writes that can block are dispatched off the Discord event loop on the interaction paths reviewed so far.
- Item use shows whether the item was consumed and what the new balance is; failure paths direct the user to verify current state before retrying.

### 4. Game lifecycle behavior

- A fixed phase timer is enforced for Mafia voting and secret actions, hide-and-seek choices, musical-chair rounds and roulette targeting.
- Late selections are rejected, action controls close, and expired panels say what happened instead of leaving a stale menu that looks usable.
- Tests cover timeout helpers, expired secret selections, and the rule that an expired roulette weapon action must not consume inventory.
- Riddle answers are normalized for common Arabic diacritics, spacing and punctuation; invalid/empty question banks fall back safely. Its reward credit uses a worker thread rather than blocking the message loop.

### 5. CI guardrails

- A source-level AST test rejects direct outgoing `embed=` and nonempty `embeds=` message payloads across `main.py` and `cogs/`; clearing old embeds during a replacement edit is allowed.
- A second guard rejects legacy disabled developer-attribution buttons.
- Another policy test protects the default no-ping behavior while allowing RPS to notify only the challenged opponent.
- Test coverage includes V2 rendering, command/help metadata, fixed deadlines, game state/settlement, inventory/shop behavior, privacy and recovery behavior.

## Remaining staging checks

CI does not prove the Discord API accepted every live component arrangement or that every attachment renders correctly on desktop and mobile. Before calling this “production ready”, staging must still exercise every game from start to finish, including:

1. Empty and full lobbies, two simultaneous starts, host cancellation, timeout, leave/join races and missing/deleted messages.
2. Duplicate/late component clicks; each private action must resolve once and only the authorized player may use it.
3. Wager success/loss/draw, insufficient funds, max-balance limits, interrupted database operations, retries and restart recovery; verify exact wallet deltas.
4. All attached art galleries on mobile and desktop, image load failures, and a result panel after each game’s normal and error paths.
5. Slash and prefix commands under the real server’s roles, allowed channels, intents and permissions.
6. Process restart and extension reload with persistent economy controls; verify the configured volume preserves wallets.

## Current readout

- **Latest verified automated status:** head `576c4a762caeb0ce4b266b546c6f760e8916720b` passed Python 3.11 and 3.14 (340 unit tests in the Python 3.11 run), Ruff, broad syntax/undefined-name checks, `compileall`, Docker build/startup smoke, and dependency audit. See [GitHub Actions #40183931873](https://github.com/SaylerX505/Casino-bot/actions/runs/40183931873). Changes made after that SHA must pass their own CI run before release.
- **Not yet verified:** a live Discord staging session on the newest branch head. Components V2 has been unit-checked, but live attachment rendering, mobile layouts, actual Discord API interactions, and real server permissions still require staging.
- **Release policy:** keep this branch separate; do not merge or deploy on CI success alone.
