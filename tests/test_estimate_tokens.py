"""Token estimation (drives compression budgeting) — CJK/JP/KR weighting."""
from dbaide.agent.loop_prompts import estimate_tokens


def test_ascii_roughly_quarter_length():
    assert estimate_tokens("x" * 40) == 10  # ~4 chars/token
    assert estimate_tokens("") == 1         # never zero


def test_cjk_and_jp_kr_counted_as_wide_chars():
    # Chinese, Japanese (hiragana+katakana), and Korean are wide → ~1.5 tokens/char,
    # far above the ascii ÷4. Under-counting them would make compression under-trigger.
    for s in ("数据库连接", "こんにちはカタカナ", "안녕하세요데이터"):
        ascii_same_len = "x" * len(s)
        assert estimate_tokens(s) > estimate_tokens(ascii_same_len), s
        # ~1.5/char, so well above len/4.
        assert estimate_tokens(s) >= len(s)
