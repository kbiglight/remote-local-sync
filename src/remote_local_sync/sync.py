"""Compare a local folder with its server copy, and copy files between them."""

from __future__ import annotations

import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple

from . import agent
from .config import Config, ConfigError, Mapping, join_remote
from .state import State
from .transport import RemoteError, TransportError, connect

TOL_NS = 1_000_000_000  # modification times closer than 1 s count as equal


class Entry(NamedTuple):
    kind: str   # "f" file or "d" folder
    size: int
    mtime: int  # ns
    mode: int


def to_entries(scan_result: dict) -> Dict[str, Entry]:
    return {e[0]: Entry(*e[1:]) for e in scan_result["entries"]}


def _kind_name(e: Entry) -> str:
    return "folder" if e.kind == "d" else "file"


@dataclass
class Item:
    path: str
    status: str  # "=" same, "M" modified, "L" local only, "R" remote only, "T" file vs folder
    local: Optional[Entry] = None
    remote: Optional[Entry] = None
    children: List[Tuple[str, Entry]] = field(default_factory=list)  # inside a one-sided folder

    @property
    def is_dir(self) -> bool:
        e = self.local or self.remote
        return e.kind == "d" and self.status != "T"

    @property
    def newer(self) -> str:
        if self.local and self.remote and self.local.kind == self.remote.kind == "f":
            diff = self.local.mtime - self.remote.mtime
            if diff > TOL_NS:
                return "local"
            if diff < -TOL_NS:
                return "remote"
        return ""

    def describe(self) -> str:
        if self.status == "=":
            return "identical"
        if self.status == "M":
            return f"modified, {self.newer} newer" if self.newer else "modified"
        if self.status == "T":
            return f"local is a {_kind_name(self.local)}, remote is a {_kind_name(self.remote)}"
        side = "local" if self.status == "L" else "remote"
        if self.is_dir:
            files = sum(1 for _, e in self.children if e.kind == "f")
            return f"{side} only, {files} file{'s' if files != 1 else ''}"
        return f"{side} only"


def _quick_status(l: Entry, r: Entry, mode: str) -> Optional[str]:
    """Status of two files, or None when only their contents can tell."""
    if mode == "size":
        return "=" if l.size == r.size else "M"
    if mode == "timestamp":
        return "=" if abs(l.mtime - r.mtime) <= TOL_NS else "M"
    if l.size != r.size:
        return "M"
    if mode == "quick" and abs(l.mtime - r.mtime) <= TOL_NS:
        return "="
    return None


def compare(local: Dict[str, Entry], remote: Dict[str, Entry], mode: str,
            hash_local: Callable[[List[str]], dict], hash_remote: Callable[[List[str]], dict]) -> List[Item]:
    """One Item per file that exists on either side, in tree order.

    A folder that exists on one side only becomes a single Item whose
    children are everything inside it. Folders on both sides are not listed.
    """
    items: List[Item] = []
    group: Optional[Item] = None      # one-sided folder being filled
    group_side: Dict[str, Entry] = {}
    need_hash: List[Item] = []
    for path in sorted(set(local) | set(remote), key=lambda p: p.split("/")):
        if group is not None:
            if path.startswith(group.path + "/") and path in group_side:
                group.children.append((path, group_side[path]))
                continue
            group = None
        l, r = local.get(path), remote.get(path)
        if l is not None and r is not None:
            if l.kind != r.kind:
                group = Item(path, "T", l, r)
                group_side = local if l.kind == "d" else remote
                items.append(group)
            elif l.kind == "f":
                status = _quick_status(l, r, mode)
                items.append(Item(path, status or "?", l, r))
                if status is None:
                    need_hash.append(items[-1])
        elif l is not None:
            items.append(Item(path, "L", l, None))
            if l.kind == "d":
                group, group_side = items[-1], local
        else:
            items.append(Item(path, "R", None, r))
            if r.kind == "d":
                group, group_side = items[-1], remote
    if need_hash:
        rels = [i.path for i in need_hash]
        lh, rh = hash_local(rels), hash_remote(rels)
        for i in need_hash:
            same = lh.get(i.path) is not None and lh.get(i.path) == rh.get(i.path)
            i.status = "=" if same else "M"
    return items


@dataclass
class Comparison:
    mapping: Mapping
    start: str
    items: List[Item]
    paths: Set[str]  # everything seen on either side
    local_exists: bool
    remote_exists: bool
    skipped: List[Tuple[str, str, str]]  # (side, path, reason)

    @property
    def differences(self) -> List[Item]:
        return [i for i in self.items if i.status != "="]


@dataclass
class Action:
    kind: str  # "copy", "mkdir", "delete", "rmdir", "skip"
    path: str
    src: Optional[Entry] = None
    dst: Optional[Entry] = None
    reason: str = ""
    forceable: bool = True  # whether --force would turn this skip into a change

    @property
    def hint(self) -> str:
        if not self.reason:
            return ""
        return f": {self.reason}" + (" (--force to override)" if self.forceable else " (resolve by hand)")

    @property
    def is_dir(self) -> bool:
        e = self.src or self.dst
        return e is not None and e.kind == "d"


class Session:
    def __init__(self, cfg: Config, transport=None, out=None) -> None:
        self.cfg = cfg
        self.out = out or sys.stdout
        self.t = transport if transport is not None else connect(cfg)
        s = cfg.server
        self.state = State(cfg.state_path, f"{s.type}:{s.label}:{s.port or ''}")
        self.keep_mode = os.name == "posix" and self.t.remote_os == "posix"

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.state.save()
        self.t.close()

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    # ---- paths -------------------------------------------------------------

    @staticmethod
    def mkey(m: Mapping) -> str:
        return f"{m.key} -> {m.remote}"

    @staticmethod
    def local_path(m: Mapping, rel: str) -> str:
        return str(m.local.joinpath(*rel.split("/"))) if rel else str(m.local)

    @staticmethod
    def remote_path(m: Mapping, rel: str) -> str:
        return join_remote(m.remote, rel) if rel else m.remote

    @staticmethod
    def display(m: Mapping, rel: str, is_dir: bool = False) -> str:
        path = rel if m.key == "." else (f"{m.key}/{rel}" if rel else m.key)
        return (path or ".") + ("/" if is_dir and path else "")

    def targets(self, paths: List[str], cwd: Path) -> List[Tuple[Mapping, str]]:
        """Turn command-line paths into (mapping, path inside the mapping)."""
        if not paths:
            return [(m, "") for m in self.cfg.mappings]
        out = []
        for p in paths:
            target = (cwd / p).resolve()
            owners = [m for m in self.cfg.mappings if target == m.local or m.local in target.parents]
            if not owners:
                raise ConfigError(f"{p} is not inside any [[mapping]] local folder")
            m = max(owners, key=lambda m: len(m.local.parts))
            rel = target.relative_to(m.local).as_posix()
            out.append((m, "" if rel == "." else rel))
        return out

    def _excludes(self, m: Mapping) -> Tuple[List[str], List[str]]:
        # A mapping nested inside this one is synced on its own, so leave it out here.
        nested = [o.local.relative_to(m.local).as_posix() for o in self.cfg.mappings
                  if o is not m and m.local in o.local.parents]
        return self.cfg.exclude_names, list(self.cfg.options.exclude_paths) + nested

    # ---- compare -------------------------------------------------------------

    def compare(self, m: Mapping, start: str = "", mode: Optional[str] = None) -> Comparison:
        names, paths = self._excludes(m)
        lscan = agent.scan(str(m.local), names, paths, start)
        rscan = self.t.scan(m.remote, names, paths, start)
        local, remote = to_entries(lscan), to_entries(rscan)
        items = compare(local, remote, mode or self.cfg.options.compare,
                        lambda rels: agent.hashes(str(m.local), rels),
                        lambda rels: self.t.hashes(m.remote, rels))
        skipped = ([("local", p, why) for p, why in lscan["skipped"]]
                   + [("remote", p, why) for p, why in rscan["skipped"]])
        return Comparison(m, start, items, set(local) | set(remote),
                          lscan["exists"], rscan["exists"], skipped)

    def record_baseline(self, comp: Comparison) -> None:
        """Remember identical files as synced, and forget files gone from both sides."""
        mkey = self.mkey(comp.mapping)
        for i in comp.items:
            if i.status == "=":
                self.state.set(mkey, i.path, [i.local.size, i.local.mtime], [i.remote.size, i.remote.mtime])
        self.state.prune(mkey, comp.start, comp.paths)

    # ---- safety checks -----------------------------------------------------

    def _dst_changed(self, m: Mapping, rel: str, dst: Entry, src: Entry, up: bool) -> str:
        """Why overwriting dst would lose work, or "" if it wouldn't."""
        dst_name = "remote" if up else "local"
        rec = self.state.get(self.mkey(m), rel)
        if rec is not None:
            if tuple(rec["r" if up else "l"]) != (dst.size, dst.mtime):
                return f"{dst_name} changed since last sync"
            return ""
        if dst.mtime > src.mtime + TOL_NS:
            return f"{dst_name} copy is newer and was never synced"
        return ""

    def _why_keep(self, m: Mapping, rel: str, dst: Entry, up: bool) -> str:
        """Why deleting dst would lose work, or "" if it wouldn't."""
        dst_name = "remote" if up else "local"
        rec = self.state.get(self.mkey(m), rel)
        if rec is None:
            return f"exists only on {dst_name} and was never synced"
        if tuple(rec["r" if up else "l"]) != (dst.size, dst.mtime):
            return f"{dst_name} changed since last sync"
        return ""

    # ---- push / pull -------------------------------------------------------

    def plan(self, comp: Comparison, up: bool, delete: bool = False, force: bool = False) -> List[Action]:
        """Actions that make the destination match the source (up: local -> remote)."""
        m = comp.mapping
        check = self.cfg.options.warn_newer and not force
        actions: List[Action] = []
        deletes: List[Action] = []
        rmdirs: List[Action] = []
        kept: List[str] = []
        for item in comp.items:
            src, dst = (item.local, item.remote) if up else (item.remote, item.local)
            if item.status == "=":
                continue
            if item.status == "T":
                actions.append(Action("skip", item.path, src, dst, item.describe(), forceable=False))
            elif src is not None and dst is not None:
                reason = self._dst_changed(m, item.path, dst, src, up) if check else ""
                actions.append(Action("skip" if reason else "copy", item.path, src, dst, reason))
            elif src is not None:
                actions.append(Action("mkdir" if src.kind == "d" else "copy", item.path, src))
                for rel, e in item.children:
                    actions.append(Action("mkdir" if e.kind == "d" else "copy", rel, e))
            elif delete:
                for rel, e in [(item.path, dst)] + item.children:
                    if e.kind == "d":
                        rmdirs.append(Action("rmdir", rel, None, e))
                        continue
                    reason = "" if force else self._why_keep(m, rel, e, up)
                    if reason:
                        kept.append(rel)
                        actions.append(Action("skip", rel, None, e, reason))
                    else:
                        deletes.append(Action("delete", rel, None, e))
        # Folders are removed deepest first, and only when nothing inside is kept.
        rmdirs = [a for a in rmdirs if not any(k.startswith(a.path + "/") for k in kept)]
        rmdirs.sort(key=lambda a: a.path.count("/"), reverse=True)
        return actions + deletes + rmdirs

    def execute(self, comp: Comparison, actions: List[Action], up: bool) -> Counter:
        m, opts = comp.mapping, self.cfg.options
        mkey = self.mkey(m)
        done: Counter = Counter()
        for a in actions:
            shown = self.display(m, a.path, a.is_dir)
            if a.kind == "skip":
                self.say(f"  skip      {shown}{a.hint}")
                done["skipped"] += 1
                continue
            lpath, rpath = self.local_path(m, a.path), self.remote_path(m, a.path)
            try:
                if a.kind == "mkdir":
                    self.t.mkdir(rpath) if up else agent.mkdir(lpath)
                elif a.kind == "copy" and up:
                    s = a.src
                    res = self.t.put(lpath, rpath, s.mtime if opts.preserve_timestamps else None,
                                     s.mode if self.keep_mode else None, opts.use_temp_file)
                    self.state.set(mkey, a.path, [s.size, s.mtime], res)
                elif a.kind == "copy":
                    s = a.src
                    res = self.t.get(rpath, lpath, opts.use_temp_file, opts.preserve_timestamps, self.keep_mode)
                    self.state.set(mkey, a.path, res, [s.size, s.mtime])
                else:  # delete, rmdir
                    self.t.remove(rpath) if up else agent.remove(lpath)
                    self.state.drop(mkey, a.path)
            except (RemoteError, OSError) as e:
                if a.kind == "rmdir":
                    self.say(f"  keep      {shown}: not empty (excluded items inside)")
                    done["skipped"] += 1
                else:
                    self.say(f"  error     {shown}: {e}")
                    done["errors"] += 1
                continue
            verb = {"copy": "upload" if up else "download"}.get(a.kind, a.kind)
            self.say(f"  {verb:<9} {shown}")
            done[a.kind] += 1
        self.state.save()
        return done

    # ---- watch (automatic upload) ------------------------------------------

    def _scan_local(self, m: Mapping) -> Dict[str, Entry]:
        names, paths = self._excludes(m)
        return to_entries(agent.scan(str(m.local), names, paths))

    def _log(self, verb: str, shown: str, note: str = "") -> None:
        self.say(f"{time.strftime('%H:%M:%S')}  {verb:<8} {shown}{': ' + note if note else ''}")

    def _watch_upload(self, m: Mapping, rel: str, e: Entry) -> None:
        opts, shown = self.cfg.options, self.display(m, rel)
        rpath = self.remote_path(m, rel)
        try:
            if opts.warn_newer:
                st = self.t.stat(rpath)
                if st is not None and st[0] != "f":
                    return self._log("skip", shown, f"remote is a {'folder' if st[0] == 'd' else st[0]}")
                reason = self._dst_changed(m, rel, Entry(*st), e, up=True) if st is not None else ""
                if reason:
                    return self._log("skip", shown, reason + f" (check with `{agent.NAME} diff`, then `{agent.NAME} push --force`)")
            res = self.t.put(self.local_path(m, rel), rpath, e.mtime if opts.preserve_timestamps else None,
                             e.mode if self.keep_mode else None, opts.use_temp_file)
        except (RemoteError, OSError) as err:
            return self._log("error", shown, str(err))
        self.state.set(self.mkey(m), rel, [e.size, e.mtime], res)
        self.state.save()
        self._log("upload", shown)

    def _watch_delete(self, m: Mapping, gone: List[str], old: Dict[str, Entry]) -> None:
        mkey = self.mkey(m)
        for rel in [r for r in gone if old[r].kind == "f"]:
            shown, rpath = self.display(m, rel), self.remote_path(m, rel)
            try:
                st = self.t.stat(rpath)
                if st is None:
                    self.state.drop(mkey, rel)
                    continue
                rec = self.state.get(mkey, rel)
                if st[0] != "f" or rec is None or tuple(rec["r"]) != (st[1], st[2]):
                    self._log("keep", shown, "remote changed since last sync or was never synced")
                    continue
                self.t.remove(rpath)
            except (RemoteError, OSError) as err:
                self._log("error", shown, str(err))
                continue
            self.state.drop(mkey, rel)
            self._log("delete", shown)
        for rel in sorted((r for r in gone if old[r].kind == "d"), key=lambda r: r.count("/"), reverse=True):
            try:
                self.t.remove(self.remote_path(m, rel))
                self._log("rmdir", self.display(m, rel, True))
            except RemoteError:
                self._log("keep", self.display(m, rel, True), "not empty on remote")
        self.state.save()

    def watch(self, interval: Optional[float] = None, delete: Optional[bool] = None,
              rounds: Optional[int] = None) -> None:
        """Upload local files shortly after they change.

        A changed file is uploaded once it looks the same in two scans in a row,
        so half-written files are not sent.
        """
        opts = self.cfg.options
        interval = opts.watch_interval if interval is None else interval
        delete = opts.watch_delete if delete is None else delete
        snaps = {m.key: self._scan_local(m) for m in self.cfg.mappings}
        pending: Dict[Tuple[str, str], Entry] = {}
        n = 0
        while rounds is None or n < rounds:
            n += 1
            time.sleep(interval)
            try:
                for m in self.cfg.mappings:
                    old, new = snaps[m.key], self._scan_local(m)
                    for rel, e in sorted(new.items(), key=lambda kv: kv[0].split("/")):
                        key = (m.key, rel)
                        if e.kind == "d":
                            if rel not in old:
                                self.t.mkdir(self.remote_path(m, rel))
                                self._log("mkdir", self.display(m, rel, True))
                        elif old.get(rel) != e:
                            pending[key] = e
                        elif pending.get(key) == e:
                            self._watch_upload(m, rel, e)
                            del pending[key]
                    gone = [rel for rel in old if rel not in new]
                    for rel in gone:
                        pending.pop((m.key, rel), None)
                    if delete and gone:
                        self._watch_delete(m, gone, old)
                    snaps[m.key] = new
            except TransportError as err:
                self._log("error", "connection", f"{err}; reconnecting in 5 s")
                time.sleep(5)
                try:
                    self.t.close()
                    self.t = connect(self.cfg)
                except TransportError as err2:
                    self._log("error", "connection", str(err2))
