"""Ensure high-contention economy I/O never blocks Discord's event loop."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cogs.casino.currency_views import BuyForm, CurrencyHubLayout, TransferModal


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

        def claim(*_args):
            transaction_threads.append(threading.get_ident())
            return {"claimed": True, "amount": 30, "streak": 4}

        interaction = fake_interaction()
        with patch("cogs.casino.currency_views.bank.claim_daily", side_effect=claim) as mocked:
            await CurrencyHubLayout.daily_btn(SimpleNamespace(), interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        interaction.followup.send.assert_awaited_once()
        mocked.assert_called_once()
        self.assertEqual(len(transaction_threads), 1)
        self.assertNotEqual(transaction_threads[0], main_thread)

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
