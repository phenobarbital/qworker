import importlib

import pytest


class TestTemplateDir:
    def test_template_dir_default_none(self, monkeypatch):
        """TEMPLATE_DIR defaults to None when env var is unset."""
        monkeypatch.delenv('TEMPLATE_DIR', raising=False)
        # Re-import to pick up change
        import qw.conf
        importlib.reload(qw.conf)
        assert qw.conf.TEMPLATE_DIR is None

    def test_template_dir_from_env(self, monkeypatch):
        """TEMPLATE_DIR reads value from env var."""
        monkeypatch.setenv('TEMPLATE_DIR', '/opt/templates')
        import qw.conf
        importlib.reload(qw.conf)
        assert qw.conf.TEMPLATE_DIR == '/opt/templates'

    def test_template_dir_importable(self):
        """TEMPLATE_DIR is importable from qw.conf."""
        from qw.conf import TEMPLATE_DIR
        assert TEMPLATE_DIR is None or isinstance(TEMPLATE_DIR, str)
