"""Regression test for double-click races on the roulette weapon button."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from cogs.casino.roulette_views import KickView


class RouletteWeaponConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_weapon_clicks_consume_only_one_inventory_item(self):
        chosen_player = SimpleNamespace(
            id=101,
            mention="<@101>",
            display_name="Chosen player",
        )
        target = SimpleNamespace(id=202, mention="<@202>", display_name="Target")
        currency = SimpleNamespace(remove_item=Mock(return_value=True))
        view = KickView(
            chosen_player,
            [chosen_player, target],
            has_weapon=True,
            currency_cog=currency,
            round_num=1,
        )

        first_ack_started = asyncio.Event()
        release_first_ack = asyncio.Event()

        async def pause_first_ack(**_kwargs):
            first_ack_started.set()
            await release_first_ack.wait()

        first_response = SimpleNamespace(
            edit_message=AsyncMock(side_effect=pause_first_ack),
            send_message=AsyncMock(),
        )
        second_response = SimpleNamespace(
            edit_message=AsyncMock(),
            send_message=AsyncMock(),
        )
        first_interaction = SimpleNamespace(
            user=chosen_player,
            response=first_response,
        )
        second_interaction = SimpleNamespace(
            user=chosen_player,
            response=second_response,
        )

        first_task = asyncio.create_task(view.weapon_callback(first_interaction))
        await asyncio.wait_for(first_ack_started.wait(), timeout=1)

        # The first callback has charged one item and changed the local view,
        # but its Discord acknowledgement is still blocked.
        self.assertTrue(view.double_shot_active)
        self.assertTrue(view.weapon_btn.disabled)
        second_task = asyncio.create_task(view.weapon_callback(second_interaction))
        await asyncio.wait_for(second_task, timeout=1)

        currency.remove_item.assert_called_once_with(101, "double_shot", 1)
        second_response.send_message.assert_awaited_once()
        second_response.edit_message.assert_not_awaited()

        release_first_ack.set()
        await asyncio.wait_for(first_task, timeout=1)
        first_response.edit_message.assert_awaited_once_with(view=view)
        self.assertEqual(currency.remove_item.call_count, 1)


if __name__ == "__main__":
    unittest.main()
