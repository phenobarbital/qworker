"""Unit tests for qw.registry.HandlerRegistry."""
import pytest
from unittest.mock import patch, MagicMock

from qw.registry import HandlerRegistry, handler_registry
from qw.exceptions import QWException


@pytest.fixture
def registry():
    """Fresh HandlerRegistry per test."""
    return HandlerRegistry()


@pytest.fixture
def sample_handler():
    """A simple async handler fixture."""
    async def handler(slug=None, conditions=None, **options):
        return {"result": "ok"}
    return handler


class TestHandlerRegistry:
    def test_register_and_resolve(self, registry, sample_handler):
        """register() stores handler; resolve() returns it."""
        registry.register("test.handler", sample_handler)
        resolved = registry.resolve("test.handler")
        assert resolved is sample_handler

    def test_resolve_unknown_raises(self, registry):
        """resolve() for an unregistered name raises QWException."""
        with pytest.raises(QWException, match="test.unknown"):
            registry.resolve("test.unknown")

    def test_explicit_overrides_entry_points(self, registry, sample_handler):
        """Explicit register() wins over any entry_points handler with the same name."""
        registry.register("ep.handler", sample_handler)
        resolved = registry.resolve("ep.handler")
        assert resolved is sample_handler

    def test_list_handlers_includes_registered(self, registry, sample_handler):
        """list_handlers() includes programmatically registered handlers."""
        registry.register("test.handler", sample_handler)
        handlers = registry.list_handlers()
        assert "test.handler" in handlers

    def test_list_handlers_returns_repr(self, registry, sample_handler):
        """list_handlers() values are repr strings."""
        registry.register("test.handler", sample_handler)
        handlers = registry.list_handlers()
        assert isinstance(handlers["test.handler"], str)

    def test_entry_points_not_scanned_initially(self, registry):
        """Entry_points scan has not happened at construction time."""
        assert registry._entry_points_scanned is False

    def test_entry_points_lazy_scan_triggered_on_resolve(self, registry):
        """First unresolved resolve() triggers the entry_points scan."""
        assert registry._entry_points_scanned is False
        with pytest.raises(QWException):
            registry.resolve("nonexistent.handler")
        assert registry._entry_points_scanned is True

    def test_entry_points_lazy_scan_triggered_on_list(self, registry):
        """list_handlers() also triggers the entry_points scan."""
        assert registry._entry_points_scanned is False
        registry.list_handlers()
        assert registry._entry_points_scanned is True

    def test_entry_points_scanned_only_once(self, registry):
        """Entry_points are not re-scanned on repeated calls."""
        with patch("importlib.metadata.entry_points", return_value=[]) as mock_eps:
            try:
                registry.resolve("first.miss")
            except QWException:
                pass
            try:
                registry.resolve("second.miss")
            except QWException:
                pass
            # Should only have been called once
            mock_eps.assert_called_once()

    def test_entry_points_discovery(self, registry):
        """resolve() discovers and caches a handler from entry_points."""
        mock_handler = lambda: None  # noqa: E731
        mock_ep = MagicMock()
        mock_ep.name = "discovered.handler"
        mock_ep.load.return_value = mock_handler

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            resolved = registry.resolve("discovered.handler")
            assert resolved is mock_handler

    def test_entry_points_cached_after_discovery(self, registry):
        """Handler discovered via entry_points is cached and not reloaded."""
        mock_handler = lambda: None  # noqa: E731
        mock_ep = MagicMock()
        mock_ep.name = "cached.handler"
        mock_ep.load.return_value = mock_handler

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            r1 = registry.resolve("cached.handler")
            r2 = registry.resolve("cached.handler")
            assert r1 is r2
            # entry_points only called once
            mock_ep.load.assert_called_once()

    def test_explicit_register_evicts_cache(self, registry, sample_handler):
        """Calling register() with an already-cached name replaces it."""
        # Seed cache directly
        other_handler = lambda: "other"  # noqa: E731
        registry._cache["test.handler"] = other_handler

        registry.register("test.handler", sample_handler)
        resolved = registry.resolve("test.handler")
        assert resolved is sample_handler
        assert resolved is not other_handler

    def test_list_handlers_explicit_wins_over_cache(self, registry, sample_handler):
        """When a name exists in both cache and _handlers, explicit wins in list."""
        cache_handler = lambda: "cache"  # noqa: E731
        registry._cache["test.handler"] = cache_handler
        registry._handlers["test.handler"] = sample_handler

        handlers = registry.list_handlers()
        # Should reflect sample_handler's repr, not cache_handler's
        assert handlers["test.handler"] == repr(sample_handler)

    def test_singleton_exists(self):
        """The module-level handler_registry singleton is a HandlerRegistry instance."""
        assert handler_registry is not None
        assert isinstance(handler_registry, HandlerRegistry)

    def test_multiple_registrations(self, registry, sample_handler):
        """Multiple handlers can be registered and resolved independently."""
        another = lambda: None  # noqa: E731
        registry.register("handler.one", sample_handler)
        registry.register("handler.two", another)
        assert registry.resolve("handler.one") is sample_handler
        assert registry.resolve("handler.two") is another

    def test_register_overwrites(self, registry, sample_handler):
        """Registering the same name twice overwrites the previous handler."""
        first = lambda: "first"  # noqa: E731
        registry.register("test.handler", first)
        registry.register("test.handler", sample_handler)
        assert registry.resolve("test.handler") is sample_handler
