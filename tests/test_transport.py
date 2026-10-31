import hashlib
import os
import shutil
import sys

import pytest

from remote_local_sync import agent
from remote_local_sync.transport import AgentTransport, RemoteError, TransportError, remote_command
from conftest import write

SYSTEM_PYTHONS = [p for p in ("/usr/bin/python3",) if os.path.exists(p)]


def open_agent(python=sys.executable, noise=""):
    return AgentTransport(["sh", "-c", noise + remote_command(python)])


@pytest.mark.parametrize("python", [sys.executable] + SYSTEM_PYTHONS)
def test_round_trip_large_file(tmp_path, python):
    data = os.urandom(agent.CHUNK * 2 + 12345)
    src = tmp_path / "src.bin"
    src.write_bytes(data)
    t = open_agent(python)
    try:
        size, mtime = t.put(str(src), str(tmp_path / "remote" / "dst.bin"), 1_600_000_000_000_000_000, 0o640, True)
        assert size == len(data) and mtime == 1_600_000_000_000_000_000
        assert (tmp_path / "remote" / "dst.bin").read_bytes() == data
        assert os.stat(tmp_path / "remote" / "dst.bin").st_mode & 0o777 == 0o640

        t.get(str(tmp_path / "remote" / "dst.bin"), str(tmp_path / "back.bin"), True, True, True)
        assert (tmp_path / "back.bin").read_bytes() == data
        assert os.stat(tmp_path / "back.bin").st_mtime_ns == 1_600_000_000_000_000_000

        assert t.hashes(str(tmp_path / "remote"), ["dst.bin"]) == {"dst.bin": hashlib.sha256(data).hexdigest()}
    finally:
        t.close()


def test_login_noise_before_agent_is_ignored(tmp_path):
    t = open_agent(noise="echo 'Welcome to the server'; echo '{\"motd\": 1}'; ")
    try:
        assert t.stat(str(tmp_path))[0] == "d"
    finally:
        t.close()


def test_errors_keep_connection_usable(tmp_path):
    write(tmp_path / "file", "x")
    write(tmp_path / "dir" / "inside", "x")
    src = write(tmp_path / "src", "data")
    t = open_agent()
    try:
        with pytest.raises(RemoteError):
            t.put(str(src), str(tmp_path / "file" / "child"), None, None, True)  # parent is a file
        with pytest.raises(RemoteError):
            t.get(str(tmp_path / "missing"), str(tmp_path / "out"), True, True, False)
        with pytest.raises(RemoteError):
            t.remove(str(tmp_path / "dir"))  # not empty
        # still in step after the failures
        assert t.put(str(src), str(tmp_path / "ok"), None, None, True)[0] == 4
        assert t.read(str(tmp_path / "ok")) == b"data"
    finally:
        t.close()


def test_missing_python_reports_clearly(tmp_path):
    with pytest.raises(TransportError, match="python"):
        AgentTransport(["sh", "-c", remote_command("no-such-python3") + " 2>/dev/null"])
