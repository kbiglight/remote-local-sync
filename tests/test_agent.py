import os

from remote_local_sync import agent
from conftest import write


def rels(result):
    return sorted(e[0] for e in result["entries"])


def test_scan_excludes_and_skips(tmp_path):
    write(tmp_path / "a.py", "a")
    write(tmp_path / "pkg" / "b.py", "b")
    write(tmp_path / "pkg" / "__pycache__" / "b.pyc", "x")
    write(tmp_path / "data" / "raw" / "big.bin", "x")
    write(tmp_path / (".x.py" + agent.TMP_SUFFIX), "half-written upload")
    os.symlink(tmp_path / "a.py", tmp_path / "link.py")

    result = agent.scan(str(tmp_path), ["__pycache__"], ["data/raw"])

    assert result["exists"]
    assert rels(result) == ["a.py", "data", "pkg", "pkg/b.py"]
    assert result["skipped"] == [["link.py", "symlink"]]


def test_scan_start_limits_to_a_subtree(tmp_path):
    write(tmp_path / "a.py", "a")
    write(tmp_path / "pkg" / "b.py", "b")

    assert rels(agent.scan(str(tmp_path), start="pkg")) == ["pkg", "pkg/b.py"]
    assert rels(agent.scan(str(tmp_path), start="a.py")) == ["a.py"]
    assert rels(agent.scan(str(tmp_path), start="missing")) == []


def test_scan_missing_root(tmp_path):
    assert agent.scan(str(tmp_path / "nope")) == {"exists": False, "entries": [], "skipped": []}


def test_writer_keeps_mode_of_replaced_file(tmp_path):
    target = write(tmp_path / "run.sh", "old")
    os.chmod(target, 0o755)
    src = write(tmp_path / "src.sh", "new")

    agent.copy_file(str(src), str(target), mode=None)

    assert target.read_text() == "new"
    assert os.stat(target).st_mode & 0o777 == 0o755
    assert not list(tmp_path.glob("*" + agent.TMP_SUFFIX))


def test_remove_folder_only_when_empty(tmp_path):
    write(tmp_path / "d" / "f", "x")
    try:
        agent.remove(str(tmp_path / "d"))
        assert False, "non-empty folder was removed"
    except OSError:
        pass
    assert agent.remove(str(tmp_path / "d" / "f"))
    assert agent.remove(str(tmp_path / "d"))
    assert not agent.remove(str(tmp_path / "d"))
