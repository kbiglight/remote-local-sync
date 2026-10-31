import json
import os
import sys
from pathlib import Path

import pytest

from remote_local_sync.config import CONFIG_DIR

# Runs the agent the way ssh would: through a shell, as one command string.
# `sh -c 'eval "$2"' sh <host> <command>` ignores the host and runs the command.
FAKE_SSH = ["sh", "-c", 'eval "$2"', "sh"]


def write(path: Path, text: str, mtime: float = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def make_config(local: Path, remote: Path, kind: str, ssh=None, options: str = "") -> None:
    if kind == "local":
        server = f'type = "local"\nroot = {json.dumps(str(remote.parent))}\n'
    else:
        server = (f'type = "ssh"\nhost = "fake"\nroot = {json.dumps(str(remote.parent))}\n'
                  f'python = {json.dumps(sys.executable)}\nssh = {json.dumps(ssh or FAKE_SSH)}\n')
    text = (f"[server]\n{server}\n[[mapping]]\nlocal = \".\"\nremote = {json.dumps(remote.name)}\n\n"
            f"[options]\n{options}\n")
    (local / CONFIG_DIR).mkdir(exist_ok=True)
    (local / CONFIG_DIR / "config.toml").write_text(text)


@pytest.fixture(params=["local", "agent"])
def proj(request, tmp_path, monkeypatch):
    """A local project folder and its "server" folder, connected by either transport."""
    local, remote = tmp_path / "local", tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    make_config(local, remote, request.param)
    monkeypatch.chdir(local)
    return local, remote
