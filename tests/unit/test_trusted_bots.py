from types import SimpleNamespace

import pytest
from telegram import Chat, Message, MessageEntity, Update, User

from efb_telegram_master.trusted_bots import build_trusted_bot_filter, normalize_id_list

BOT_ID = 5000
GROUP_ID = -1001


def make_update(user_id=BOT_ID, chat_id=GROUP_ID, text="hello", is_bot=True, edited=False):
    entities = [MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(text.split()[0]))] \
        if text.startswith("/") else []
    message = Message(message_id=1, date=None, chat=Chat(id=chat_id, type=Chat.SUPERGROUP),
                      from_user=User(id=user_id, first_name="bot", is_bot=is_bot),
                      text=text, entities=entities)
    if edited:
        return Update(update_id=1, edited_message=message)
    return Update(update_id=1, message=message)


def test_normalize_id_list():
    assert normalize_id_list(None, "x") == []
    assert normalize_id_list(5, "x") == [5]
    assert normalize_id_list(["-1001", 7], "x") == [-1001, 7]
    with pytest.raises(ValueError):
        normalize_id_list(["abc"], "x")
    with pytest.raises(ValueError):
        normalize_id_list({"a": 1}, "x")


def test_no_trusted_bots_builds_no_filter():
    assert build_trusted_bot_filter([]) is None


def test_trusted_bot_plain_message_allowed():
    trusted = build_trusted_bot_filter([BOT_ID], [GROUP_ID])
    assert trusted(make_update())


def test_trusted_bot_restrictions():
    trusted = build_trusted_bot_filter([BOT_ID], [GROUP_ID])
    assert not trusted(make_update(text="/unlink_all"))
    assert not trusted(make_update(edited=True))
    assert not trusted(make_update(chat_id=-2002))
    assert not trusted(make_update(user_id=6000))


def test_trusted_bot_any_chat_when_unrestricted():
    trusted = build_trusted_bot_filter([BOT_ID])
    assert trusted(make_update(chat_id=-2002))
