"""Atomic file writes for the local storage backend (rule 2).

Every file is written to a temporary file in the same directory and then moved into place, so a failed or
interrupted write never leaves a partial file that later runs would take for a good cached copy.
"""

import os
import tempfile
from pathlib import Path

PARTIAL_SUFFIX = ".partial"


def save_atomically(path: Path, body: bytes) -> None:
    """Write `body` to `path` so that readers see either the old file or the whole new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=PARTIAL_SUFFIX)
    try:
        with os.fdopen(handle, "wb") as partial:
            partial.write(body)
        Path(temporary).replace(path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)  # cleanup only; the error itself is re-raised unchanged
        raise
