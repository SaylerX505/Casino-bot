"""Privacy-aware diagnostics shared by command and asyncio error handlers."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any


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
    """Log one full local traceback with stable, searchable, non-secret context."""
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
    )
    return original


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
                except Exception:
                    task_name = type(future).__name__
            else:
                task_name = type(future).__name__

        message = str(context.get("message") or "Unobserved asyncio failure")
        if isinstance(exception, BaseException):
            original = unwrap_exception(exception)
            logger.error(
                "Unhandled asyncio failure incident_id=%s task=%s message=%s error_type=%s",
                incident_id,
                task_name,
                message,
                type(original).__name__,
                exc_info=(type(original), original, original.__traceback__),
            )
        else:
            logger.error(
                "Unhandled asyncio failure incident_id=%s task=%s message=%s",
                incident_id,
                task_name,
                message,
            )

    loop.set_exception_handler(handle)
