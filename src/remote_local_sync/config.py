"""Project settings: config.toml in the project's settings folder (CONFIG_DIR)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List, Optional

from .agent import NAME

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

CONFIG_DIR = "." + NAME
CONFIG_FILE = "config.toml"
STATE_FILE = "state.json"
COMPARE_MODES = ("content", "quick", "size", "timestamp")
DEFAULT_SSH = ["ssh", "-T", "-o", "ServerAliveInterval=30"]
DEFAULT_EXCLUDE_NAMES = [".git", ".idea", ".vscode", "__pycache__", "*.pyc", ".DS_Store",
                         ".venv", "venv", ".ipynb_checkpoints"]


class ConfigError(Exception):
    pass


@dataclass
class Server:
    type: str
    root: str
    host: str = ""
    port: Optional[int] = None
    user: Optional[str] = None
    python: str = "python3"
    ssh: List[str] = field(default_factory=lambda: list(DEFAULT_SSH))

    @property
    def label(self) -> str:
        if self.type == "local":
            return "local"
        return f"{self.user}@{self.host}" if self.user else self.host


@dataclass
class Mapping:
    key: str     # local folder relative to the project, "." for the project itself
    local: Path  # absolute local folder
    remote: str  # folder on the server, "/" separated, may start with ~


@dataclass
class Options:
    compare: str = "content"
    exclude_names: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_NAMES))
    exclude_paths: List[str] = field(default_factory=list)
    preserve_timestamps: bool = True
    use_temp_file: bool = True
    warn_newer: bool = True
    watch_delete: bool = False
    watch_interval: float = 1.0


@dataclass
class Config:
    project: Path
    server: Server
    mappings: List[Mapping]
    options: Options

    @property
    def state_path(self) -> Path:
        return self.project / CONFIG_DIR / STATE_FILE

    @property
    def exclude_names(self) -> List[str]:
        return [CONFIG_DIR] + self.options.exclude_names


def is_absolute_remote(path: str) -> bool:
    return path.startswith(("/", "~")) or (len(path) >= 2 and path[1] == ":")


def join_remote(root: str, path: str) -> str:
    """Join server paths with "/". An absolute path (/, ~, C:) replaces root."""
    path = path.replace("\\", "/")
    if is_absolute_remote(path):
        joined = path
    elif path in ("", "."):
        joined = root.replace("\\", "/")
    else:
        joined = root.replace("\\", "/").rstrip("/") + "/" + path
    joined = joined.rstrip("/")
    if not joined:
        return "/"
    if len(joined) == 2 and joined[1] == ":":  # "C:" alone means the drive's current folder
        return joined + "/"
    return joined


def find_project(start: Path) -> Path:
    start = start.resolve()
    for folder in [start, *start.parents]:
        if (folder / CONFIG_DIR / CONFIG_FILE).is_file():
            return folder
    raise ConfigError(f"no {CONFIG_DIR}/{CONFIG_FILE} in {start} or its parents; run `{NAME} init` first")


def _check_keys(section: str, data: dict, allowed) -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ConfigError(f"unknown key(s) in [{section}]: {', '.join(unknown)} "
                          f"(allowed: {', '.join(sorted(allowed))})")


def load(project: Path) -> Config:
    path = project / CONFIG_DIR / CONFIG_FILE
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found; run `{NAME} init` first") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None

    s = data.get("server", {})
    _check_keys("server", s, [f.name for f in fields(Server)])
    server_type = s.get("type", "ssh")
    if server_type == "ssh":
        if not s.get("host"):
            raise ConfigError("[server] host is required when type = \"ssh\"")
        root = str(s.get("root", "~"))
    elif server_type == "local":
        if not s.get("root"):
            raise ConfigError("[server] root is required when type = \"local\"")
        local_root = Path(str(s["root"])).expanduser()
        root = (project / local_root).resolve().as_posix()
    else:
        raise ConfigError(f"[server] type must be \"ssh\" or \"local\", not {server_type!r}")
    server = Server(type=server_type, root=root, host=str(s.get("host", "")),
                    port=s.get("port"), user=s.get("user"), python=str(s.get("python", "python3")),
                    ssh=[str(a) for a in s.get("ssh", DEFAULT_SSH)])

    raw_maps = data.get("mapping", [])
    if not raw_maps:
        raise ConfigError("add at least one [[mapping]] with local and remote")
    mappings = []
    for m in raw_maps:
        _check_keys("mapping", m, ["local", "remote"])
        local = (project / str(m.get("local", "."))).resolve()
        key = Path(os.path.relpath(local, project)).as_posix()
        if key.startswith(".."):
            raise ConfigError(f"mapping local {m.get('local')!r} is outside the project folder {project}")
        mappings.append(Mapping(key=key, local=local, remote=join_remote(root, str(m.get("remote", ".")))))

    o = data.get("options", {})
    _check_keys("options", o, [f.name for f in fields(Options)])
    options = Options(**o)
    if options.compare not in COMPARE_MODES:
        raise ConfigError(f"[options] compare must be one of {', '.join(COMPARE_MODES)}")
    return Config(project=project, server=server, mappings=mappings, options=options)


TEMPLATE = """\
# {name} 설정. 서버 쪽 경로는 서버가 Windows여도 / 로 적는다.

[server]
type = {type}  # "ssh": SSH로 접속(서버에 python 필요) / "local": 로컬 폴더나 마운트된 폴더
host = {host}  # ~/.ssh/config의 Host 이름 또는 user@hostname. type = "local"이면 쓰지 않음
# port = 22
python = {python}  # 서버에서 실행할 python. Windows 서버면 "python"
root = {root}  # mapping.remote의 기준 폴더. ~ 사용 가능
# ssh = ["ssh", "-T", "-o", "ServerAliveInterval=30"]  # ssh 옵션을 바꿀 때만

[[mapping]]
local = "."  # {config_dir} 폴더가 있는 폴더 기준
remote = {remote}  # server.root 기준. /, ~, C: 로 시작하면 절대 경로

[options]
compare = "content"  # content(내용) | quick(크기와 수정 시각이 같으면 같다고 봄) | size | timestamp
exclude_names = {exclude_names}
exclude_paths = []  # mapping 기준 경로. 예: ["data/raw", "runs"]
preserve_timestamps = true  # 보낸 파일의 수정 시각을 원본과 같게 맞춤
use_temp_file = true  # 임시 이름으로 다 받은 뒤 이름을 바꿔 덮어씀
warn_newer = true  # 마지막 동기화 뒤 받는 쪽이 바뀌었으면 덮어쓰지 않고 건너뜀
watch_delete = false  # watch 중 로컬에서 지운 파일을 서버에서도 지움
watch_interval = 1.0  # watch 검사 주기(초)
"""


def write_template(project: Path, server_type: str, host: str, remote: str, root: str, python: str) -> Path:
    path = project / CONFIG_DIR / CONFIG_FILE
    if path.exists():
        raise ConfigError(f"{path} already exists")
    path.parent.mkdir(exist_ok=True)
    text = TEMPLATE.format(name=NAME, config_dir=CONFIG_DIR, type=json.dumps(server_type), host=json.dumps(host), python=json.dumps(python),
                           root=json.dumps(root), remote=json.dumps(remote),
                           exclude_names=json.dumps(DEFAULT_EXCLUDE_NAMES))
    path.write_text(text, encoding="utf-8")
    return path
