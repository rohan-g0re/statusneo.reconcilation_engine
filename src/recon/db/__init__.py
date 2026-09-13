"""Persistence: a thin standard-library ``sqlite3`` layer.

No ORM, no query builder, no unit-of-work abstraction, no connection pool, no async.
Plain functions taking a connection as their first argument.

This package imports nothing but the standard library, ``recon.config`` and
``recon.domain``.  That is what lets the engine depend on it without dragging FastAPI
into the import graph, and a test asserts it in a subprocess.
"""
