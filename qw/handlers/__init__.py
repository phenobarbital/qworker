"""QWorker built-in handlers package.

Handlers are callables that can be discovered via entry_points
(group="qworker.handlers") and registered with HandlerRegistry.

Available built-in handlers:
    querysource.remote.query_handler — Execute QuerySource queries remotely.
        Requires the `querysource` optional dependency.
"""
