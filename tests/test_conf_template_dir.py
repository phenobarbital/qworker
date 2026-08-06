import importlib


class TestTemplateDir:
    def test_template_dir_default_none(self, monkeypatch):
        """TEMPLATE_DIR defaults to None when env var is unset."""
        import qw.conf
        monkeypatch.delenv('TEMPLATE_DIR', raising=False)
        # Re-import to pick up change
        importlib.reload(qw.conf)
        try:
            assert qw.conf.TEMPLATE_DIR is None
        finally:
            # `importlib.reload` mutates the shared `qw.conf` module object
            # in-place; `monkeypatch` only reverts the environment variable
            # at fixture teardown, *after* this test function returns. To
            # avoid leaking a stale `TEMPLATE_DIR` value into `qw.conf` for
            # any test that runs after this one, revert the env var now
            # (via `monkeypatch.undo()`) and reload again so the module is
            # left in its original state before the next test runs.
            monkeypatch.undo()
            importlib.reload(qw.conf)

    def test_template_dir_from_env(self, monkeypatch):
        """TEMPLATE_DIR reads value from env var."""
        import qw.conf
        monkeypatch.setenv('TEMPLATE_DIR', '/opt/templates')
        importlib.reload(qw.conf)
        try:
            assert qw.conf.TEMPLATE_DIR == '/opt/templates'
        finally:
            # See test_template_dir_default_none for why this is needed.
            monkeypatch.undo()
            importlib.reload(qw.conf)

    def test_template_dir_importable(self):
        """TEMPLATE_DIR is importable from qw.conf."""
        from qw.conf import TEMPLATE_DIR
        assert TEMPLATE_DIR is None or isinstance(TEMPLATE_DIR, str)
