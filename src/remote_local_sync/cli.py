"""Command line: init | status | diff | push | pull | watch."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import List, Tuple

from . import __version__, agent
from .agent import NAME
from .config import COMPARE_MODES, CONFIG_DIR, ConfigError, find_project, load, write_template
from .sync import Action, Comparison, Session
from .transport import RemoteError, TransportError

DIFF_LIMIT = 4 << 20  # files bigger than 4 MiB are not diffed


def open_session() -> Session:
    return Session(load(find_project(Path.cwd())))


def remote_label(s: Session, remote: str) -> str:
    return remote if s.cfg.server.type == "local" else f"{s.cfg.server.label}:{remote}"


def header(s: Session, comp: Comparison) -> str:
    m = comp.mapping
    where = s.display(m, comp.start) if comp.start else ""
    return f"{m.local}  <->  {remote_label(s, m.remote)}" + (f"   [{where}]" if where else "")


def summary(comp_list: List[Comparison]) -> Tuple[str, bool]:
    counts = Counter(i.status for c in comp_list for i in c.items)
    parts = [f"{counts['=']} identical"]
    for status, name in (("M", "modified"), ("L", "local only"), ("R", "remote only"), ("T", "file/folder conflict")):
        if counts[status]:
            parts.append(f"{counts[status]} {name}")
    in_sync = sum(counts.values()) == counts["="]
    return ", ".join(parts) + (" -> in sync" if in_sync else " -> NOT in sync"), in_sync


# ---- init --------------------------------------------------------------------

def cmd_init(args) -> int:
    if args.type == "ssh" and not args.host:
        raise ConfigError("--host is required for --type ssh")
    if args.type == "local" and not args.root:
        raise ConfigError("--root is required for --type local (the local or mounted folder)")
    root = args.root or "~"
    path = write_template(Path.cwd(), args.type, args.host or "", args.remote, root, args.python)
    print(f"created {path}")
    print(f"check [server] and [[mapping]] in it, then run `{NAME} status`")
    return 0


# ---- status ------------------------------------------------------------------

def item_json(s: Session, comp: Comparison, i) -> dict:
    return {"path": s.display(comp.mapping, i.path, i.is_dir), "status": i.status, "description": i.describe(),
            "local": i.local._asdict() if i.local else None, "remote": i.remote._asdict() if i.remote else None,
            "children": len(i.children)}


def cmd_status(args) -> int:
    with open_session() as s:
        mode = args.compare or s.cfg.options.compare
        comps = [s.compare(m, start, mode) for m, start in s.targets(args.paths, Path.cwd())]
        line, in_sync = summary(comps)
        if args.json:
            print(json.dumps({"in_sync": in_sync, "compare": mode, "mappings": [
                {"local": str(c.mapping.local), "remote": c.mapping.remote, "start": c.start,
                 "local_exists": c.local_exists, "remote_exists": c.remote_exists,
                 "items": [item_json(s, c, i) for i in c.items if args.all or i.status != "="],
                 "skipped": [{"side": side, "path": p, "reason": why} for side, p, why in c.skipped]}
                for c in comps]}, indent=1))
            return 0 if in_sync else 1
        if not args.quiet:
            for c in comps:
                m = c.mapping
                print(header(s, c) + f"   (compare: {mode})")
                if not c.local_exists:
                    print("  ! local folder does not exist")
                if not c.remote_exists:
                    print("  ! remote folder does not exist (yet)")
                for i in c.items:
                    if i.status != "=" or args.all:
                        print(f"  {i.status}  {s.display(m, i.path, i.is_dir):<44} {i.describe()}")
                if c.skipped and args.all:
                    for side, p, why in c.skipped:
                        print(f"  -  {s.display(m, p):<44} skipped on {side}: {why}")
                elif c.skipped:
                    print(f"  ({len(c.skipped)} symlinks/unreadable items skipped; --all lists them)")
        print(line)
        return 0 if in_sync else 1


# ---- diff --------------------------------------------------------------------

def cmd_diff(args) -> int:
    with open_session() as s:
        [(m, rel)] = s.targets([args.path], Path.cwd())
        if not rel:
            raise ConfigError(f"give a file, not a mapping folder; use `{NAME} status` for folders")
        lpath, rpath = s.local_path(m, rel), s.remote_path(m, rel)
        lst, rst = agent.stat_path(lpath), s.t.stat(rpath)
        if lst is None and rst is None:
            print(f"{rel}: not found on either side")
            return 2
        if lst is None or rst is None:
            print(f"{rel}: exists only on {'remote' if lst is None else 'local'}")
            return 1
        if lst[0] != "f" or rst[0] != "f":
            print(f"{rel}: not a file on both sides (local {lst[0]}, remote {rst[0]}); use `{NAME} status`")
            return 1
        if max(lst[1], rst[1]) > DIFF_LIMIT:
            same = lst[1] == rst[1] and agent.file_hash(lpath) == s.t.hashes(m.remote, [rel])[rel]
            print(f"{rel}: {'identical' if same else 'differs'} (local {lst[1]} bytes, remote {rst[1]} bytes; "
                  "too large to show a diff)")
            return 0 if same else 1
        local_bytes = Path(lpath).read_bytes()
        try:
            remote_bytes = s.t.read(rpath)
        except RemoteError as e:
            print(f"{rel}: cannot read remote copy: {e}")
            return 2
    if local_bytes == remote_bytes:
        print(f"{rel}: identical")
        return 0
    try:
        a, b = remote_bytes.decode("utf-8"), local_bytes.decode("utf-8")
    except UnicodeDecodeError:
        print(f"{rel}: binary files differ")
        return 1
    for line in difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                     f"remote/{rel}", f"local/{rel}"):
        sys.stdout.write(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return 1


# ---- push / pull -------------------------------------------------------------

def action_line(s: Session, comp: Comparison, a: Action, up: bool) -> str:
    shown = s.display(comp.mapping, a.path, a.is_dir)
    verb = {"copy": "upload" if up else "download"}.get(a.kind, a.kind)
    return f"  {verb:<9} {shown}{a.hint}"


def confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        print("not a terminal: pass --yes to allow deleting", file=sys.stderr)
        return False
    return input(prompt).strip().lower() in ("y", "yes")


def cmd_transfer(args, up: bool) -> int:
    dst = "remote" if up else "local"
    with open_session() as s:
        plans = []
        for m, start in s.targets(args.paths, Path.cwd()):
            comp = s.compare(m, start, args.compare)
            plans.append((comp, s.plan(comp, up, args.delete, args.force)))
        dst_only = sum(1 for c, _ in plans for i in c.items if i.status == ("R" if up else "L"))

        if args.dry_run:
            for comp, actions in plans:
                if actions:
                    print(header(s, comp))
                    for a in actions:
                        print(action_line(s, comp, a, up))
            print("dry run: nothing changed")
            return 0

        removals = [(c, a) for c, actions in plans for a in actions if a.kind in ("delete", "rmdir")]
        if removals and not args.yes:
            print(f"these will be deleted on {dst}:")
            for c, a in removals:
                print(f"  {s.display(c.mapping, a.path, a.is_dir)}")
            if not confirm(f"delete {len(removals)} item(s) on {dst}? [y/N] "):
                print("aborted; nothing changed")
                return 1

        done: Counter = Counter()
        for comp, actions in plans:
            if actions:
                print(header(s, comp))
            done += s.execute(comp, actions, up)
            s.record_baseline(comp)

    verb = "uploaded" if up else "downloaded"
    parts = [f"{done['copy']} {verb}"]
    for key, name in (("mkdir", "folder(s) created"), ("delete", "deleted"), ("rmdir", "folder(s) removed"),
                      ("skipped", "skipped"), ("errors", "error(s)")):
        if done[key]:
            parts.append(f"{done[key]} {name}")
    print(", ".join(parts))
    if dst_only and not args.delete:
        print(f"{dst_only} item(s) exist only on {dst}; kept (use --delete to remove them)")
    return 1 if done["skipped"] or done["errors"] else 0


# ---- watch -------------------------------------------------------------------

def cmd_watch(args) -> int:
    with open_session() as s:
        for m in s.cfg.mappings:
            print(f"watching {m.local}  ->  {remote_label(s, m.remote)}")
        line, in_sync = summary([s.compare(m, "", "quick") for m in s.cfg.mappings])
        if not in_sync:
            print(f"note: {line}. Existing differences are uploaded only when the file changes again; "
                  f"run `{NAME} status` / `{NAME} push` for them.")
        delete = s.cfg.options.watch_delete if args.delete is None else args.delete
        print(f"uploading changes every {args.interval or s.cfg.options.watch_interval}s"
              f"{', deleting remote files deleted locally' if delete else ''}. Ctrl+C to stop.")
        try:
            s.watch(args.interval, delete)
        except KeyboardInterrupt:
            print("stopped")
    return 0


# ---- main --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=NAME, description="Check that a local folder matches a server folder "
                                "and keep them in sync: compare, upload, download, auto-upload.")
    p.add_argument("--version", action="version", version=f"{NAME} {__version__}")
    sub = p.add_subparsers(dest="command", metavar="command")
    sub.required = True

    sp = sub.add_parser("init", help=f"create {CONFIG_DIR}/config.toml in the current folder")
    sp.add_argument("--type", choices=["ssh", "local"], default="ssh",
                    help="ssh: over ssh / local: a local or mounted folder (default ssh)")
    sp.add_argument("--host", help="ssh host: an alias from ~/.ssh/config or user@hostname")
    sp.add_argument("--remote", required=True,
                    help="server folder for this project, relative to --root unless it starts with /, ~ or C:")
    sp.add_argument("--root", help='base folder on the server (default "~"; required with --type local)')
    sp.add_argument("--python", default="python3", help="python on the server (default python3)")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("status", help="compare local and remote; exit code 0 = in sync, 1 = differences")
    sp.add_argument("paths", nargs="*", help="limit to these files or folders")
    sp.add_argument("--compare", choices=COMPARE_MODES, help="override [options] compare")
    sp.add_argument("-a", "--all", action="store_true", help="also list identical and skipped items")
    sp.add_argument("-q", "--quiet", action="store_true", help="print only the summary line")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("diff", help="show a file's differences (remote -> local)")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_diff)

    for name, up in (("push", True), ("pull", False)):
        src, dst = ("local", "remote") if up else ("remote", "local")
        sp = sub.add_parser(name, help=f"copy new and changed files from {src} to {dst}")
        sp.add_argument("paths", nargs="*", help="limit to these files or folders")
        sp.add_argument("-n", "--dry-run", action="store_true", help="show what would happen, change nothing")
        sp.add_argument("--delete", action="store_true",
                        help=f"also delete {dst} items that are not on {src} (only ones synced before, "
                             "unless --force)")
        sp.add_argument("--force", action="store_true",
                        help=f"overwrite or delete {dst} items even if they changed since the last sync")
        sp.add_argument("-y", "--yes", action="store_true", help="don't ask before deleting")
        sp.add_argument("--compare", choices=COMPARE_MODES, help="override [options] compare")
        sp.set_defaults(func=lambda a, up=up: cmd_transfer(a, up))

    sp = sub.add_parser("watch", help="upload local changes automatically as files are saved")
    sp.add_argument("--interval", type=float, help="seconds between checks (default [options] watch_interval)")
    sp.add_argument("--delete", action="store_true", default=None,
                    help="also delete remote files deleted locally (only ones synced before)")
    sp.set_defaults(func=cmd_watch)
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # odd file names must not crash a Windows console
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, TransportError, RemoteError) as e:
        print(f"{NAME}: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
