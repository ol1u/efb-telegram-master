from efb_telegram_master.topic_finder import display_name, match_score, topic_link


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
