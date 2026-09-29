"""NamedHandlerWrapper — wire-format wrapper for string-based handler dispatch.

Carries a handler name string + positional args + keyword args from client
to server via cloudpickle serialization. The server resolves the name via
HandlerRegistry and executes the handler.

Pattern follows FuncWrapper (qw/wrappers/func.py).
"""
from .base import QueueWrapper


class NamedHandlerWrapper(QueueWrapper):
    """Wire-format wrapper for string-based handler dispatch.

    Carries a handler name + positional/keyword arguments from client to server.
    The server resolves the name via HandlerRegistry and executes the handler.

    Named handlers are always immediate (queued=False by default).

    Example:
        >>> wrapper = NamedHandlerWrapper(
        ...     "querysource.remote.query_handler",
        ...     "my-slug",
        ...     conditions={"store_id": 42},
        ... )
        >>> wrapper.handler_name
        'querysource.remote.query_handler'
        >>> wrapper.queued
        False
    """

    def __init__(self, handler_name: str, *args, **kwargs) -> None:
        """Initialise the wrapper.

        Args:
            handler_name: The string identifier for the handler to resolve.
            *args: Positional arguments to pass to the handler on execution.
            **kwargs: Keyword arguments to pass to the handler on execution.
                      ``queued`` defaults to False (named handlers are always immediate).
        """
        # Force queued=False — named handlers bypass the queue
        kwargs.setdefault('queued', False)
        # Pass args/kwargs to base so UUID, debug, container_config are initialised.
        # NOTE: `**kwargs` unpacking in super().__init__ creates a *new* dict inside
        # QueueWrapper, so QueueWrapper's internal `.pop('queued')` does NOT affect
        # our local `kwargs` variable here.  However, our local `kwargs` STILL contains
        # the internal keys (e.g. 'queued' from setdefault above), so we must strip
        # them explicitly below before storing as handler arguments.
        super().__init__(*args, **kwargs)
        self._handler_name: str = handler_name
        # Re-assign args so they contain the handler call arguments (not the coro slot).
        self.args = args
        # Strip QueueWrapper-internal keys so they are not forwarded to the handler.
        # 'queued'/'debug'/'container_config' are QueueWrapper lifecycle flags.
        # 'id' is consumed by QueueWrapper.__init__ as the task UUID — it is NOT
        # a handler-level 'id' parameter and must not be forwarded to the handler.
        _internal_keys = ('queued', 'debug', 'id', 'container_config')
        self.kwargs = {k: v for k, v in kwargs.items() if k not in _internal_keys}

    @property
    def handler_name(self) -> str:
        """The string identifier used to resolve this handler in HandlerRegistry.

        Returns:
            The handler name string provided at construction time.
        """
        return self._handler_name

    async def __call__(self) -> None:
        """Not implemented — handler resolution and execution are server-side.

        Raises:
            NotImplementedError: Always. Call the resolved handler directly.
        """
        raise NotImplementedError(
            f"NamedHandlerWrapper({self._handler_name!r}) cannot be called directly. "
            f"Use HandlerRegistry.resolve() on the server side."
        )

    def __repr__(self) -> str:
        return f"<NamedHandlerWrapper: {self._handler_name!r}>"

    def __str__(self) -> str:
        return f"<NamedHandlerWrapper: {self._handler_name!r}>"
