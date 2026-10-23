"""nightshift — keep work alive when the process that started it is not.

A task is a JSON file that moves between three folders. Whoever holds it holds a
lease; a dead holder's lease expires and the task comes back with a trail. A task
is only ``done`` when the ledger holds a ``verify`` record with ``rc == 0``.

Protocol: docs/PROTOCOL.md. Story: examples/two_workers.py.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
