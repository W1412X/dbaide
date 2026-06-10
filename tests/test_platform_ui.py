from dbaide.desktop.platform_ui import chrome_suppresses_mnemonics, escape_mnemonic, label_for_chrome_button


def test_escape_mnemonic_doubles_ampersand():
    assert escape_mnemonic("Save & Close") == "Save && Close"


def test_label_for_chrome_button_icon_only():
    assert label_for_chrome_button("Chat", icon_only=True) == ""


def test_chrome_suppresses_mnemonics_platforms():
    # Document expected platforms — actual value depends on test runner OS.
    assert isinstance(chrome_suppresses_mnemonics(), bool)
