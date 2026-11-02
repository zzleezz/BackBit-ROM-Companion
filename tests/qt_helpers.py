"""Shared helpers for the Qt test modules."""

from PyQt6 import sip


def dispose(win) -> None:
    """Close a test window and destroy it now.

    A closed window left for Python's garbage collector stays subscribed to the app-wide theme signal. If a
    collection lands inside a later theme switch, the window is torn down while its own theme handler is
    running and PyQt aborts the process on the resulting error (seen on the macOS CI runners).
    """
    if not sip.isdeleted(win):
        win.close()
        sip.delete(win)
