"""File-system side of the sync tool.

This module runs in two places:
- imported by the client, for the local folder and for "local" servers;
- sent over ssh and run by the server's python, where serve() answers
  requests on stdin/stdout.

Keep it stdlib-only and compatible with Python 3.6, because the server
runs it with whatever python it has.

Wire format: every message is one JSON line. A message with an "n" key is
followed by exactly n raw bytes. File contents travel as a series of such
frames, ended by a frame with n == 0.
"""

import fnmatch
import hashlib
import json
import os
import shutil
import stat
import sys

# Product name. It lives here, and only here, because this file must also run
# alone on the server; the rest of the package imports it from this module.
# To rename the product, change NAME and the two names in pyproject.toml
# (tests/test_name.py fails until they agree).
NAME = "relosy"

PROTOCOL = 1
CHUNK = 1 << 20  # 1 MiB
TMP_SUFFIX = "." + NAME + "-tmp"


def native(path):
    return os.path.expanduser(path)


def join(root, rel):
    if not rel:
        return root
    return os.path.join(root, *rel.split("/"))


def _excluded(name, rel, names, paths):
    if name.endswith(TMP_SUFFIX) or rel in paths:
        return True
    for pattern in names:
        if fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def _kind(mode):
    if stat.S_ISREG(mode):
        return "f"
    if stat.S_ISDIR(mode):
        return "d"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "special file"


def scan(root, exclude_names=(), exclude_paths=(), start=""):
    """List files and folders under root/start, without following symlinks.

    Returns {"exists": bool, "entries": [[rel, kind, size, mtime_ns, mode], ...],
    "skipped": [[rel, reason], ...]}. rel uses "/" and is relative to root.
    kind is "f" (file) or "d" (folder); symlinks and special files are skipped.
    "exists" says whether root itself is a folder.
    """
    root = native(root)
    result = {"exists": os.path.isdir(root), "entries": [], "skipped": []}
    if not result["exists"]:
        return result
    paths = set(p.strip("/") for p in exclude_paths)
    entries, skipped = result["entries"], result["skipped"]
    stack = []
    start = start.strip("/")
    if start:
        try:
            st = os.lstat(join(root, start))
        except OSError:
            return result
        kind = _kind(st.st_mode)
        if kind == "f":
            entries.append([start, "f", st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode)])
        elif kind == "d":
            entries.append([start, "d", 0, st.st_mtime_ns, stat.S_IMODE(st.st_mode)])
            stack.append(start)
        else:
            skipped.append([start, kind])
    else:
        stack.append("")
    while stack:
        rel_dir = stack.pop()
        try:
            it = os.scandir(join(root, rel_dir))
        except OSError as e:
            skipped.append([rel_dir, str(e)])
            continue
        with it:
            for de in it:
                rel = rel_dir + "/" + de.name if rel_dir else de.name
                if _excluded(de.name, rel, exclude_names, paths):
                    continue
                try:
                    st = de.stat(follow_symlinks=False)
                except OSError as e:
                    skipped.append([rel, str(e)])
                    continue
                kind = _kind(st.st_mode)
                if kind == "f":
                    entries.append([rel, "f", st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode)])
                elif kind == "d":
                    entries.append([rel, "d", 0, st.st_mtime_ns, stat.S_IMODE(st.st_mode)])
                    stack.append(rel)
                else:
                    skipped.append([rel, kind])
    return result


def file_hash(path):
    h = hashlib.sha256()
    with open(native(path), "rb") as f:
        while True:
            data = f.read(CHUNK)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def hashes(root, rels):
    """sha256 of each root/rel; None when the file can't be read."""
    root = native(root)
    out = {}
    for rel in rels:
        try:
            out[rel] = file_hash(join(root, rel))
        except OSError:
            out[rel] = None
    return out


def stat_path(path):
    """[kind, size, mtime_ns, mode] of path, or None if it doesn't exist."""
    try:
        st = os.lstat(native(path))
    except (FileNotFoundError, NotADirectoryError):
        return None
    return [_kind(st.st_mode), st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode)]


def mkdir(path):
    os.makedirs(native(path), exist_ok=True)


def remove(path, recursive=False):
    """Remove a file or folder. A folder must be empty unless recursive.

    Returns False when there was nothing to remove.
    """
    path = native(path)
    st = stat_path(path)
    if st is None:
        return False
    if st[0] == "d":
        if recursive:
            shutil.rmtree(path)
        else:
            os.rmdir(path)
    else:
        os.remove(path)
    return True


class Writer:
    """Write a file under a temporary name, then move it into place."""

    def __init__(self, path, use_temp=True):
        self.path = native(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if use_temp:
            head, tail = os.path.split(self.path)
            self.target = os.path.join(head, "." + tail + TMP_SUFFIX)
        else:
            self.target = self.path
        self.f = open(self.target, "wb")

    def write(self, data):
        self.f.write(data)

    def abort(self):
        self.f.close()
        if self.target != self.path:
            try:
                os.remove(self.target)
            except OSError:
                pass

    def finish(self, mtime_ns=None, mode=None):
        """Close, apply mtime/mode, move into place. Returns [size, mtime_ns]."""
        self.f.close()
        if mode is None and self.target != self.path and os.name == "posix":
            # The sender has no POSIX mode (e.g. Windows): keep the replaced file's mode.
            try:
                mode = stat.S_IMODE(os.stat(self.path).st_mode)
            except OSError:
                pass
        if mode is not None and os.name == "posix":
            os.chmod(self.target, mode)
        if mtime_ns is not None:
            os.utime(self.target, ns=(mtime_ns, mtime_ns))
        if self.target != self.path:
            os.replace(self.target, self.path)
        st = os.stat(self.path)
        return [st.st_size, st.st_mtime_ns]


def copy_file(src, dst, mtime_ns=None, mode=None, use_temp=True):
    w = Writer(dst, use_temp)
    try:
        with open(native(src), "rb") as f:
            shutil.copyfileobj(f, w.f, CHUNK)
    except BaseException:
        w.abort()
        raise
    return w.finish(mtime_ns, mode)


# ---- wire protocol ---------------------------------------------------------

def _read_exact(inp, n):
    parts = []
    while n > 0:
        data = inp.read(n)
        if not data:
            raise EOFError("connection closed")
        parts.append(data)
        n -= len(data)
    return b"".join(parts)


def write_msg(out, obj, payload=None):
    if payload is not None:
        obj = dict(obj, n=len(payload))
    out.write((json.dumps(obj) + "\n").encode("utf-8"))
    if payload:
        out.write(payload)


def read_msg(inp):
    line = inp.readline()
    if not line:
        raise EOFError("connection closed")
    obj = json.loads(line.decode("utf-8"))
    payload = _read_exact(inp, obj["n"]) if obj.get("n") else None
    return obj, payload


def _serve_put(inp, out, req):
    # The frames must be read to the end even after an error,
    # or the next request would start in the middle of file data.
    writer, error = None, None
    try:
        writer = Writer(req["path"], req.get("temp", True))
    except Exception as e:
        error = e
    while True:
        frame, data = read_msg(inp)
        if not frame.get("n"):
            break
        if error is None:
            try:
                writer.write(data)
            except Exception as e:
                error = e
    if frame.get("abort") and error is None:
        error = RuntimeError("upload aborted by client")
    if error is not None:
        if writer is not None:
            writer.abort()
        raise error
    write_msg(out, {"ok": True, "result": writer.finish(req.get("mtime"), req.get("mode"))})


def _serve_get(out, req):
    with open(native(req["path"]), "rb") as f:
        st = os.fstat(f.fileno())
        write_msg(out, {"ok": True, "mtime": st.st_mtime_ns, "mode": stat.S_IMODE(st.st_mode)})
        while True:
            try:
                data = f.read(CHUNK)
            except OSError as e:
                write_msg(out, {"error": str(e)}, b"")
                return
            write_msg(out, {}, data)
            if not data:
                return


def _serve_simple(req):
    op = req.get("op")
    if op == "scan":
        return scan(req["root"], req.get("names", ()), req.get("paths", ()), req.get("start", ""))
    if op == "hash":
        return hashes(req["root"], req["rels"])
    if op == "stat":
        return stat_path(req["path"])
    if op == "mkdir":
        return mkdir(req["path"])
    if op == "remove":
        return remove(req["path"], req.get("recursive", False))
    raise ValueError("unknown op %r" % op)


def serve(inp, out):
    write_msg(out, {NAME: PROTOCOL, "os": os.name,
                    "python": "%d.%d" % sys.version_info[:2]})
    out.flush()
    while True:
        try:
            req, _ = read_msg(inp)
        except EOFError:
            return
        try:
            if req.get("op") == "put":
                _serve_put(inp, out, req)
            elif req.get("op") == "get":
                _serve_get(out, req)
            else:
                write_msg(out, {"ok": True, "result": _serve_simple(req)})
        except EOFError:
            return
        except Exception as e:
            write_msg(out, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        out.flush()


if __name__ == "__main__":
    serve(sys.stdin.buffer, sys.stdout.buffer)
