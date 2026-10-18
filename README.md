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

The bot uses the configured prefix `+`. See the in-bot help menu for the available commands and game rules.

## Data and persistence

The economy's SQLite database is configured at the relative path `data/bank.db`. Start the process from the repository root and ensure that the host keeps the project's data directory on persistent storage between restarts and deployments.

Do not delete, replace, or recreate the live database as part of an ordinary code update. Before a deployment that could affect persistence, take a backup of the active database while the bot is stopped or through a consistent SQLite backup method. The files under `data/` and `archive-wrapper-files/` may represent different snapshots; do not assume an archived database is the currently active one.

AI conversation/state data is stored in `data/ai_data.json`. Treat stored user data and backups as sensitive and restrict access to them.

## Checks and tests

Run the syntax check and the isolated test suite from the repository root:

```bash
python -m compileall -q main.py config.py cogs tests
python -m unittest discover -s tests -v
```

The tests use a temporary SQLite database and should not operate on the live economy database. GitHub Actions runs these checks on pushes and pull requests.

## Deployment notes

- Store tokens in environment variables, not in source code.
- Preserve persistent storage across host redeployments.
- Verify the bot's startup logs and test a small set of commands in a non-production channel after deploying a new version.
- The automated suite cannot replace an end-to-end test against a real Discord server.
