# coding=utf-8
# Filters that let selected bots deliver messages through ETM.
#
# Telegram Bot API 10.0 allows bots to receive messages from other bots in
# groups. ETM still only accepts messages from ``admins`` by default; this
# module builds a narrow exception for bots listed in ``trusted_bots``.

from typing import Iterable, List, Optional

from telegram.ext import Filters
from telegram.ext.filters import BaseFilter


def normalize_id_list(value, field_name: str) -> List[int]:
    """Normalize an int / digit string / list config value into ``List[int]``."""
    if value is None:
        return []
    if isinstance(value, (int, str)):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("{field} must be a list of numeric IDs.".format(field=field_name))
    result: List[int] = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError("{field} contains an invalid ID: {item}".format(field=field_name, item=item))
        if isinstance(item, str) and item.lstrip("-").isdigit():
            item = int(item)
        if not isinstance(item, int):
            raise ValueError("{field} contains an invalid ID: {item}".format(field=field_name, item=item))
        result.append(item)
    return result


def build_trusted_bot_filter(bot_ids: Iterable[int],
                             chat_ids: Optional[Iterable[int]] = None) -> Optional[BaseFilter]:
    """Build a filter matching plain new messages sent by trusted bots.

    Commands and edits are never accepted from trusted bots, so a bot can
    only relay ordinary messages and cannot manage ETM (e.g. /link, /rm).
    """
    bot_ids = list(bot_ids)
    if not bot_ids:
        return None
    trusted = Filters.user(user_id=bot_ids) & Filters.update.message & ~Filters.command
    chat_ids = list(chat_ids or [])
    if chat_ids:
        trusted = trusted & Filters.chat(chat_id=chat_ids)
    return trusted
