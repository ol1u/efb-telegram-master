# coding=utf-8

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pickle import UnpicklingError
from queue import Queue, Empty
from threading import Thread
from typing import Optional, TYPE_CHECKING, Tuple, Any, Dict, Set

import humanize
from telegram import Update, Message, Chat, TelegramError, Contact
from telegram.constants import MAX_FILESIZE_DOWNLOAD
from telegram.ext import MessageHandler, Filters, CallbackContext, CommandHandler
from telegram.utils.helpers import escape_markdown

from ehforwarderbot import coordinator
from ehforwarderbot.constants import MsgType
from ehforwarderbot.exceptions import EFBMessageTypeNotSupported, EFBChatNotFound, \
    EFBMessageError, EFBOperationNotSupported, EFBException
from ehforwarderbot.message import LocationAttribute
from ehforwarderbot.status import MessageRemoval
from ehforwarderbot.types import ModuleID, MessageID, ChatID
from . import utils
from .chat_destination_cache import ChatDestinationCache
from .locale_mixin import LocaleMixin
from .message import ETMMsg
from .msg_type import TGMsgType, get_msg_type
from .utils import EFBChannelChatIDStr, TelegramChatID, TelegramMessageID

if TYPE_CHECKING:
    from . import TelegramChannel
    from .bot_manager import TelegramBotManager
    from .db import DatabaseManager, MsgLog
    from .chat_object_cache import ChatObjectCacheManager


class MasterMessageProcessor(LocaleMixin):
    """
    Processes messages from Telegram user and delivers to the slave channels
    """

    DELETE_FLAG = 'rm`'
    TG_IMAGE_DOCUMENT_RE = re.compile(
        r"^tg_image_.+\.(?:jpe?g|png|tiff?|webp)$",
        re.IGNORECASE,
    )

    # Constants
    TYPE_DICT = {
        TGMsgType.Text: MsgType.Text,
        TGMsgType.Audio: MsgType.File,
        TGMsgType.Document: MsgType.File,
        TGMsgType.Photo: MsgType.Image,
        TGMsgType.Sticker: MsgType.Sticker,
        # TGMsgType.AnimatedSticker: MsgType.Animation,
        TGMsgType.VideoSticker: MsgType.Animation,
        TGMsgType.Video: MsgType.Video,
        TGMsgType.VideoNote: MsgType.Video,
        TGMsgType.Voice: MsgType.Voice,
        TGMsgType.Location: MsgType.Location,
        TGMsgType.Venue: MsgType.Location,
        TGMsgType.Animation: MsgType.Animation,
        TGMsgType.Contact: MsgType.Text,
        TGMsgType.Dice: MsgType.Text,
    }

    def __init__(self, channel: 'TelegramChannel'):
        self.channel: 'TelegramChannel' = channel
        self.bot: 'TelegramBotManager' = channel.bot_manager
        self.db: 'DatabaseManager' = channel.db
        self.chat_dest_cache: ChatDestinationCache = channel.chat_dest_cache
        self.chat_manager: 'ChatObjectCacheManager' = channel.chat_manager

        self.bot.dispatcher.add_handler(CommandHandler("rm", self.delete_message))

        self.bot.dispatcher.add_handler(MessageHandler(
            (Filters.text | Filters.photo | Filters.sticker | Filters.document |
             Filters.venue | Filters.location | Filters.audio | Filters.voice |
             Filters.video | Filters.contact | Filters.video_note | Filters.dice) &
            Filters.update,
            self.enqueue_message
        ))
        self.bot.dispatcher.add_handler(MessageHandler(
            (Filters.passport_data | Filters.invoice | Filters.game | Filters.successful_payment |
             Filters.poll) & Filters.update,
            self.unsupported_message
        ))
        self.logger: logging.Logger = logging.getLogger(__name__)

        self.channel_id: ModuleID = self.channel.channel_id

        if self.channel.flag("animated_stickers"):
            self.TYPE_DICT[TGMsgType.AnimatedSticker] = MsgType.Animation

        self.message_queue: 'Queue[Optional[Tuple[Update, CallbackContext]]]' = Queue()
        # 按聊天分队列:同一聊天的消息保证顺序处理,不同聊天可并发,
        # 避免单个大文件/慢消息卡住所有聊天的消息。
        self._chat_queues: Dict[int, 'Queue[Optional[Tuple[Update, CallbackContext]]]'] = {}
        self._chat_queues_lock = threading.Lock()
        self._chat_queues_cond = threading.Condition(self._chat_queues_lock)
        self._active_chats: Set[int] = set()  # 正在被 worker 处理的聊天
        self._worker_count = 4  # 并发 worker 数
        self.message_worker_thread = Thread(target=self._dispatch_loop, name="ETM dispatch thread")
        self.message_worker_thread.start()
        self._chat_worker_threads = [
            Thread(target=self._chat_worker_loop, name=f"ETM chat worker {i}")
            for i in range(self._worker_count)
        ]
        for t in self._chat_worker_threads:
            t.start()

        # slave 调用执行器:coordinator.send_message 同步调 slave,slave hanging
        # (如 hook 卡住)时不能卡死 worker 线程,故加超时隔离。
        self._slave_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="etm-slave-call")
        # 按消息类型分级超时:TG API 限制 20MB,绝大多数是文字/图片,小超时快失败;
        # 即时通讯场景下,等 120 秒才知道失败体验太差。
        self._slave_timeout_tiers = {
            MsgType.Text: 15,
            MsgType.Image: 30,
            MsgType.Sticker: 30,
            MsgType.Animation: 30,
            MsgType.Location: 15,
        }
        self._slave_timeout_default = 60  # 文件/视频/语音等,默认 60 秒

    def _dispatch_loop(self):
        """分发线程:从全局队列取消息,按 TG 聊天 ID 路由到对应的聊天队列。"""
        while True:
            content = self.message_queue.get()
            try:
                if content is None:
                    # 关闭信号:转发给所有聊天队列
                    with self._chat_queues_lock:
                        for q in self._chat_queues.values():
                            q.put(None)
                        self._chat_queues_cond.notify_all()
                    return
                update, _ = content
                chat = update.effective_chat
                chat_key = chat.id if chat is not None else 0
                with self._chat_queues_lock:
                    q = self._chat_queues.get(chat_key)
                    if q is None:
                        q = Queue()
                        self._chat_queues[chat_key] = q
                    q.put(content)
                    self._chat_queues_cond.notify()
            finally:
                self.message_queue.task_done()

    def _chat_worker_loop(self):
        """工作线程:认领一个有待处理消息的聊天队列,按顺序处理完再认领下一个。

        同一聊天同时只被一个 worker 处理,保证同聊天的消息顺序;
        不同聊天可由不同 worker 并发处理。
        """
        while True:
            with self._chat_queues_lock:
                target_key = None
                target_q = None
                for key, q in self._chat_queues.items():
                    if key not in self._active_chats and not q.empty():
                        target_key, target_q = key, q
                        self._active_chats.add(key)
                        break
                if target_q is None:
                    self._chat_queues_cond.wait(timeout=1.0)
                    continue
            try:
                while True:
                    try:
                        content = target_q.get_nowait()
                    except Empty:
                        break
                    try:
                        if content is None:
                            return
                        update, context = content
                        try:
                            self.msg(update, context)
                        except Exception as e:
                            self.logger.exception(
                                "Error [%r] occurred while processing update %s.", e, update)
                            if update.effective_message:
                                update.effective_message.reply_text(
                                    self._("Unknown error has occurred while "
                                           "trying to process this message. See log for "
                                           "details.\n\n{error!r}").format(error=e))
                    finally:
                        target_q.task_done()
            finally:
                with self._chat_queues_lock:
                    self._active_chats.discard(target_key)
                    self._chat_queues_cond.notify_all()

    def _slave_timeout_for(self, m) -> int:
        """按消息类型取 slave 调用超时(秒)。"""
        return self._slave_timeout_tiers.get(getattr(m, "type", None), self._slave_timeout_default)

    def _call_slave(self, m):
        """带超时的 slave 调用。超时后抛 EFBMessageError,上层会给用户提示。

        注意:超时后后台线程仍可能完成发送,极端情况下用户需在微信端确认。
        """
        timeout = self._slave_timeout_for(m)
        future = self._slave_executor.submit(coordinator.send_message, m)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            self.logger.error("调用 slave 发送消息超时(%ss,类型=%s),已放弃等待",
                              timeout, getattr(m, "type", "?"))
            raise EFBMessageError(
                self._("Slave channel timed out while sending the message. "
                       "Please check on the slave side whether it was delivered."))

    def message_worker(self):
        """保留原方法名以兼容:现在仅做分发,实际处理在 _chat_worker_loop。"""
        self._dispatch_loop()

    def stop_worker(self):
        if not self.message_worker_thread.is_alive():
            return
        self.message_queue.put(None)
        self.message_worker_thread.join()
        for t in getattr(self, "_chat_worker_threads", []):
            t.join()
        executor = getattr(self, "_slave_executor", None)
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def enqueue_message(self, update: Update, context: CallbackContext):
        assert isinstance(update, Update)

        self.message_queue.put((update, context))
        workers_alive = self.message_worker_thread.is_alive() and all(
            t.is_alive() for t in getattr(self, "_chat_worker_threads", []))
        if not workers_alive:
            if update.effective_message:
                update.effective_message.reply_text(
                    self._(
                        "ETM message worker is not running due to unforeseen reason. This might be a bug. Please see log for details."))

    def msg(self, update: Update, context: CallbackContext):
        """
        Process, wrap and dispatch messages from user.
        """
        assert isinstance(update, Update)
        assert update.effective_message
        assert update.effective_chat

        message: Message = update.effective_message
        mid = utils.message_id_to_str(update=update)

        self.logger.debug("[%s] Received message from Telegram: %s", mid, message.to_dict())
        if message.from_user and message.from_user.id in self.bot.trusted_bots:
            self.logger.info(
                "[%s] Received trusted bot message: bot_id=%s chat_id=%s topic_id=%s type=%s",
                mid, message.from_user.id, message.chat.id, message.message_thread_id,
                get_msg_type(message).name)

        destination = None
        edited = None
        destination = None
        edited = None
