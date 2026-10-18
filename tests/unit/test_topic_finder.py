from efb_telegram_master.topic_finder import build_topic_maps, display_name, match_score, topic_link
from efb_telegram_master.utils import chat_id_to_str


def test_match_score():
    assert match_score("张三", "客户张三", "wxid_1", "张三") == 4
    assert match_score("张三", None, None, "张") == 3
    assert match_score("张三", None, None, "三") == 2
    assert match_score("李四", None, None, "张") == 0


def test_topic_link():
    assert topic_link("-1001234567890", "42") == "https://t.me/c/1234567890/42"
    assert topic_link("-1001234567890", "42", "9876") == "https://t.me/c/1234567890/42/9876"
    assert topic_link("-123456", "42") is None
    assert topic_link("123456", "42") is None
    assert topic_link("-1001234567890", "0") is None


def test_display_name():
    assert display_name("张三", "客户张三") == "客户张三（张三）"
    assert display_name("张三", None) == "张三"


def test_chat_id_to_str_uses_space_separator():
    assert chat_id_to_str(channel_id="honus.comwechat", chat_uid="wxid_1") == "honus.comwechat wxid_1"


def test_build_topic_maps_prefers_current_group():
    rows = [
        ("honus.comwechat wxid_1", "-100111", "5"),
        ("honus.comwechat wxid_1", "-100222", "6"),
        ("honus.comwechat wxid_2", "-100222", "7"),
    ]
    preferred, fallback = build_topic_maps(rows, "-100111")
    assert preferred["honus.comwechat wxid_1"] == ("-100111", "5")
    assert fallback["honus.comwechat wxid_1"] == ("-100111", "5")
    assert fallback["honus.comwechat wxid_2"] == ("-100222", "7")
    assert "honus.comwechat wxid_2" not in preferred
