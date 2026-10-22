"""Tests for GeoGuessr image rendering isolated from Discord game flow."""

import unittest
from unittest.mock import patch

from cogs.casino.geoguessr_art import (
    draw_dashed_line,
    generate_dashboard_image,
    generate_result_map,
)


class DrawSpy:
    def __init__(self):
        self.calls = []

    def line(self, points, *, fill, width):
        self.calls.append((points, fill, width))


class GeoGuessrArtworkTests(unittest.IsolatedAsyncioTestCase):
    def test_dashed_line_ignores_zero_length_and_draws_short_lines_once(self):
        draw = DrawSpy()
        draw_dashed_line(draw, (1, 1), (1, 1), fill="white", width=2)
        self.assertEqual(draw.calls, [])

        draw_dashed_line(draw, (0, 0), (3, 4), fill="white", width=2, dash_length=15)
        self.assertEqual(len(draw.calls), 1)
        self.assertEqual(draw.calls[0][1:], ("white", 2))

    async def test_result_map_renders_png_without_players_or_optional_assets(self):
        with patch("cogs.casino.geoguessr_art.Image.open", side_effect=FileNotFoundError):
            rendered = await generate_result_map((0.0, 0.0), [])

        self.assertEqual(rendered.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertGreater(len(rendered.getvalue()), 100)

    async def test_dashboard_renders_png_without_players_or_optional_assets(self):
        with patch("cogs.casino.geoguessr_art.Image.open", side_effect=OSError("missing asset")):
            rendered = await generate_dashboard_image([], round_num=1, total_rounds=6)

        self.assertEqual(rendered.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertGreater(len(rendered.getvalue()), 100)


if __name__ == "__main__":
    unittest.main()
