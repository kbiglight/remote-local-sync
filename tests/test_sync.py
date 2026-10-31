import io
import json
import os
import time

import pytest

from remote_local_sync import NAME, cli
from remote_local_sync.config import CONFIG_DIR, join_remote, load
from remote_local_sync.sync import Session
from conftest import write

T0 = 1_700_000_000  # fixed mtimes keep "which side is newer" deterministic


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_status_then_push_reaches_in_sync(proj, capsys):
    local, remote = proj
    write(local / "train.py", "print(1)\n", T0)
    write(local / "pkg" / "util.py", "x = 1\n", T0)
    write(local / "__pycache__" / "train.pyc", "junk")
    write(local / ".git" / "HEAD", "ref")

    code, out = run(capsys, "status")
    assert code == 1
    assert "L  train.py" in out and "L  pkg/" in out and "local only, 1 file" in out
    assert "__pycache__" not in out and ".git" not in out and CONFIG_DIR not in out

    code, out = run(capsys, "push")
    assert code == 0, out
    assert (remote / "train.py").read_text() == "print(1)\n"
    assert (remote / "pkg" / "util.py").read_text() == "x = 1\n"
    assert os.stat(remote / "train.py").st_mtime == T0  # timestamps preserved
    for junk in ("__pycache__", ".git", CONFIG_DIR):
        assert not (remote / junk).exists()

    code, out = run(capsys, "status")
    assert code == 0 and "in sync" in out


def test_modified_file_diff_and_push(proj, capsys):
    local, remote = proj
    write(local / "a.py", "one\ntwo\n", T0)
    run(capsys, "push")
    write(local / "a.py", "one\nTWO\n", T0 + 60)

    code, out = run(capsys, "status")
    assert code == 1 and "M  a.py" in out and "local newer" in out

    code, out = run(capsys, "diff", "a.py")
    assert code == 1 and "-two" in out and "+TWO" in out

    code, out = run(capsys, "push")
    assert code == 0 and (remote / "a.py").read_text() == "one\nTWO\n"
    assert run(capsys, "diff", "a.py") == (0, "a.py: identical\n")


def test_content_compare_sees_what_quick_misses(proj, capsys):
    local, remote = proj
    write(local / "w.txt", "aaaa", T0)
    write(remote / "w.txt", "bbbb", T0)  # same size, same mtime, different bytes

    assert run(capsys, "status", "--compare", "quick")[0] == 0
    code, out = run(capsys, "status")
    assert code == 1 and "M  w.txt" in out


def test_push_does_not_overwrite_remote_changes(proj, capsys):
    local, remote = proj
    write(local / "cfg.yaml", "lr: 1\n", T0)
    run(capsys, "push")
    write(remote / "cfg.yaml", "lr: 2  # edited on the server\n", T0 + 10)
    write(local / "cfg.yaml", "lr: 3\n", T0 + 20)  # local is newer, but the server copy changed too

    code, out = run(capsys, "push")
    assert code == 1 and "remote changed since last sync" in out
    assert "edited on the server" in (remote / "cfg.yaml").read_text()

    code, out = run(capsys, "push", "--force")
    assert code == 0 and (remote / "cfg.yaml").read_text() == "lr: 3\n"


def test_push_skips_newer_never_synced_remote_file(proj, capsys):
    local, remote = proj
    write(local / "notes.md", "local", T0)
    write(remote / "notes.md", "remote!", T0 + 100)

    code, out = run(capsys, "push")
    assert code == 1 and "never synced" in out
    assert (remote / "notes.md").read_text() == "remote!"


def test_remote_results_are_kept_by_delete_and_pulled(proj, capsys):
    local, remote = proj
    write(local / "train.py", "x", T0)
    write(remote / "runs" / "exp1" / "metrics.csv", "acc\n0.9\n", T0)

    code, out = run(capsys, "push", "--delete", "--yes")
    assert code == 1 and "never synced" in out
    assert (remote / "runs" / "exp1" / "metrics.csv").exists()

    code, out = run(capsys, "pull")
    assert code == 0, out
    assert (local / "runs" / "exp1" / "metrics.csv").read_text() == "acc\n0.9\n"
    assert run(capsys, "status")[0] == 0


def test_push_delete_removes_what_was_deleted_locally(proj, capsys):
    local, remote = proj
    write(local / "old" / "a.py", "a", T0)
    write(local / "keep.py", "k", T0)
    run(capsys, "push")
    (local / "old" / "a.py").unlink()
    (local / "old").rmdir()

    code, out = run(capsys, "push")
    assert code == 0 and "exist only on remote; kept" in out
    assert (remote / "old" / "a.py").exists()

    code, out = run(capsys, "push", "--delete", "--yes")
    assert code == 0, out
    assert not (remote / "old").exists() and (remote / "keep.py").exists()


def test_delete_needs_yes_without_a_terminal(proj, capsys, monkeypatch):
    local, remote = proj
    write(local / "a.py", "a", T0)
    run(capsys, "push")
    (local / "a.py").unlink()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    code, out = run(capsys, "push", "--delete")
    assert code == 1 and "aborted" in out and (remote / "a.py").exists()


def test_pull_does_not_overwrite_local_changes(proj, capsys):
    local, remote = proj
    write(local / "a.py", "v1", T0)
    run(capsys, "push")
    write(remote / "a.py", "v2 remote", T0 + 10)
    write(local / "a.py", "v2 local", T0 + 5)

    code, out = run(capsys, "pull")
    assert code == 1 and "local changed since last sync" in out
    assert (local / "a.py").read_text() == "v2 local"


def test_dry_run_changes_nothing(proj, capsys):
    local, remote = proj
    write(local / "a.py", "a", T0)
    code, out = run(capsys, "push", "--dry-run")
    assert code == 0 and "upload    a.py" in out and "dry run" in out
    assert not (remote / "a.py").exists()


def test_paths_limit_the_operation(proj, capsys, monkeypatch):
    local, remote = proj
    write(local / "a" / "x.py", "x", T0)
    write(local / "b" / "y.py", "y", T0)

    assert run(capsys, "push", "a")[0] == 0
    assert (remote / "a" / "x.py").exists() and not (remote / "b").exists()

    monkeypatch.chdir(local / "b")
    code, out = run(capsys, "status", ".")
    assert code == 1 and "L  b/" in out and "L  a/" not in out


def test_file_vs_folder_is_reported_not_resolved(proj, capsys):
    local, remote = proj
    write(local / "data", "a file", T0)
    write(remote / "data" / "x.csv", "1", T0)

    code, out = run(capsys, "status")
    assert code == 1 and "T  data" in out and "local is a file, remote is a folder" in out
    code, out = run(capsys, "push")
    assert code == 1 and (remote / "data" / "x.csv").exists()


def test_status_json(proj, capsys):
    local, remote = proj
    write(local / "a.py", "a", T0)
    code, out = run(capsys, "status", "--json")
    data = json.loads(out)
    assert code == 1 and data["in_sync"] is False
    assert [(i["path"], i["status"]) for i in data["mappings"][0]["items"]] == [("a.py", "L")]


def test_watch_uploads_saved_files_and_deletes(proj, capsys, monkeypatch):
    local, remote = proj
    write(local / "a.py", "v1", T0)
    steps = iter([
        lambda: write(local / "a.py", "v2", T0 + 10),          # saved: wait one round
        lambda: None,                                           # unchanged since: upload
        lambda: write(local / "new" / "b.py", "b", T0 + 20),   # new folder and file
        lambda: None,
        lambda: (local / "a.py").unlink(),                      # deleted locally
        lambda: None,
    ])
    real_sleep = time.sleep

    def fake_sleep(seconds):
        step = next(steps, None)
        step() if step else real_sleep(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    with Session(load(local)) as s:
        s.watch(interval=0, delete=True, rounds=6)
    out = capsys.readouterr().out

    assert "upload   a.py" in out and "mkdir    new/" in out and "upload   new/b.py" in out
    assert "delete   a.py" in out
    assert not (remote / "a.py").exists()
    assert (remote / "new" / "b.py").read_text() == "b"


def test_init_and_missing_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["status"]) == 2
    assert f"{NAME} init" in capsys.readouterr().err

    assert cli.main(["init", "--host", "myserver", "--remote", "projects/myproject"]) == 0
    cfg = load(tmp_path)
    assert cfg.server.host == "myserver" and cfg.mappings[0].remote == "~/projects/myproject"
    assert cli.main(["init", "--host", "myserver", "--remote", "x"]) == 2


@pytest.mark.parametrize("root, path, expected", [
    ("~", "projects/myproject", "~/projects/myproject"),
    ("/home/u/", "data/", "/home/u/data"),
    ("~", "/srv/data", "/srv/data"),
    ("C:/Users/me", "work", "C:/Users/me/work"),
    ("C:\\Users\\me", "work", "C:/Users/me/work"),
    ("~", "D:/work", "D:/work"),
    ("C:/", ".", "C:/"),
    ("/", ".", "/"),
])
def test_join_remote(root, path, expected):
    assert join_remote(root, path) == expected
