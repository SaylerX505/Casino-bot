"""Regression coverage for the riddle game and shared Components V2 status style."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, mock_open, patch

import discord
from discord.ui import LayoutView

from cogs.casino import bank
from cogs.casino.currency_views import (
    CurrencyHubLayout,
    InventoryView,
    ShopView,
    build_currency_hub_view,
    build_inventory_view,
    build_profile_panel,
    build_shop_view,
    render_profile_panel,
)
from cogs.casino.fazoora import (
    DEFAULT_QUESTIONS,
    credit_riddle_reward,
    load_bank,
    normalize,
    publish_riddle_result,
)
from cogs.utility.theme import (
    StatusPanel,
    base_embed,
    embed_to_v2,
    send_status,
)


class RiddleInputTests(unittest.TestCase):
    def test_arabic_answer_matching_ignores_tashkeel_punctuation_and_extra_spacing(self):
        self.assertEqual(normalize("  الحُفْرَة؟   "), normalize("الحفرة"))
        self.assertEqual(normalize("إجابةٌ صحيحة!"), normalize("اجابه صحيحه"))

    def test_empty_bank_falls_back_to_a_usable_question(self):
        with patch("builtins.open", mock_open(read_data=json.dumps({"فزورة": []}, ensure_ascii=False))):
            self.assertEqual(load_bank(), DEFAULT_QUESTIONS)

    def test_malformed_rows_are_filtered_without_disabling_the_game(self):
        payload = {
            "فزورة": [
                None,
                {"q": "", "a": ["answer"]},
                {"q": "سؤال غير صالح", "a": []},
                {"q": "سؤال صالح؟", "a": ["إجابة!"]},
            ]
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(payload, ensure_ascii=False))):
            self.assertEqual(load_bank(), [{"q": "سؤال صالح؟", "a": ["إجابة!"]}])


class RiddleResultMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_is_a_new_v2_message_instead_of_replacing_the_question(self):
        ctx = SimpleNamespace(
            channel=SimpleNamespace(id=123),
            send=AsyncMock(),
        )
        panel = StatusPanel("انتهى الوقت", "الإجابة الصحيحة هي الحفرة.", "loss")

        published = await publish_riddle_result(ctx, panel, context="test")

        self.assertTrue(published)
        ctx.send.assert_awaited_once()
        kwargs = ctx.send.await_args.kwargs
        self.assertIs(kwargs["view"], panel)
        self.assertIsInstance(kwargs["view"], LayoutView)
        allowed_mentions = kwargs["allowed_mentions"]
        self.assertIs(allowed_mentions.everyone, False)
        self.assertIs(allowed_mentions.users, False)
        self.assertIs(allowed_mentions.roles, False)
        self.assertIs(allowed_mentions.replied_user, False)


class RiddleRewardThreadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_reward_write_is_dispatched_off_the_event_loop(self):
        currency = SimpleNamespace(update_balance=Mock())
        with patch(
            "cogs.casino.fazoora.asyncio.to_thread",
            new=AsyncMock(return_value=130),
        ) as to_thread:
            result = await credit_riddle_reward(currency, 101)

        self.assertEqual(result, 130)
        to_thread.assert_awaited_once_with(currency.update_balance, 101, 30)


class EconomyHubUXTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=101, display_name="Test member")
        self.cog = SimpleNamespace(
            get_balance=lambda user_id: 250,
            get_inventory=lambda user_id: {"loot_box": 1},
        )

    def test_hub_has_direct_inventory_and_profile_actions(self):
        view = CurrencyHubLayout(self.cog, self.user, balance=250)
        self.assertIsInstance(view, LayoutView)
        buttons = [
            item for item in view.walk_children()
            if isinstance(item, discord.ui.Button)
        ]
        custom_ids = {button.custom_id for button in buttons}
        self.assertTrue({"hubv2_inventory", "hubv2_profile"}.issubset(custom_ids))
        self.assertTrue(all(button.style is discord.ButtonStyle.secondary for button in buttons))

    def test_command_and_hub_can_share_pure_profile_renderer(self):
        panel = render_profile_panel(self.user, 250, 4, {"loot_box": 1})
        self.assertIsInstance(panel, LayoutView)
        content = "\n".join(
            item.content
            for item in panel.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("الرصيد", content)
        self.assertIn("4 يوم", content)
        self.assertIn("الأغراض", content)


    def test_shop_shows_the_requesters_current_balance(self):
        view = ShopView(self.cog, self.user, balance=250)
        content = "\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("رصيدك الحالي", content)
        self.assertIn("250", content)


class HubInventoryButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_inventory_button_tracks_the_ephemeral_message(self):
        owner = SimpleNamespace(id=9, display_name="Tester")
        cog = SimpleNamespace(
            get_balance=lambda _user_id: 100,
            get_inventory=lambda _user_id: {},
            get_item_count=lambda _user_id, _item_id: 0,
        )
        hub = CurrencyHubLayout(cog, owner, balance=100)
        message = object()
        response = SimpleNamespace(
            defer=AsyncMock(),
            is_done=Mock(return_value=True),
            send_message=AsyncMock(),
        )
        interaction = SimpleNamespace(
            user=owner,
            response=response,
            edit_original_response=AsyncMock(),
            original_response=AsyncMock(return_value=message),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await hub.inventory_btn(interaction)

        response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        view = interaction.edit_original_response.await_args.kwargs["view"]
        self.assertIsInstance(view, InventoryView)
        self.assertIs(view.message, message)


class InventoryViewLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_category_change_updates_the_same_view_instance(self):
        owner = SimpleNamespace(id=7, display_name="Tester")
        cog = SimpleNamespace(
            get_balance=lambda _user_id: 100,
            get_inventory=lambda _user_id: {"double_shot": 1, "loot_box": 0, "luck_ticket": 0},
            get_item_count=lambda _user_id, _item_id: 0,
        )
        view = InventoryView(cog, owner, inventory={"double_shot": 1}, balance=100)
        view.category_select._values = ["tools"]
        response = SimpleNamespace(edit_message=AsyncMock())
        interaction = SimpleNamespace(response=response)

        await view.select_category(interaction)

        self.assertEqual(view.category, "tools")
        response.edit_message.assert_awaited_once_with(view=view)
        self.assertIn("أدوات الألعاب", view.category_select.options[1].label)


class ShopTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_shop_disables_controls_and_edits_the_message(self):
        user = SimpleNamespace(id=7)
        cog = SimpleNamespace(get_balance=lambda _user_id: 50)
        view = ShopView(cog, user, balance=50)
        view.message = SimpleNamespace(edit=AsyncMock())

        await view.on_timeout()

        controls = [item for item in view.walk_children() if hasattr(item, "disabled")]
        self.assertTrue(controls)
        self.assertTrue(all(item.disabled for item in controls))
        view.message.edit.assert_awaited_once_with(view=view)


class EconomySnapshotBuilderTests(unittest.IsolatedAsyncioTestCase):
    async def test_personalized_views_require_database_snapshots_from_worker_threads(self):
        user = SimpleNamespace(id=41, display_name="Snapshot User")
        cog = SimpleNamespace(
            get_balance=Mock(),
            get_inventory=Mock(),
        )
        inventory = {"loot_box": 1}
        snapshot = (inventory, 250)

        with patch(
            "cogs.casino.currency_views.asyncio.to_thread",
            new=AsyncMock(return_value=250),
        ) as to_thread:
            shop = await build_shop_view(cog, user)
        self.assertIsInstance(shop, ShopView)
        self.assertIn("250", "\n".join(
            item.content for item in shop.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ))
        to_thread.assert_awaited_once_with(cog.get_balance, user.id)

        with patch(
            "cogs.casino.currency_views.asyncio.to_thread",
            new=AsyncMock(return_value=snapshot),
        ) as to_thread:
            inventory_view = await build_inventory_view(cog, user)
        self.assertIsInstance(inventory_view, InventoryView)
        self.assertEqual(inventory_view.inventory, inventory)
        self.assertEqual(inventory_view.balance, 250)
        to_thread.assert_awaited_once()

        with patch(
            "cogs.casino.currency_views.asyncio.to_thread",
            new=AsyncMock(return_value=(250, 4, inventory)),
        ) as to_thread:
            profile = await build_profile_panel(cog, user)
        self.assertIsInstance(profile, LayoutView)
        to_thread.assert_awaited_once()

        with patch(
            "cogs.casino.currency_views.asyncio.to_thread",
            new=AsyncMock(return_value=250),
        ) as to_thread:
            hub = await build_currency_hub_view(cog, user)
        self.assertIsInstance(hub, CurrencyHubLayout)
        to_thread.assert_awaited_once_with(cog.get_balance, user.id)

    def test_personalized_view_constructors_reject_missing_snapshots(self):
        user = SimpleNamespace(id=5, display_name="No Snapshot")
        cog = SimpleNamespace(get_balance=Mock(), get_inventory=Mock())
        with self.assertRaisesRegex(ValueError, "balance snapshot"):
            ShopView(cog, user)
        with self.assertRaisesRegex(ValueError, "inventory/balance snapshot"):
            InventoryView(cog, user)
        with self.assertRaisesRegex(ValueError, "balance snapshot"):
            CurrencyHubLayout(cog, user)
        cog.get_balance.assert_not_called()
        cog.get_inventory.assert_not_called()


class EconomyInteractionThreadingTests(unittest.IsolatedAsyncioTestCase):
    def make_interaction(self, user_id=77):
        user = SimpleNamespace(id=user_id, display_name="Tester")
        interaction = SimpleNamespace(
            user=user,
            response=SimpleNamespace(
                defer=AsyncMock(),
                send_message=AsyncMock(),
                is_done=Mock(return_value=True),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        return user, interaction

    async def test_daily_claim_runs_off_the_event_loop_and_returns_a_v2_receipt(self):
        user, interaction = self.make_interaction()
        claim = {"claimed": True, "amount": 35, "streak": 3}
        hub = CurrencyHubLayout(SimpleNamespace(), requester=user, balance=100)

        with (
            patch("cogs.casino.currency_views.bank.now", return_value=123456),
            patch("cogs.casino.currency_views.random.randint", return_value=35),
            patch("cogs.casino.currency_views.asyncio.to_thread", new=AsyncMock(return_value=claim)) as to_thread,
        ):
            await hub.daily_btn(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        to_thread.assert_awaited_once_with(
            bank.claim_daily, user.id, 123456, 35
        )
        interaction.followup.send.assert_awaited_once()
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertIn("35", "\n".join(
            item.content for item in kwargs["view"].walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ))

    async def test_leaderboard_query_runs_off_the_event_loop_and_is_private_v2(self):
        user, interaction = self.make_interaction()
        hub = CurrencyHubLayout(SimpleNamespace(), requester=user, balance=100)
        with patch(
            "cogs.casino.currency_views.asyncio.to_thread",
            new=AsyncMock(return_value=[(88, 900), (99, 700)]),
        ) as to_thread:
            await hub.top_btn(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        to_thread.assert_awaited_once_with(bank.top_balances, 10)
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIsInstance(kwargs["view"], StatusPanel)
        text = "\n".join(
            item.content for item in kwargs["view"].walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("**#1** <@88>", text)
        self.assertIn("**#2** <@99>", text)
        self.assertFalse(kwargs.get("allowed_mentions", discord.AllowedMentions.none()).users)


class SharedStatusPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefix_status_is_visible_and_uses_components_v2(self):
        ctx = SimpleNamespace(interaction=None, send=AsyncMock())

        await send_status(ctx, "الروم مشغول", "فيه لعبة شغالة.", "warning")

        ctx.send.assert_awaited_once()
        kwargs = ctx.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertNotIn("ephemeral", kwargs)

    async def test_slash_status_is_ephemeral_and_uses_components_v2(self):
        ctx = SimpleNamespace(interaction=object(), send=AsyncMock())

        await send_status(ctx, "الروم مشغول", "فيه لعبة شغالة.", "warning")

        ctx.send.assert_awaited_once()
        kwargs = ctx.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], StatusPanel)
        self.assertTrue(kwargs["ephemeral"])

    def test_base_panels_have_clean_footer_and_v2_conversion(self):
        legacy = base_embed(title="اختبار", description="محتوى")
        self.assertNotIn("Developed by", legacy.footer.text)
        self.assertIsInstance(embed_to_v2(legacy), LayoutView)


    def test_status_panel_places_footer_after_the_content_separator(self):
        panel = StatusPanel("الحالة", "تفاصيل الحالة", "success")
        items = [
            item
            for item in panel.walk_children()
            if isinstance(item, (discord.ui.TextDisplay, discord.ui.Separator))
        ]
        self.assertEqual(len(items), 3)
        self.assertIsInstance(items[0], discord.ui.TextDisplay)
        self.assertIn("تفاصيل الحالة", items[0].content)
        self.assertIsInstance(items[1], discord.ui.Separator)
        self.assertIsInstance(items[2], discord.ui.TextDisplay)
        self.assertIn("كازينو الحفرة", items[2].content)


if __name__ == "__main__":
    unittest.main()
