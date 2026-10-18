"""Race-condition regression tests for the per-channel game registry."""
import asyncio
import unittest
from types import SimpleNamespace

from cogs.casino.game_registry import (
    is_active_game,
    register_game_task,
    release_game_task,
)


class GameRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = SimpleNamespace(active_games={})
        self.channel_id = 12345
        self.waiter = asyncio.Event()

    async def asyncTearDown(self):
        for entry in list(self.bot.active_games.values()):
            task = entry.get("task")
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await asyncio.sleep(0)

    async def test_one_lobby_owns_a_channel(self):
        first = asyncio.create_task(self.waiter.wait())
        second = asyncio.create_task(self.waiter.wait())
        self.assertTrue(register_game_task(self.bot, self.channel_id, first, 1, "Lobby"))
        self.assertFalse(register_game_task(self.bot, self.channel_id, second, 2, "Another Lobby"))
        self.assertTrue(is_active_game(self.bot, self.channel_id))
        self.assertIs(self.bot.active_games[self.channel_id]["task"], first)
        second.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second

    async def test_old_task_cannot_remove_new_game_entry(self):
        old = asyncio.create_task(self.waiter.wait())
        self.assertTrue(register_game_task(self.bot, self.channel_id, old, 1, "Lobby"))

        new = asyncio.create_task(self.waiter.wait())
        self.assertTrue(
            register_game_task(
                self.bot, self.channel_id, new, 1, "Game", replace_task=old
            )
        )
        self.assertFalse(release_game_task(self.bot, self.channel_id, old))
        self.assertIs(self.bot.active_games[self.channel_id]["task"], new)

        old.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await old
        await asyncio.sleep(0)
        self.assertIs(self.bot.active_games[self.channel_id]["task"], new)


if __name__ == "__main__":
    unittest.main()
