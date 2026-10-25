"""Shared static assets referenced by more than one game."""

import config

# Keep every game lobby and shared panel on the same configured brand asset.
SERVER_LOGO_URL = getattr(config, "SERVER_LOGO_URL", "")
