"""One exclusive file lock, on either platform.

`fcntl` does not exist on Windows, and importing it at module scope meant
`gmail_send` and `verify_email` did not import at all there, so `daily.py`
died on its first import and no send was ever attempted. A failed import is
the worst shape for this to take: nothing is logged, because the logging
lives behind the import.

Each backend imports its own module inside the function, so the platform only
reaches for the module it has. Same idea on both: an exclusive advisory lock
held on an open file, which is all the three call sites need. On Windows
`msvcrt.locking` takes a byte range from the current position rather than the
whole file, which is why the seek is part of the call and not an accident.
"""

import os


def _posix(fh, blocking):
    import fcntl
    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(fh.fileno(), flags)
    except OSError:
        return False
    return True


def _windows(fh, blocking):
    import msvcrt
    fh.seek(0)
    mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
    try:
        msvcrt.locking(fh.fileno(), mode, 1)
    except OSError:
        return False
    return True


_acquire = _windows if os.name == "nt" else _posix

# msvcrt retries a blocking lock once a second and gives up after ten, so the
# Windows side waits up to ten seconds rather than indefinitely. Every hold in
# this repository is milliseconds, and the one lock that could outlive that is
# the queue lock, which is taken non blocking on purpose.
WAITS_FOREVER = os.name != "nt"


def lock(fh):
    """Take the lock, waiting for it."""
    _acquire(fh, True)


def try_lock(fh):
    """Take the lock, or return False when another process holds it."""
    return _acquire(fh, False)
