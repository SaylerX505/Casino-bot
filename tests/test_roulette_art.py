"""Tests for roulette artwork output when optional assets/network are unavailable."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs.casino.roulette_art import generate_sniper_image


class RouletteArtworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_sniper_art_still_renders_png_without_optional_logo_or_avatar(self):
        player = SimpleNamespace(display_avatar=SimpleNamespace(url="https://invalid.example/avatar.png"))
        with (
            patch("cogs.casino.roulette_art.os.path.exists", return_value=False),
            patch("cogs.casino.roulette_art.aiohttp.ClientSession", side_effect=OSError("network disabled")),
        ):
            rendered = await generate_sniper_image(player)

        self.assertEqual(rendered.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertGreater(len(rendered.getvalue()), 500)


if __name__ == "__main__":
    unittest.main()
