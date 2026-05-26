"""Handler Registry for named handler dispatch.

This module provides the HandlerRegistry class and module-level singleton
for server-side resolution of string-based handler names to callables.

Resolution order:
  1. Explicit register() calls
  2. importlib.metadata entry_points (group="qworker.handlers"), lazy + cached
  3. Raise QWException if not found
"""
import importlib.metadata
from navconfig.logging import logging

from qw.exceptions import QWException


class HandlerRegistry:
    """Server-side registry mapping string names to callable handlers.

    Resolution order: explicit register() → entry_points → error.
    Resolved handlers are cached after first lookup.
    Thread-safe: each worker process has its own registry instance; no locking needed.

    Example:
        >>> registry = HandlerRegistry()
        >>> async def my_handler(slug=None, conditions=None, **opts): ...
        >>> registry.register("my.handler", my_handler)
        >>> handler = registry.resolve("my.handler")
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._handlers: dict[str, callable] = {}
        self._cache: dict[str, callable] = {}
        self._entry_points_scanned: bool = False
        self.logger = logging.getLogger('QW.Registry')

    def register(self, name: str, handler: callable) -> None:
        """Register a handler by name. Overwrites existing entry.

        Args:
            name: The string identifier for the handler (e.g. "querysource.remote.query_handler").
            handler: The callable (sync or async function) to invoke when resolved.
        """
        self._handlers[name] = handler
        # Evict any stale cache entry so the explicit handler takes precedence
        self._cache.pop(name, None)
        self.logger.debug("Registered handler: %s", name)

    def resolve(self, name: str) -> callable:
        """Resolve a handler name to a callable.

        Resolution order:
          1. Explicit _handlers dict (programmatic register())
          2. Entry_points scan (lazy, cached) via importlib.metadata
          3. Raise QWException

        Args:
            name: The handler name to resolve.

        Returns:
            The callable registered under *name*.

        Raises:
            QWException: When no handler is found for the given name.
        """
        # 1. Explicit registrations always win
        if name in self._handlers:
            return self._handlers[name]

        # 2. Check cache (previously discovered via entry_points)
        if name in self._cache:
            return self._cache[name]

        # 3. Lazy entry_points scan — only once per process
        if not self._entry_points_scanned:
            self._scan_entry_points()
            if name in self._cache:
                return self._cache[name]

        raise QWException(
            f"Handler not found: {name!r}. "
            f"Register it via HandlerRegistry.register() or declare it in "
            f"[project.entry-points.\"qworker.handlers\"] in pyproject.toml."
        )

    def list_handlers(self) -> dict[str, str]:
        """Return a snapshot of all registered and discovered handlers.

        Triggers a lazy entry_points scan if not yet done.

        Returns:
            A dict mapping handler name → repr(handler) for all known handlers.
        """
        if not self._entry_points_scanned:
            self._scan_entry_points()

        combined: dict[str, str] = {}
        # Entry_points first (lower priority)
        for name, handler in self._cache.items():
            combined[name] = repr(handler)
        # Explicit registrations last (higher priority — may overwrite cache repr)
        for name, handler in self._handlers.items():
            combined[name] = repr(handler)
        return combined

    def _scan_entry_points(self) -> None:
        """Scan importlib.metadata entry_points for the qworker.handlers group.

        Results are stored in _cache. Only called once per process.
        """
        self._entry_points_scanned = True
        try:
            eps = importlib.metadata.entry_points(group="qworker.handlers")
            for ep in eps:
                try:
                    handler = ep.load()
                    self._cache[ep.name] = handler
                    self.logger.debug(
                        "Discovered handler via entry_points: %s → %r", ep.name, handler
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    self.logger.warning(
                        "Failed to load entry_point handler %r: %s", ep.name, exc
                    )
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.warning("Entry_points scan failed: %s", exc)


# Module-level singleton — each worker process gets its own instance
handler_registry = HandlerRegistry()
