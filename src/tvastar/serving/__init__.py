"""Serving layer: expose agents over HTTP/WebSocket and a CLI.

The FastAPI server is optional (``tvastar[serve]``); the CLI works with the core
alone for the ``chat`` REPL and uses the server only for ``serve``.
"""

from .loader import load_agent

__all__ = ["load_agent"]
