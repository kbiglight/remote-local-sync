"""Talk to the server: a local or mounted folder, or the agent over ssh."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import zlib
from pathlib import Path
from typing import List, Optional

from . import agent
from .config import Config


class TransportError(Exception):
    """The connection to the server failed; nothing more can be done over it."""


class RemoteError(Exception):
    """One operation failed on the server; the connection is still usable."""


class LocalTransport:
    """Server type "local": the folder is reachable through the local file system."""

    def __init__(self) -> None:
        self.remote_os = os.name

    def scan(self, root: str, names: List[str], paths: List[str], start: str = "") -> dict:
        return agent.scan(root, names, paths, start)

    def hashes(self, root: str, rels: List[str]) -> dict:
        return agent.hashes(root, rels)

    def stat(self, path: str) -> Optional[list]:
        return agent.stat_path(path)

    def mkdir(self, path: str) -> None:
        agent.mkdir(path)

    def remove(self, path: str, recursive: bool = False) -> bool:
        return agent.remove(path, recursive)

    def put(self, src: str, path: str, mtime: Optional[int], mode: Optional[int], temp: bool) -> list:
        return agent.copy_file(src, path, mtime, mode, temp)

    def get(self, path: str, dst: str, temp: bool, keep_mtime: bool, keep_mode: bool) -> list:
        st = agent.stat_path(path)
        if st is None:
            raise RemoteError(f"{path}: no such file")
        return agent.copy_file(path, dst, st[2] if keep_mtime else None, st[3] if keep_mode else None, temp)

    def read(self, path: str) -> bytes:
        with open(agent.native(path), "rb") as f:
            return f.read()

    def close(self) -> None:
        pass


def agent_code() -> str:
    """One line of python that runs agent.py, safe to pass through any shell."""
    blob = base64.b64encode(zlib.compress(Path(agent.__file__).read_bytes(), 9)).decode("ascii")
    return f"import base64,zlib;exec(zlib.decompress(base64.b64decode('{blob}')))"


def remote_command(python: str) -> str:
    return f'{python} -c "{agent_code()}"'


class AgentTransport:
    """Run agent.py in a child process (normally ssh) and send it requests."""

    def __init__(self, argv: List[str]) -> None:
        self.argv = argv
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        except OSError as e:
            raise TransportError(f"cannot run {argv[0]}: {e}") from None
        self.inp, self.out = self.proc.stdout, self.proc.stdin
        hello = self._handshake()
        self.remote_os = hello["os"]
        self.remote_python = hello.get("python", "")

    def _handshake(self) -> dict:
        # Login scripts on the server may print text before the agent starts; skip it.
        while True:
            line = self.inp.readline()
            if not line:
                code = self.proc.wait()
                raise TransportError(f"connection closed before the agent started (exit code {code}); "
                                     "check ssh access and that [server] python exists on the server")
            try:
                hello = json.loads(line)
            except ValueError:
                continue
            if isinstance(hello, dict) and agent.NAME in hello:
                if hello[agent.NAME] != agent.PROTOCOL:
                    raise TransportError(f"agent protocol {hello[agent.NAME]} != {agent.PROTOCOL}")
                return hello

    def _send(self, obj: dict, payload: Optional[bytes] = None) -> None:
        try:
            agent.write_msg(self.out, obj, payload)
        except (BrokenPipeError, OSError) as e:
            raise TransportError(f"connection lost: {e}") from None

    def _flush(self) -> None:
        try:
            self.out.flush()
        except (BrokenPipeError, OSError) as e:
            raise TransportError(f"connection lost: {e}") from None

    def _recv(self):
        try:
            return agent.read_msg(self.inp)
        except (EOFError, OSError, ValueError) as e:
            raise TransportError(f"connection lost: {e}") from None

    def _reply(self) -> dict:
        resp, _ = self._recv()
        if not resp.get("ok"):
            raise RemoteError(resp.get("error", "unknown error"))
        return resp

    def _call(self, req: dict):
        self._send(req)
        self._flush()
        return self._reply().get("result")

    def scan(self, root: str, names: List[str], paths: List[str], start: str = "") -> dict:
        return self._call({"op": "scan", "root": root, "names": names, "paths": paths, "start": start})

    def hashes(self, root: str, rels: List[str]) -> dict:
        return self._call({"op": "hash", "root": root, "rels": rels})

    def stat(self, path: str) -> Optional[list]:
        return self._call({"op": "stat", "path": path})

    def mkdir(self, path: str) -> None:
        self._call({"op": "mkdir", "path": path})

    def remove(self, path: str, recursive: bool = False) -> bool:
        return self._call({"op": "remove", "path": path, "recursive": recursive})

    def put(self, src: str, path: str, mtime: Optional[int], mode: Optional[int], temp: bool) -> list:
        self._send({"op": "put", "path": path, "mtime": mtime, "mode": mode, "temp": temp})
        try:
            with open(src, "rb") as f:
                while True:
                    data = f.read(agent.CHUNK)
                    if not data:
                        break
                    self._send({}, data)
        except OSError:
            self._send({"abort": True}, b"")
            self._flush()
            try:
                self._reply()
            except RemoteError:
                pass
            raise
        self._send({}, b"")
        self._flush()
        return self._reply()["result"]

    def _stream(self, path: str):
        """Yield the header, then each chunk of the file."""
        self._send({"op": "get", "path": path})
        self._flush()
        yield self._reply()
        while True:
            frame, data = self._recv()
            if frame.get("error"):
                raise RemoteError(frame["error"])
            if not data:
                return
            yield data

    def get(self, path: str, dst: str, temp: bool, keep_mtime: bool, keep_mode: bool) -> list:
        stream = self._stream(path)
        head = next(stream)
        try:
            writer = agent.Writer(dst, temp)
        except OSError:
            for _ in stream:  # drain so the connection stays in step
                pass
            raise
        error = None
        try:
            for data in stream:
                if error is None:
                    try:
                        writer.write(data)
                    except OSError as e:  # keep reading so the connection stays in step
                        error = e
        except BaseException:
            writer.abort()
            raise
        if error is not None:
            writer.abort()
            raise error
        return writer.finish(head["mtime"] if keep_mtime else None, head["mode"] if keep_mode else None)

    def read(self, path: str) -> bytes:
        stream = self._stream(path)
        next(stream)
        return b"".join(stream)

    def close(self) -> None:
        try:
            self.out.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def ssh_argv(cfg: Config) -> List[str]:
    s = cfg.server
    argv = list(s.ssh)
    if s.port:
        argv += ["-p", str(s.port)]
    if s.user:
        argv += ["-l", s.user]
    return argv + [s.host, remote_command(s.python)]


def connect(cfg: Config):
    if cfg.server.type == "local":
        return LocalTransport()
    return AgentTransport(ssh_argv(cfg))
