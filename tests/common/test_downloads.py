"""Only the network is replaced: `http_client.get` is swapped for a fake reply or failure."""

from pathlib import Path

import pytest

from nfl_alt_lines.common import http_client
from nfl_alt_lines.common.downloads import save_atomically

URL = "https://example.com/file.csv"


def reply(body: bytes) -> http_client.Response:
    return http_client.Response(body=body, headers={})


def test_save_creates_the_folder_and_replaces_an_older_file(tmp_path: Path) -> None:
    path = tmp_path / "new" / "games.csv"
    save_atomically(path, b"old")
    save_atomically(path, b"new")
    assert path.read_bytes() == b"new"
    assert [child.name for child in path.parent.iterdir()] == ["games.csv"]


def test_a_failed_save_leaves_no_partial_file(tmp_path: Path) -> None:
    target = tmp_path / "games.csv"
    target.mkdir()  # a folder in the way makes the final move fail
    with pytest.raises(OSError):
        save_atomically(target, b"body")
    assert list(tmp_path.iterdir()) == [target]
