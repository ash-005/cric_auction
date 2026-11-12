"""Gunicorn reads this file automatically from the project root, before it loads
the app. When the gevent worker is in use, patch the standard library here, as
early as possible, so the app always starts in gevent mode. Without this, a
preloaded app (``--preload``, e.g. via GUNICORN_CMD_ARGS) is imported before the
worker patches anything, starts in threading mode, and WebSockets and polling
hang under the gevent worker.
"""
import os
import sys

_command = ' '.join(sys.argv) + ' ' + os.environ.get('GUNICORN_CMD_ARGS', '')
if 'gevent' in _command:
    from gevent import monkey

    monkey.patch_all()
