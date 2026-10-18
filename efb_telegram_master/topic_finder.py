# coding=utf-8
"""Find a linked chat and open its existing Telegram forum topic."""

import html
import logging
from typing import List, Optional, Tuple, TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

from .db import MsgLog, SlaveChatInfo, TopicAssoc
from .utils import message_id_str_to_id

if TYPE_CHECKING:
    from . import TelegramChannel


def normalize(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def match_score(name: Optional[str], alias: Optional[str], uid: Optional[str], keyword: str) -> int:
    """Return 0 for no match; a higher value means a better match."""
    keyword = normalize(keyword)
    if not keyword:
        return 0

    values = [normalize(name), normalize(alias), normalize(uid)]
    if keyword in values:
        return 4
    if any(value.startswith(keyword) for value in values if value):
        return 3
    if any(keyword in value for value in values if value):
        return 2
    return 0


def topic_link(topic_chat_id: str, message_thread_id: str, message_id: Optional[str] = None) -> Optional[str]:
    """Build a link that opens an existing forum topic or a message in it."""
    topic_chat_id = str(topic_chat_id)
    message_thread_id = str(message_thread_id)
    if not topic_chat_id or not message_thread_id or message_thread_id == "0":
        return None
    if topic_chat_id.startswith("-100"):
        topic_chat_id = topic_chat_id[4:]
    else:
        # Topics only exist in supergroups, whose IDs start with -100.
        return None
    base = "https://t.me/c/{}/{}".format(topic_chat_id, message_thread_id)
    if message_id:
        return "{}/{}".format(base, message_id)
    return base


def display_name(name: Optional[str], alias: Optional[str]) -> str:
    name = (name or "").strip()
    alias = (alias or "").strip()
    if alias and name and alias != name:
        return "{}（{}）".format(alias, name)
    return alias or name or "未命名会话"


class TopicFinder:
    """Search chat names and aliases, then jump to their existing topics."""

    MAX_RESULTS = 10

    def __init__(self, channel: "TelegramChannel"):
        self.channel = channel
        self.logger = logging.getLogger(__name__)

    def _topic_assoc(self, slave_uid: str) -> Optional[TopicAssoc]:
        query = TopicAssoc.select().where(TopicAssoc.slave_uid == slave_uid)
        if self.channel.topic_group:
            query = query.where(TopicAssoc.topic_chat_id == str(self.channel.topic_group))
        return query.order_by(TopicAssoc.id.desc()).first()

    def find(self, update: Update, context: CallbackContext):
        message = update.effective_message
        if message is None:
            return

        keyword = " ".join(context.args or []).strip()
        if not keyword:
            message.reply_text("用法：/find 姓名或备注")
            return

        matches: List[Tuple[int, str, str, str, str]] = []
        query = (
            SlaveChatInfo.select()
            .where(
                (SlaveChatInfo.slave_chat_group_id.is_null(True))
                & (SlaveChatInfo.slave_chat_type.in_(["Private", "Group"]))
            )
        )
        for row in query:
            score = match_score(
                row.slave_chat_name,
                row.slave_chat_alias,
                row.slave_chat_uid,
                keyword,
            )
            if not score:
                continue

            slave_uid = "{}.{}".format(row.slave_channel_id, row.slave_chat_uid)
            assoc = self._topic_assoc(slave_uid)
            if assoc is None:
                continue
            link = topic_link(assoc.topic_chat_id, assoc.message_thread_id)
            if link is None:
                continue

            matches.append(
                (
                    score,
                    display_name(row.slave_chat_name, row.slave_chat_alias),
                    link,
                    row.slave_chat_type,
                    slave_uid,
                )
            )

        if not matches:
            message.reply_text("没有找到匹配的会话。")
            return

        seen = set()
        selected = []
        for score, name, base_link, chat_type, slave_uid in sorted(matches, key=lambda item: (-item[0], item[1])):
            if base_link in seen:
                continue
            seen.add(base_link)
            selected.append((name, base_link, chat_type, slave_uid))
            if len(selected) >= self.MAX_RESULTS:
                break

        results = []
        for name, base_link, chat_type, slave_uid in selected:
            link = base_link
            latest = (
                MsgLog.select(MsgLog.master_msg_id)
                .where(MsgLog.slave_origin_uid == slave_uid)
                .order_by(MsgLog.time.desc())
                .limit(1)
                .first()
            )
            if latest:
                try:
                    _, message_id = message_id_str_to_id(latest.master_msg_id)
                    link = "{}/{}".format(base_link, message_id)
                except (IndexError, ValueError):
                    self.logger.warning("Cannot parse message ID %s", latest.master_msg_id)

            results.append((name, link, chat_type))

        lines = ["找到 {} 个会话：".format(len(results)), ""]
        buttons = []
        for index, (name, link, chat_type) in enumerate(results, 1):
            type_label = "群" if chat_type == "Group" else "私聊"
            lines.append(
                '{}. <b>{}</b> · {}\n<a href="{}">打开话题</a>'.format(
                    index, html.escape(name), type_label, link
                )
            )
            button_name = name if len(name) <= 40 else name[:39] + "…"
            buttons.append([InlineKeyboardButton("打开 {}".format(button_name), url=link)])

        message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True,
        )
