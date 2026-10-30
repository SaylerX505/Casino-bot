"""Ensure high-contention economy I/O never blocks Discord's event loop."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.casino.currency_catalog import DAILY_REWARD_MAX, DAILY_REWARD_MIN
from cogs.casino.currency_views import (
    BuyForm,
    CurrencyHubLayout,
    TransferModal,
    build_currency_hub_view,
    build_inventory_view,
    build_profile_panel,
    build_shop_view,
)


def fake_interaction(*, user_id=101, guild=None):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, mention=f"<@{user_id}>"),
        guild=guild,
        response=SimpleNamespace(
            defer=AsyncMock(),
            is_done=Mock(return_value=True),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class EconomyCallbackThreadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_shop_purchase_runs_sqlite_transaction_in_worker_thread(self):
        main_thread = threading.get_ident()
        transaction_threads = []

        def purchase(*_args):
            transaction_threads.append(threading.get_ident())
            return True, 80

        interaction = fake_interaction()
        modal = SimpleNamespace(
            children=[SimpleNamespace(value="2")],
            item_id="loot_box",
            price=10,
            item_name="صندوق الحظ",
        )
        with patch("cogs.casino.currency_views.bank.purchase_item", side_effect=purchase) as mocked:
            await BuyForm.on_submit(modal, interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        interaction.followup.send.assert_awaited_once()
        mocked.assert_called_once_with(101, "loot_box", 2, 10)
        self.assertEqual(len(transaction_threads), 1)
        self.assertNotEqual(transaction_threads[0], main_thread)

    async def test_daily_claim_runs_atomically_off_the_event_loop(self):
        main_thread = threading.get_ident()
        transaction_threads = []

        claim_arguments = []

        def claim(*args):
            transaction_threads.append(threading.get_ident())
            claim_arguments.append(args)
            return {"claimed": True, "amount": 30, "streak": 4}

        interaction = fake_interaction()
        with patch("cogs.casino.currency_views.bank.claim_daily", side_effect=claim) as mocked:
            await CurrencyHubLayout.daily_btn(SimpleNamespace(), interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        interaction.followup.send.assert_awaited_once()
        mocked.assert_called_once()
        self.assertGreaterEqual(claim_arguments[0][2], DAILY_REWARD_MIN)
        self.assertLessEqual(claim_arguments[0][2], DAILY_REWARD_MAX)
        self.assertEqual(len(transaction_threads), 1)
        self.assertNotEqual(transaction_threads[0], main_thread)

    async def test_inventory_snapshot_load_runs_in_worker_thread(self):
        main_thread = threading.get_ident()
        snapshot_threads = []
        owner = SimpleNamespace(id=101, display_name="Test player")

        def load_snapshot(_cog, user_id):
            snapshot_threads.append(threading.get_ident())
            return {"loot_box": 1}, 123

        cog = SimpleNamespace(get_inventory=Mock(), get_balance=Mock(), get_item_count=Mock())
        with patch("cogs.casino.currency_views._load_inventory_snapshot", side_effect=load_snapshot):
            view = await build_inventory_view(cog, owner)

        self.assertEqual(view.inventory, {"loot_box": 1})
        self.assertEqual(view.balance, 123)
        self.assertEqual(len(snapshot_threads), 1)
        self.assertNotEqual(snapshot_threads[0], main_thread)
        cog.get_inventory.assert_not_called()
        cog.get_balance.assert_not_called()

    async def test_profile_snapshot_load_runs_in_worker_thread(self):
        main_thread = threading.get_ident()
        snapshot_threads = []
        user = SimpleNamespace(id=101, display_name="Test player")

        def load_snapshot(_cog, user_id):
            snapshot_threads.append(threading.get_ident())
            return 123, 4, {"luck_ticket": 2}

        cog = SimpleNamespace()
        with patch("cogs.casino.currency_views._load_profile_snapshot", side_effect=load_snapshot):
            panel = await build_profile_panel(cog, user)

        self.assertIsNotNone(panel)
        self.assertEqual(len(snapshot_threads), 1)
        self.assertNotEqual(snapshot_threads[0], main_thread)

    async def test_shop_and_hub_balance_loads_run_in_worker_threads(self):
        main_thread = threading.get_ident()
        read_threads = []

        def get_balance(user_id):
            read_threads.append(threading.get_ident())
            return 300

        owner = SimpleNamespace(id=101, display_name="Test player")
        cog = SimpleNamespace(get_balance=get_balance)
        shop = await build_shop_view(cog, owner)
        hub = await build_currency_hub_view(cog, owner)

        self.assertIn("300", "\n".join(
            item.content for item in shop.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ))
        self.assertIn("300", "\n".join(
            item.content for item in hub.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ))
        self.assertEqual(len(read_threads), 2)
        self.assertTrue(all(thread_id != main_thread for thread_id in read_threads))

    async def test_uncached_recipient_lookup_happens_after_transfer_acknowledgement(self):
        sequence = []
        recipient = SimpleNamespace(id=12345678901234567, bot=False, mention="<@12345678901234567>")

        async def acknowledge(**_kwargs):
            sequence.append("defer")

        async def fetch_member(_user_id):
            sequence.append("fetch_member")
            return recipient

        interaction = fake_interaction(guild=SimpleNamespace(
            get_member=Mock(return_value=None),
            fetch_member=AsyncMock(side_effect=fetch_member),
        ))
        interaction.response.defer = AsyncMock(side_effect=acknowledge)
        modal = SimpleNamespace(
            target=SimpleNamespace(value="<@12345678901234567>"),
            amount=SimpleNamespace(value="25"),
            cog=SimpleNamespace(log_action=AsyncMock()),
        )

        def transfer(*_args):
            sequence.append("transfer")
            return True, 975

        with patch("cogs.casino.currency_views.bank.transfer_balance", side_effect=transfer):
            await TransferModal.on_submit(modal, interaction)

        self.assertEqual(sequence, ["defer", "fetch_member", "transfer"])
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        interaction.followup.send.assert_awaited_once()

    async def test_currency_transfer_runs_atomically_off_the_event_loop(self):
        main_thread = threading.get_ident()
        transaction_threads = []
        recipient = SimpleNamespace(id=12345678901234567, bot=False, mention="<@12345678901234567>")
        guild = SimpleNamespace(get_member=Mock(return_value=recipient), fetch_member=AsyncMock())
        interaction = fake_interaction(guild=guild)
        modal = SimpleNamespace(
            target=SimpleNamespace(value="<@12345678901234567>"),
            amount=SimpleNamespace(value="25"),
            cog=SimpleNamespace(log_action=AsyncMock()),
        )

        def transfer(*_args):
            transaction_threads.append(threading.get_ident())
            return True, 975

        with patch("cogs.casino.currency_views.bank.transfer_balance", side_effect=transfer) as mocked:
            await TransferModal.on_submit(modal, interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        interaction.followup.send.assert_awaited_once()
        mocked.assert_called_once_with(101, 12345678901234567, 25)
        modal.cog.log_action.assert_awaited_once()
        self.assertEqual(len(transaction_threads), 1)
        self.assertNotEqual(transaction_threads[0], main_thread)


if __name__ == "__main__":
    unittest.main()
