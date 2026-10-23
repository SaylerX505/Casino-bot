"""Regression tests for correlated, rotating diagnostics."""

import asyncio
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord.ui import LayoutView

from cogs.utility.diagnostics import (
    DiagnosticViewMixin,
    JsonLogFormatter,
    configure_file_logging,
    install_asyncio_exception_handler,
    log_unhandled_exception,
    new_incident_id,
    unwrap_exception,
)


class DiagnosticsTests(unittest.TestCase):
    def test_incident_ids_are_short_and_unique(self):
        ids = {new_incident_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)
        self.assertTrue(all(len(value) == 10 and value.isupper() for value in ids))

    def test_wrapped_exceptions_unwrap_without_looping(self):
        original = ValueError("sensitive detail")
        wrapper = RuntimeError("wrapper")
        wrapper.original = original
        self.assertIs(unwrap_exception(wrapper), original)

        cycle_a = RuntimeError("cycle a")
        cycle_b = RuntimeError("cycle b")
        cycle_a.original = cycle_b
        cycle_b.original = cycle_a
        self.assertIn(unwrap_exception(cycle_a), (cycle_a, cycle_b))

    def test_unhandled_log_has_correlated_metadata_and_traceback(self):
        logger = logging.getLogger("casino.tests.diagnostics")
        previous_level = logger.level
        logger.setLevel(logging.ERROR)
        error = RuntimeError("private exception detail")
        try:
            try:
                raise error
            except RuntimeError as caught:
                result = log_unhandled_exception(
                    logger,
                    incident_id="AB12CD34EF",
                    surface="slash",
                    command_name="casino.coinflip",
                    user_id=101,
                    guild_id=202,
                    channel_id=303,
                    error=caught,
                )
            self.assertIs(result, error)
        finally:
            logger.setLevel(previous_level)

    def test_json_formatter_emits_structured_context_and_traceback(self):
        logger = logging.getLogger("casino.tests.formatter")
        try:
            raise ValueError("diagnostic detail")
        except ValueError as error:
            record = logger.makeRecord(
                logger.name,
                logging.ERROR,
                __file__,
                44,
                "Unhandled incident %s",
                ("AB12CD34EF",),
                (type(error), error, error.__traceback__),
                extra={
                    "incident_id": "AB12CD34EF",
                    "surface": "prefix",
                    "command_name": "coinflip",
                    "guild_id": 202,
                },
            )
        payload = json.loads(JsonLogFormatter().format(record))
        self.assertEqual(payload["incident_id"], "AB12CD34EF")
        self.assertEqual(payload["surface"], "prefix")
        self.assertEqual(payload["command_name"], "coinflip")
        self.assertEqual(payload["guild_id"], 202)
        self.assertIn("ValueError: diagnostic detail", payload["exception"])

    def test_file_logging_is_json_rotated_idempotent_and_removable(self):
        root = logging.getLogger()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logs" / "bot.jsonl"
            handler = configure_file_logging(path, max_bytes=550, backup_count=2)
            self.assertIsNotNone(handler)
            self.addCleanup(lambda: root.removeHandler(handler) if handler in root.handlers else None)
            self.addCleanup(handler.close)

            self.assertIs(configure_file_logging(path, max_bytes=550, backup_count=2), handler)
            logger = logging.getLogger("casino.tests.file")
            old_level = logger.level
            logger.setLevel(logging.INFO)
            try:
                for index in range(10):
                    logger.info(
                        "test event %s",
                        index,
                        extra={"incident_id": f"INCIDENT{index}", "surface": "test"},
                    )
            finally:
                logger.setLevel(old_level)
                handler.flush()

            self.assertTrue(path.exists())
            self.assertTrue(Path(f"{path}.1").exists())
            current_payloads = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(current_payloads)
            self.assertIn("message", current_payloads[-1])
            self.assertIn("timestamp", current_payloads[-1])

    async def test_component_error_logs_incident_runs_recovery_and_acknowledges_privately(self):
        class RecoverableView(DiagnosticViewMixin, LayoutView):
            def __init__(self):
                super().__init__(timeout=30)
                self.recover = AsyncMock()

            async def _recover_after_component_error(self, incident_id):
                await self.recover(incident_id)

        response = SimpleNamespace(is_done=Mock(return_value=False), send_message=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=101),
            guild_id=202,
            channel_id=303,
            client=SimpleNamespace(dispatch=Mock()),
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )
        view = RecoverableView()
        try:
            with self.assertLogs("casino.components", level="ERROR") as captured:
                await view.on_error(interaction, RuntimeError("private component fault"), item=None)

            response.send_message.assert_awaited_once()
            kwargs = response.send_message.await_args.kwargs
            self.assertTrue(kwargs["ephemeral"])
            self.assertIsInstance(kwargs["view"], StatusPanel)
            panel_text = "\n".join(
                item.content
                for item in kwargs["view"].walk_children()
                if isinstance(item, discord.ui.TextDisplay)
            )
            self.assertNotIn("private component fault", panel_text)
            self.assertIn("رقم المتابعة", panel_text)
            incident_id = captured.output[0].split("incident_id=", 1)[1].split()[0]
            view.recover.assert_awaited_once_with(incident_id)
            interaction.client.dispatch.assert_called_once_with(
                "casino_component_error",
                "component:RecoverableView",
                101,
                202,
                303,
                "RuntimeError",
                incident_id,
            )
        finally:
            view.stop()

    def test_asyncio_exception_handler_logs_incident_context(self):
        loop = asyncio.new_event_loop()
        previous_handler = loop.get_exception_handler()
        install_asyncio_exception_handler(loop)
        handler = loop.get_exception_handler()
        self.assertIsNotNone(handler)
        self.assertIsNot(handler, previous_handler)
        with self.assertLogs("casino.asyncio", level="ERROR") as captured:
            loop.call_exception_handler(
                {
                    "message": "Task exception was never retrieved",
                    "exception": RuntimeError("async failure"),
                    "task": asyncio.current_task(loop=loop) if False else None,
                }
            )
        self.assertIn("incident_id=", captured.output[0])
        self.assertIn("RuntimeError", captured.output[0])
        loop.close()


if __name__ == "__main__":
    unittest.main()
