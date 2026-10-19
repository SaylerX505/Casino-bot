"""Race-safe registry for game lobbies and active game tasks."""


def _registry(bot):
    games = getattr(bot, "active_games", None)
    if games is None:
        games = {}
        bot.active_games = games
    return games


def release_game_task(bot, channel_id, task=None):
    """Remove a registry entry only if it still belongs to task."""
    games = _registry(bot)
    entry = games.get(channel_id)
    if entry is None:
        return False
    if task is not None and entry.get("task") is not task:
        return False
    games.pop(channel_id, None)
    return True


def is_active_game(bot, channel_id):
    """Return whether a non-finished lobby/game task owns this channel."""
    games = _registry(bot)
    entry = games.get(channel_id)
    if entry is None:
        return False
    task = entry.get("task")
    if task is not None and not task.done():
        return True
    if games.get(channel_id) is entry:
        games.pop(channel_id, None)
    return False


def owns_game_task(bot, channel_id, task):
    """Whether the given task currently owns the channel registry entry."""
    return _registry(bot).get(channel_id, {}).get("task") is task


def set_game_view(bot, channel_id, task, view):
    """Update the active view only for its owning task."""
    entry = _registry(bot).get(channel_id)
    if entry is None or entry.get("task") is not task:
        return False
    entry["view"] = view
    return True


def set_game_message(bot, channel_id, task, message):
    """Update the active message only for its owning task."""
    entry = _registry(bot).get(channel_id)
    if entry is None or entry.get("task") is not task:
        return False
    entry["message"] = message
    return True


def register_game_task(bot, channel_id, task, host_id, name, *, replace_task=None, view=None):
    """Register a lobby or promote its lobby task to the actual game task.

    Completion callbacks remove only the entry created for that exact task,
    so older task cleanup cannot erase the state of a newer game.
    """
    games = _registry(bot)
    current = games.get(channel_id)
    if replace_task is None:
        if current is not None:
            current_task = current.get("task")
            if current_task is not None and not current_task.done():
                return False
            if games.get(channel_id) is current:
                games.pop(channel_id, None)
    elif current is None or current.get("task") is not replace_task:
        return False

    entry = {"task": task, "host_id": host_id, "name": name}
    if view is not None:
        entry["view"] = view
    elif replace_task is not None and current is not None and current.get("view") is not None:
        entry["view"] = current["view"]
    games[channel_id] = entry
    task.add_done_callback(
        lambda completed: release_game_task(bot, channel_id, completed)
    )
    return True
