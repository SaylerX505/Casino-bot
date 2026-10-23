"""Privacy-aware, correlated diagnostics and bounded local log persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

DEFAULT_LOG_PATH = "data/logs/casino-bot.log"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


class JsonLogFormatter(logging.Formatter):
    """Serialize each local log record as one searchable JSON line."""

    _CONTEXT_FIELDS = (
        "incident_id",
        "surface",
        "command_name",
        "user_id",
        "guild_id",
        "channel_id",
        "error_type",
        "task_name",
        "event_name",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process": record.process,
        }
        for field in self._CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_file_logging(
    log_file_path: str | os.PathLike[str] | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> RotatingFileHandler | None:
    """Attach a rotating JSON log file to the root logger, without duplicate handlers.

    The file is private local diagnostics: it may contain exception tracebacks. It must
    live on private persistent storage and never be forwarded verbatim to Discord.
    Console logging remains available if the file cannot be opened.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if isinstance(backup_count, bool) or not isinstance(backup_count, int) or backup_count < 0:
        raise ValueError("backup_count must be a non-negative integer")

    configured_path = (
        str(log_file_path)
        if log_file_path is not None
        else os.getenv("CASINO_LOG_FILE", DEFAULT_LOG_PATH)
    )
    if configured_path.strip().lower() in {"", "off", "disabled", "none", "0"}:
        return None

    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        project_root = Path(__file__).resolve().parents[2]
        path = project_root / path
    path = path.resolve()

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if getattr(handler, "_casino_log_file_path", None) == str(path):
            return handler if isinstance(handler, RotatingFileHandler) else None

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
        )
        handler.setLevel(root_logger.getEffectiveLevel())
        handler.setFormatter(JsonLogFormatter())
        handler._casino_log_file_path = str(path)
        root_logger.addHandler(handler)
        logging.getLogger("casino.logging").info(
            "Rotating diagnostics enabled path=%s max_bytes=%s backups=%s",
            path,
            max_bytes,
            backup_count,
        )
        return handler
    except (OSError, ValueError):
        logging.getLogger("casino.logging").exception(
            "Could not configure rotating diagnostics; console logging remains active"
        )
        return None


def new_incident_id() -> str:
    """Return a short, non-sensitive correlation ID for one failure."""
    return uuid.uuid4().hex[:10].upper()


def unwrap_exception(error: BaseException) -> BaseException:
    """Unwrap Discord command wrappers while avoiding malformed exception cycles."""
    current = error
    visited: set[int] = set()
    while id(current) not in visited:
        visited.add(id(current))
        original = getattr(current, "original", None)
        if not isinstance(original, BaseException) or original is current:
            break
        current = original
    return current


def log_unhandled_exception(
    logger: logging.Logger,
    *,
    incident_id: str,
    surface: str,
    command_name: str,
    user_id: int | str | None,
    guild_id: int | str | None,
    channel_id: int | str | None,
    error: BaseException,
) -> BaseException:
    """Log a traceback locally with correlated metadata, never to the user's response."""
    original = unwrap_exception(error)
    logger.error(
        "Unhandled failure incident_id=%s surface=%s command=%s user_id=%s "
        "guild_id=%s channel_id=%s error_type=%s",
        incident_id,
        surface,
        command_name,
        user_id if user_id is not None else "unknown",
        guild_id if guild_id is not None else "unknown",
        channel_id if channel_id is not None else "unknown",
        type(original).__name__,
        exc_info=(type(original), original, original.__traceback__),
        extra={
            "incident_id": incident_id,
            "surface": surface,
            "command_name": command_name,
            "user_id": user_id,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "error_type": type(original).__name__,
        },
    )
    return original


class DiagnosticViewMixin:
    """Shared diagnostics for Discord UI callback failures."""

    async def on_error(self, interaction, error: BaseException, item) -> None:
        """Correlate UI failures, invoke game-specific recovery, and acknowledge privately."""
        logger = logging.getLogger("casino.components")
        user_id = getattr(getattr(interaction, "user", None), "id", None)
        guild_id = getattr(interaction, "guild_id", None)
        channel_id = getattr(interaction, "channel_id", None)
        view_name = (
            getattr(self, "game_type", None)
            or getattr(self, "game_title", None)
            or type(self).__name__
        )
        command_name = f"component:{view_name}"
        incident_id = new_incident_id()
        original = log_unhandled_exception(
            logger,
            incident_id=incident_id,
            surface="component",
            command_name=command_name,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            error=error,
        )

        client = getattr(interaction, "client", None)
        dispatch = getattr(client, "dispatch", None)
        if callable(dispatch):
            try:
                dispatch(
                    "casino_component_error",
                    command_name,
                    user_id if user_id is not None else "unknown",
                    guild_id,
                    channel_id,
                    type(original).__name__,
                    incident_id,
                )
            except Exception:
                logger.exception(
                    "Could not dispatch component incident incident_id=%s",
                    incident_id,
                )

        recovery = getattr(self, "_recover_after_component_error", None)
        if callable(recovery):
            try:
                await recovery(incident_id)
            except asyncio.CancelledError:
                raise
            except Exception as recovery_error:
                log_unhandled_exception(
                    logger,
                    incident_id=incident_id,
                    surface="component_recovery",
                    command_name=command_name,
                    user_id=user_id,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    error=recovery_error,
                )

        try:
            from cogs.utility.theme import StatusPanel

            response = getattr(interaction, "response", None)
            is_done = getattr(response, "is_done", None)
            panel = StatusPanel(
                "حصل عطل في التفاعل",
                f"اتسجل رقم المتابعة **{incident_id}**. "
                "هنراجع حالة الجولة بأمان؛ متبدأش محاولة تانية من نفس اللوحة.",
                "warning",
            )
            if response is not None and callable(is_done) and not is_done():
                await response.send_message(view=panel, ephemeral=True)
            else:
                await interaction.followup.send(view=panel, ephemeral=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Could not acknowledge component incident incident_id=%s",
                incident_id,
                exc_info=True,
            )


def install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
    """Report otherwise-unobserved task/future failures with a searchable incident ID."""
    logger = logging.getLogger("casino.asyncio")

    def handle(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        incident_id = new_incident_id()
        exception = context.get("exception")
        future = context.get("task") or context.get("future")
        task_name = "unknown"
        if future is not None:
            get_name = getattr(future, "get_name", None)
            if callable(get_name):
                try:
                    task_name = str(get_name())
                except Exception:  # noqa: BLE001 - keep diagnostics alive if a custom Future accessor fails.
                    task_name = type(future).__name__
            else:
                task_name = type(future).__name__

        message = str(context.get("message") or "Unobserved asyncio failure")
        message = " ".join(message.split())[:300]
        common_extra = {
            "incident_id": incident_id,
            "surface": "asyncio",
            "task_name": task_name,
        }
        if isinstance(exception, BaseException):
            original = unwrap_exception(exception)
            logger.error(
                "Unhandled asyncio failure incident_id=%s task=%s message=%s error_type=%s",
                incident_id,
                task_name,
                message,
                type(original).__name__,
                exc_info=(type(original), original, original.__traceback__),
                extra={**common_extra, "error_type": type(original).__name__},
            )
        else:
            logger.error(
                "Unhandled asyncio failure incident_id=%s task=%s message=%s",
                incident_id,
                task_name,
                message,
                extra=common_extra,
            )

    loop.set_exception_handler(handle)
