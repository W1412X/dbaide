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
    labels = [w.text() for w in page.findChildren(type(page)) if hasattr(w, "text")]
    from PyQt6.QtWidgets import QLabel
    texts = [w.text() for w in page.findChildren(QLabel)]
    assert APP_NAME in " ".join(texts)
    assert app_version() in " ".join(texts)
