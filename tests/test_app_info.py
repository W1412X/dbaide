import pytest

from dbaide.app_info import APP_NAME, app_version, project_links


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_app_version_matches_package():
    from dbaide import __version__
    assert app_version() == __version__


def test_project_links_include_github():
    urls = [url for _, url in project_links()]
    assert any("github.com/W1412X/dbaide" in url for url in urls)
    assert any("/releases" in url for url in urls)


def test_settings_about_page_shows_version(qapp):
    from dbaide.desktop.dialogs.settings import SettingsDialog

    dlg = SettingsDialog(connections=[], models=[], initial_page="about")
    assert dlg.stack.currentIndex() == 4
    page = dlg.stack.widget(4)
    from PyQt6.QtWidgets import QLabel
    texts = [w.text() for w in page.findChildren(QLabel)]
    assert APP_NAME in " ".join(texts)
    assert app_version() in " ".join(texts)


def test_settings_about_release_check_result(qapp):
    from dbaide.desktop.dialogs.settings import SettingsDialog
    from dbaide.i18n import t

    dlg = SettingsDialog(connections=[], models=[], initial_page="about")
    dlg.set_release_check_result(
        ok=True,
        current_version="0.2.2",
        latest_version="0.2.3",
        update_available=True,
        release_url="https://github.com/W1412X/dbaide/releases/tag/v0.2.3",
    )
    assert not dlg._about_latest_link.isHidden()
    assert "0.2.3" in dlg._about_latest_link.text()
    assert "0.2.3" in dlg._about_latest_value.text()

    dlg.set_release_check_result(ok=False)
    assert t("settings.about.latest_unavailable") == dlg._about_latest_value.text()
    assert dlg._about_latest_link.isHidden()


def test_settings_about_ahead_of_release(qapp):
    from dbaide.desktop.dialogs.settings import SettingsDialog
    from dbaide.i18n import t

    dlg = SettingsDialog(connections=[], models=[], initial_page="about")
    dlg.set_release_check_result(
        ok=True,
        current_version="0.3.0",
        latest_version="0.2.2",
        update_available=False,
        ahead_of_release=True,
        release_url="https://github.com/W1412X/dbaide/releases/tag/v0.2.2",
    )
    assert t("settings.about.latest_ahead", version="0.2.2") == dlg._about_latest_value.text()
    assert dlg._about_latest_link.isHidden()


def test_topbar_update_button_hidden_when_ahead(qapp):
    from dbaide.desktop.views.topbar import TopBar

    bar = TopBar()
    bar.set_update_available(False, version="0.2.2", url="https://example/release")
    assert bar.update_btn.isHidden()


def test_topbar_update_button_hidden_by_default(qapp):
    from dbaide.desktop.views.topbar import TopBar

    bar = TopBar()
    assert bar.update_btn.isHidden()
    bar.set_update_available(True, version="9.9.9", url="https://example/release")
    assert not bar.update_btn.isHidden()
    bar.set_update_available(False)
    assert bar.update_btn.isHidden()
