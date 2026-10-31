# relosy

**원격 폴더와 로컬 폴더를 같은 상태로 맞추는 명령줄 프로그램**

[빠르게 시작](#빠르게-시작) · [요구 사항](#요구-사항) · [설치](#설치) · [사용법](#사용법) · [설정](#설정) · [안전 규칙](#안전-규칙) · [동작 방식](#동작-방식) · [문제 해결](#문제-해결) · [개발](#개발)

서버의 폴더(원격)와 내 컴퓨터의 폴더(로컬)를 같은 상태로 맞춘다. 그래서 서버에 접속하지 않아도 로컬에서 서버와 같은 파일을 볼 수 있다. 저장할 때마다 자동으로 올릴 수도 있다.

옮기기 전에는 무엇이 다른지 항목마다 보여 주고, 무엇을 옮길지는 사용자가 정한다. 마지막 동기화 때의 상태도 기억해 둔다. 그래서 받는 쪽이 그 뒤에 바뀌었으면 덮어쓰지 않고 건너뛴다.

```
C:\work\myproject  <->  myserver:~/projects/myproject   (compare: content)
  M  config.yaml                                  modified, remote newer
  R  runs/                                        remote only, 1 file
  M  train.py                                     modified, local newer
0 identical, 2 modified, 1 remote only -> NOT in sync
```

## 핵심 기능

- **옮기기 전에 보여 준다**: `relosy status`로 다른 점을 항목마다 보고, `relosy diff`로 내용 차이를 본 뒤 옮길 것을 정한다. 같으면 종료 코드 0, 다르면 1이라 스크립트에서도 쓸 수 있다.
- **상대 쪽 작업을 지킨다**: 마지막 동기화 뒤에 받는 쪽이 바뀌었으면 덮어쓰지 않는다. 한 번도 동기화한 적 없는 파일(예: 서버에서 나온 실험 결과)은 지우지 않는다.
- **바뀐 것만 옮긴다**: 크기가 같은 파일은 양쪽이 각자 sha256 지문을 계산해 비교한다. 비교하려고 파일 내용을 네트워크로 받아 오지 않는다.
- **서버에 설치할 것이 없다**: ssh로 접속해 서버의 `python3`로 relosy의 agent를 실행한다. agent는 표준 라이브러리만 쓴다.
- **Windows와 Linux**: 어느 쪽에서든 실행한다. 서버 쪽 경로는 언제나 `/`로 적는다.
- **저장하면 자동으로 올린다**: `relosy watch`를 켜 두면 저장이 끝난 파일을 바로 서버에 올린다.

## 빠르게 시작

Windows의 `C:\work\myproject`와 서버의 `~/projects/myproject`를 맞추는 예시다. [설치](#설치)를 마친 뒤 **PowerShell**에서 실행한다. `myserver`는 `~/.ssh/config`에 적은 서버 이름이고, `C:\work\myproject`와 `projects/myproject`는 자기 폴더로 바꾼다.

```powershell
# 1) 맞출 로컬 폴더로 들어간다.
cd C:\work\myproject

# 2) 설정 파일 .relosy\config.toml을 만든다.
#    --host: ssh로 접속할 서버 이름 / --remote: 서버 폴더 (서버의 홈 폴더 ~ 기준)
relosy init --host myserver --remote projects/myproject
# 기대 결과: created C:\work\myproject\.relosy\config.toml

# 3) 두 폴더를 비교만 한다. 아무것도 바꾸지 않는다.
relosy status
# 기대 결과: 다른 파일 목록과 마지막 줄 "... -> in sync" 또는 "... -> NOT in sync"

# 4) 무엇을 올릴지 미리 본다. -n(--dry-run): 실제로는 아무것도 바꾸지 않는다.
relosy push -n

# 5) 바뀐 파일을 서버에 올린다.
relosy push
# 기대 결과: 마지막 줄 "N uploaded"

# 6) 저장할 때마다 자동으로 올린다. 끝낼 때는 Ctrl+C를 누른다.
relosy watch
```

## 요구 사항

| | 내 컴퓨터 (relosy를 실행하는 곳) | 서버 |
|---|---|---|
| 운영체제 | Windows 10 1809 이상, Linux | Linux (Windows 서버도 고려해 만들었지만 아직 확인하지 않았다) |
| Python | 3.9 이상. 설치할 때 uv가 알아서 받는다 | 3.6 이상의 `python3`. 서버에 따로 설치할 것은 없다 |
| 그 밖의 프로그램 | [uv](https://docs.astral.sh/uv/) (설치 도구), OpenSSH 클라이언트 `ssh` | ssh 접속. 매번 비밀번호를 묻지 않도록 키 로그인을 권한다 |
| 런타임 의존성 | `tomli` 하나 (Python 3.11 미만일 때만, 자동 설치) | 없음 |

서버 폴더가 이 컴퓨터에서 바로 보이는 경우에는 `type = "local"`을 쓴다. 마운트한 네트워크 폴더나 Google Drive for desktop의 `G:/내 드라이브/myproject` 같은 폴더가 여기에 해당한다. 이때는 서버 쪽 요구 사항이 없다.

macOS에서는 아직 실행해 보지 않았다.

## 설치

설치는 세 단계다. ① uv와 ssh를 준비하고, ② relosy를 설치하고, ③ 서버에 키 로그인을 설정한다.

relosy는 GitHub 저장소에서 바로 설치한다. 그래서 설치하는 컴퓨터에 git이 있어야 한다. Windows에서 `git --version`이 동작하지 않으면 [Git for Windows](https://git-scm.com/download/win)를 먼저 설치한다(무료).

<details>
<summary><b>Windows (PowerShell)</b></summary>

**① OpenSSH 클라이언트와 uv를 준비한다.**

```powershell
# OpenSSH 클라이언트가 있는지 확인한다.
ssh -V
# 기대 결과: "OpenSSH_for_Windows_..."로 시작하는 줄
# 없으면 시작 메뉴에서 "선택적 기능"(Optional Features)을 검색해 열고,
# "기능 추가"에서 "OpenSSH 클라이언트"(OpenSSH Client)를 설치한다.

# uv를 설치한다. -ExecutionPolicy ByPass: 이 명령 하나만 스크립트 실행 제한을 풀고 실행한다.
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# 기대 결과: 설치가 끝났다는 메시지. 그 뒤 PowerShell 창을 새로 연다.
```

**② relosy를 설치한다.**

```powershell
# GitHub 저장소에서 바로 설치한다.
#   --managed-python: uv가 받은 Python만 쓴다. conda 같은 다른 Python과 섞이지 않게 한다.
#   --python 3.12: Python 3.12로 설치한다. 없으면 uv가 받는다.
uv tool install --managed-python --python 3.12 git+https://github.com/kbiglight/remote-local-sync
# 기대 결과: "Installed 1 executable: relosy"
# relosy.exe는 C:\Users\<사용자>\.local\bin에 생긴다.

# 설치를 확인한다.
relosy --version
# 기대 결과: relosy 0.1.0
# "relosy를 찾을 수 없다"고 나오면 uv tool update-shell을 실행하고 PowerShell 창을 새로 연다.
```

</details>

<details>
<summary><b>Linux (bash)</b></summary>

```bash
# uv가 없으면 설치한다. 이미 있으면 건너뛴다.
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
# relosy를 설치한다.
uv tool install git+https://github.com/kbiglight/remote-local-sync
# 기대 결과: "Installed 1 executable: relosy"

relosy --version
# 기대 결과: relosy 0.1.0
```

</details>

<details>
<summary><b>서버에 키 로그인 설정 (권장)</b></summary>

relosy는 명령을 실행할 때마다 ssh로 접속한다. 키 로그인을 설정하면 비밀번호를 묻지 않는다. 아래는 Windows PowerShell 기준이다.

```powershell
# 키 쌍을 만든다. 이미 C:\Users\<사용자>\.ssh\id_ed25519가 있으면 건너뛴다.
ssh-keygen -t ed25519

# 공개 키를 서버의 ~/.ssh/authorized_keys 끝에 덧붙인다(>>). 기존 내용은 지우지 않는다.
# 이번 한 번은 서버 비밀번호를 묻는다.
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh myserver "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"

# 확인한다.
ssh myserver python3 --version
# 기대 결과: 비밀번호를 묻지 않고 "Python 3.x.x"가 나온다.
```

`C:\Users\<사용자>\.ssh\config`에 서버를 이름으로 적어 두면 `myserver`처럼 짧게 부를 수 있다.

```
Host myserver
    HostName <서버 주소>
    User <서버 사용자 이름>
```

</details>

**새 버전으로 바꾸거나 지우기**

```powershell
# GitHub 저장소의 최신판으로 바꾼다.
uv tool upgrade relosy
```

```powershell
# relosy를 지운다. 프로젝트 폴더의 .relosy 설정과 기록은 남는다.
uv tool uninstall relosy
```

## 사용법

모든 명령은 `.relosy/config.toml`이 있는 프로젝트 폴더나 그 하위 폴더에서 실행한다. 경로를 주면 그 파일이나 폴더만 다룬다.

### 처음 설정: `relosy init`

현재 폴더에 `.relosy/config.toml`을 만든다. 만든 뒤 `[server]`와 `[[mapping]]`을 확인한다. 자세한 항목은 [설정](#설정)에 있다.

```powershell
# ssh 서버와 맞출 때. --remote는 서버 홈 폴더(~) 기준이다.
relosy init --host myserver --remote projects/myproject

# 이 컴퓨터에서 보이는 폴더와 맞출 때. --root에 그 폴더의 상위 폴더를 준다.
relosy init --type local --root "G:/내 드라이브" --remote myproject
```

### 같은지 확인: `relosy status`

양쪽을 비교해 다른 파일만 보여 준다. 아무것도 바꾸지 않는다.

```powershell
relosy status              # 전체 비교
relosy status src data     # src와 data 폴더만 비교
relosy status -a           # -a(--all): 같은 파일과 건너뛴 항목(symlink 등)도 보여 준다
relosy status -q           # -q(--quiet): 마지막 요약 줄만 보여 준다
relosy status --json       # 프로그램이 읽기 좋은 JSON으로 출력한다
relosy status --compare quick   # 이번만 비교 방식을 바꾼다
```

| 기호 | 뜻 |
|---|---|
| `=` | 같음 (`-a`일 때만 보인다) |
| `M` | 양쪽에 있고 내용이 다름. 어느 쪽이 최근인지 함께 보여 준다 |
| `L` | 로컬에만 있음 |
| `R` | 서버에만 있음 |
| `T` | 한쪽은 파일, 다른 쪽은 폴더. 직접 정리해야 한다 |

### 파일 하나의 차이 보기: `relosy diff`

서버 사본을 기준으로 로컬 사본에서 무엇이 바뀌었는지 줄 단위로 보여 준다. `-`는 서버에만 있는 줄, `+`는 로컬에만 있는 줄이다.

```
$ relosy diff config.yaml
--- remote/config.yaml
+++ local/config.yaml
@@ -1 +1 @@
-lr: 0.05
+lr: 0.1
```

4 MiB보다 큰 파일과 바이너리 파일은 같은지 다른지만 알려 준다.

### 올리기와 받기: `relosy push`, `relosy pull`

`push`는 로컬에서 서버로, `pull`은 서버에서 로컬로 다른 파일만 옮긴다.

```powershell
relosy push -n             # -n(--dry-run): 무엇을 할지 보여 주기만 한다
relosy push                # 새 파일과 바뀐 파일을 올린다
relosy pull runs           # 서버의 runs 폴더만 내려받는다
relosy push --delete       # 로컬에서 지운 파일을 서버에서도 지운다 (지우기 전에 묻는다)
```

받는 쪽이 마지막 동기화 뒤에 바뀌었으면 그 파일은 건너뛰고 이유를 보여 준다.

```
$ relosy push --delete
  skip      config.yaml: remote changed since last sync (--force to override)
  skip      runs/result.txt: exists only on remote and was never synced (--force to override)
  upload    train.py
1 uploaded, 2 skipped
```

건너뛴 파일은 `relosy diff`로 차이를 확인한 뒤 정한다. 서버 쪽이 맞으면 `relosy pull 파일`로 받고, 로컬 쪽이 맞으면 `relosy push --force 파일`로 덮어쓴다.

> [!WARNING]
> `--force`는 안전 규칙을 끈다. 서버에서 바뀐 파일을 덮어쓰고, `--delete`와 함께 쓰면 한 번도 동기화한 적 없는 파일까지 지운다. 먼저 `-n`으로 무엇이 바뀌는지 확인한다.

| 옵션 | 뜻 |
|---|---|
| `-n`, `--dry-run` | 할 일을 보여 주기만 하고 아무것도 바꾸지 않는다 |
| `--delete` | 보내는 쪽에 없는 파일을 받는 쪽에서 지운다. 예전에 동기화했던 파일만 지운다 |
| `--force` | 받는 쪽이 바뀌었어도 덮어쓰거나 지운다 |
| `-y`, `--yes` | 지우기 전에 묻지 않는다. 터미널이 아닌 곳(스크립트 등)에서 지우려면 필요하다 |
| `--compare` | 이번만 비교 방식을 바꾼다 (`content`, `quick`, `size`, `timestamp`) |

### 자동으로 올리기: `relosy watch`

1초마다 로컬 폴더를 훑어서, 바뀐 파일이 두 번 연속 같은 모습이면 저장이 끝났다고 보고 올린다. 그래서 쓰는 중인 파일은 보내지 않는다. 새 폴더는 바로 서버에 만든다. 끝낼 때는 Ctrl+C를 누른다.

```
$ relosy watch
watching C:\work\myproject  ->  myserver:~/projects/myproject
uploading changes every 1.0s. Ctrl+C to stop.
14:02:11  upload   train.py
14:02:30  mkdir    figures/
14:02:31  upload   figures/loss.png
```

- watch는 올리기만 한다. 서버에서 바뀐 파일은 `relosy pull`로 받는다.
- watch를 켜기 전부터 있던 차이는 그 파일이 다시 바뀔 때 올린다. 바로 맞추려면 먼저 `relosy push`를 실행한다.
- `--delete`를 주거나 설정에서 `watch_delete = true`로 두면, 로컬에서 지운 파일을 서버에서도 지운다. 이때도 예전에 동기화했고 그 뒤 서버에서 바뀌지 않은 파일만 지운다.
- `--interval 5`처럼 주면 검사 주기를 초 단위로 바꾼다.
- 접속이 끊기면 5초 뒤 다시 접속한다.

### 종료 코드

스크립트에서 결과를 판단할 때 쓴다.

| 명령 | 0 | 1 | 2 |
|---|---|---|---|
| `status` | 같음 | 다름 | 설정 오류, 접속 실패 |
| `diff` | 같음 | 다름, 한쪽에만 있음 | 양쪽에 없음, 서버 사본을 읽지 못함, 설정 오류, 접속 실패 |
| `push`, `pull` | 모두 처리함 (`-n` 포함) | 건너뛴 파일이나 오류가 있음, 삭제를 취소함 | 설정 오류, 접속 실패 |

Ctrl+C로 중단하면 130이다. `watch`를 Ctrl+C로 끝내면 0이다.

## 설정

설정은 프로젝트 폴더의 `.relosy/config.toml` 하나다. `relosy init`이 아래와 같은 파일을 만든다.

```toml
[server]
type = "ssh"              # "ssh": ssh로 접속 / "local": 이 컴퓨터에서 보이는 폴더
host = "myserver"              # ~/.ssh/config의 Host 이름 또는 user@hostname
python = "python3"        # 서버에서 agent를 실행할 python. Windows 서버면 "python"
root = "~"                # 서버 쪽 기준 폴더

[[mapping]]               # 로컬 폴더와 서버 폴더의 짝. 여러 개 둘 수 있다
local = "."               # .relosy 폴더가 있는 프로젝트 폴더 기준
remote = "projects/myproject"   # root 기준. 그래서 서버의 ~/projects/myproject

[options]
compare = "content"
exclude_names = [".git", ".idea", ".vscode", "__pycache__", "*.pyc", ".DS_Store", ".venv", "venv", ".ipynb_checkpoints"]
exclude_paths = []        # 예: ["data/raw", "runs"]
preserve_timestamps = true
use_temp_file = true
warn_newer = true
watch_delete = false
watch_interval = 1.0
```

### `[server]`

| 항목 | 기본값 | 뜻 |
|---|---|---|
| `type` | `"ssh"` | `"ssh"`는 ssh로 접속해 서버에서 agent를 실행한다. `"local"`은 이 컴퓨터에서 보이는 폴더를 직접 읽고 쓴다 |
| `host` | 없음 | ssh 접속 대상. `type = "ssh"`이면 꼭 적는다 |
| `port`, `user` | 없음 | ssh 포트와 사용자. 보통은 `~/.ssh/config`에 적는 편이 낫다 |
| `python` | `"python3"` | 서버에서 실행할 python |
| `root` | `"~"` | `mapping.remote`의 기준 폴더. `type = "local"`이면 꼭 적고, 상대 경로면 프로젝트 폴더 기준이다 |
| `ssh` | `["ssh", "-T", "-o", "ServerAliveInterval=30"]` | 실행할 ssh 명령과 옵션. 특정 ssh를 쓰거나 옵션을 바꿀 때만 적는다 |

### `[[mapping]]`

로컬 폴더 하나와 서버 폴더 하나를 짝짓는다. 여러 개를 적으면 명령 한 번에 모두 다룬다.

```toml
[[mapping]]
local = "code"                       # 프로젝트의 code 폴더를
remote = "projects/myproject/code"         # 서버의 ~/projects/myproject/code와 맞춘다

[[mapping]]
local = "results"
remote = "/data/<사용자>/results"   # /, ~, C:로 시작하면 root를 무시한 절대 경로
```

- `local`은 프로젝트 폴더 안이어야 한다.
- 한 mapping 안에 다른 mapping이 들어 있으면, 안쪽 폴더는 안쪽 mapping으로만 다룬다.
- 서버 경로는 서버가 Windows여도 `/`로 적는다.

### `[options]`

| 항목 | 기본값 | 뜻 |
|---|---|---|
| `compare` | `"content"` | 비교 방식. 아래 표를 본다 |
| `exclude_names` | 위 목록 | 이 이름(와일드카드 `*` 가능)의 파일과 폴더는 어느 깊이에 있든 뺀다. 적으면 기본 목록을 대신하므로 필요한 기본값도 함께 적는다 |
| `exclude_paths` | `[]` | mapping 기준 경로를 정확히 적어 뺀다. 폴더를 적으면 그 안이 모두 빠진다 |
| `preserve_timestamps` | `true` | 보낸 파일의 수정 시각을 원본과 같게 맞춘다 |
| `use_temp_file` | `true` | 임시 이름(`.이름.relosy-tmp`)으로 다 받은 뒤 이름을 바꾼다. 중간에 끊겨도 반쯤 쓴 파일이 남지 않는다 |
| `warn_newer` | `true` | 받는 쪽이 마지막 동기화 뒤에 바뀌었으면 덮어쓰지 않는다. `false`로 두면 이 검사를 끈다 |
| `watch_delete` | `false` | watch 중 로컬에서 지운 파일을 서버에서도 지운다 |
| `watch_interval` | `1.0` | watch 검사 주기(초) |

`.relosy` 폴더와 `*.relosy-tmp` 임시 파일은 설정과 관계없이 언제나 뺀다. 모르는 항목을 적으면 오류와 함께 쓸 수 있는 항목을 알려 준다.

| `compare` | 같다고 보는 조건 | 특징 |
|---|---|---|
| `content` | 크기가 같고 sha256 지문이 같다 | 가장 정확하다. 크기가 같은 파일은 양쪽이 각자 끝까지 읽는다 |
| `quick` | 크기가 같고 수정 시각 차이가 1초 이내다. 시각이 다르면 지문으로 확인한다 | 빠르다. 내용만 바뀌고 크기와 시각이 같은 드문 경우를 놓친다 |
| `size` | 크기가 같다 | 가장 빠르고 가장 거칠다 |
| `timestamp` | 수정 시각 차이가 1초 이내다 | 내용을 보지 않는다 |

## 안전 규칙

relosy는 확실하지 않으면 건너뛰고, 사용자가 `--force`로 정하게 한다.

- **덮어쓰기**: 받는 쪽이 마지막 동기화 뒤에 바뀌었으면 건너뛴다. 동기화 기록이 없는 파일은 받는 쪽이 1초 넘게 더 최근일 때 건너뛴다.
- **지우기**: `--delete`는 예전에 동기화했던 파일만 지운다. 서버에서 새로 생긴 실험 결과처럼 한 번도 동기화한 적 없는 파일은 지우지 않는다. 지우기 전에 목록을 보여 주고 묻는다.
- **폴더**: 폴더는 비어 있을 때만 지운다. 제외된 파일이 남아 있으면 폴더를 그대로 둔다.
- **파일과 폴더 충돌**: 한쪽은 파일, 다른 쪽은 폴더(`T`)면 `--force`를 줘도 건드리지 않는다.
- **symlink**: 따라가지 않고 건너뛴다. 링크로 걸어 둔 데이터셋을 통째로 복사하지 않기 위해서다.
- **`.git`**: 작업 폴더가 git 저장소여도 `.git` 폴더는 옮기지 않는다. 파일 단위로 옮기면 저장소가 깨질 수 있기 때문이다.

## 동작 방식

relosy는 명령마다 다섯 단계를 거친다.

```
① 설정 읽기    .relosy/config.toml에서 서버와 폴더 짝을 읽는다
② 목록 만들기  로컬 폴더를 훑고, 서버 폴더도 훑는다 (서버 쪽은 서버가 직접 훑는다)
③ 비교        파일마다 = M L R T 가운데 하나로 나눈다
④ 계획        올릴 것, 건너뛸 것, 지울 것을 정한다   ← 안전 규칙은 여기서 작동한다
⑤ 실행·기록   파일을 보내고, 양쪽의 크기와 수정 시각을 .relosy/state.json에 적는다
```

**마지막 동기화 기록.** 수정 시각만 보면 "서버 사본이 그냥 예전 것"인지 "마지막 동기화 뒤에 서버에서 누가 고친 것"인지 구분할 수 없다. 그래서 relosy는 파일을 옮기거나 같다고 확인할 때마다 양쪽의 크기와 수정 시각을 적어 둔다. 다음번에 받는 쪽 사본이 기록과 다르면 그 사이에 바뀐 것으로 보고 건너뛴다.

```
"train.py": { "l": [12, 1791842590803809350],   ← 로컬: 크기 12바이트, 수정 시각(나노초)
              "r": [12, 1791842590803809350] }  ← 서버: 이때 두 사본이 같았다
```

**서버와의 통신.** `type = "ssh"`이면 relosy는 사용자의 `ssh` 명령으로 접속한다. 그래서 `~/.ssh/config`, 키, ProxyJump 설정이 그대로 적용된다. 접속하면 agent(`agent.py`)를 압축해 명령 한 줄에 실어 보내고, 서버의 python이 이를 실행한다. 그 뒤에는 JSON 한 줄씩 요청과 답을 주고받는다.

```
서버의 첫 인사   {"relosy": 1, "os": "posix", "python": "3.9"}
relosy의 요청     {"op": "hash", "root": "~/projects/myproject", "rels": ["train.py"]}
서버의 답        {"ok": true, "result": {"train.py": "59e9044c4320..."}}
```

서버 로그인 때 환영 메시지가 출력되어도 괜찮다. relosy는 agent의 첫 인사가 나올 때까지 다른 출력을 건너뛴다.

## 문제 해결

### `connection closed before the agent started`

ssh 접속에 실패했거나 서버에 `python3`가 없다. 같은 셸에서 직접 확인한다.

```powershell
# 비밀번호 없이 접속되고 Python 버전이 나와야 한다.
ssh myserver python3 --version
```

접속이 안 되면 ssh 설정과 키를 확인한다. 서버의 python 이름이 다르면 `[server] python`을 고친다. 예: `python = "/usr/bin/python3.8"`

### Windows에서 셸마다 다른 ssh가 잡힌다

PowerShell에서는 Windows의 `C:\Windows\System32\OpenSSH\ssh.exe`가, Git Bash에서는 Git에 들어 있는 `/usr/bin/ssh`가 먼저 잡힌다. 두 ssh는 설정과 키를 다르게 읽을 수 있다. 어느 셸에서든 같은 ssh를 쓰려면 설정에 경로를 적는다.

```toml
[server]
ssh = ["C:/Windows/System32/OpenSSH/ssh.exe", "-T", "-o", "ServerAliveInterval=30"]
```

### 설치할 때 conda 같은 다른 Python이 잡힌다

uv는 자기가 받은 Python이 없으면 PATH에 있는 Python을 먼저 쓴다. `uv tool install`에 `--managed-python --python 3.12`를 주면 uv가 받은 Python만 쓴다.

### `skip ... remote changed since last sync`

마지막 동기화 뒤에 서버 사본이 바뀌었다. `relosy diff 파일`로 차이를 본 뒤, 서버 쪽이 맞으면 `relosy pull 파일`, 로컬 쪽이 맞으면 `relosy push --force 파일`을 실행한다. 받을 때는 같은 이유로 `local changed since last sync`가 나온다.

### `not a terminal: pass --yes to allow deleting`

지울 것이 있는데 확인을 물을 터미널이 없다. 스크립트에서 실행한 경우다. 먼저 `-n`으로 지울 목록을 확인하고, 괜찮으면 `--yes`를 붙인다.

### 내용이 같은데 `M`으로 나온다

양쪽에서 git으로 따로 체크아웃한 사본은 줄바꿈(CRLF와 LF)이 다를 수 있다. relosy는 바이트 단위로 비교하므로 다르다고 본다. relosy로 옮긴 사본은 바이트 그대로라서 이 문제가 없다.

## 자주 묻는 질문

**`.relosy/state.json`을 지우면 어떻게 되나?**
모든 파일을 한 번도 동기화한 적 없는 파일로 본다. 지우기는 하지 않고, 받는 쪽이 더 최근인 파일은 덮어쓰지 않는다. 다음 `push`나 `pull`에서 같은 파일부터 다시 기록한다.

**서버나 서버 폴더를 바꾸면?**
이전 기록은 쓰지 않고 새로 시작한다.

**파일 이름을 바꾸면?**
옛 이름을 지우고 새 이름을 만든 것으로 처리한다. 옛 이름까지 지우려면 `--delete`를 준다.

**`status`도 기록을 남기나?**
아니다. `status`는 읽기만 한다. 기록은 `push`, `pull`, `watch`가 남긴다.

## 아직 안 되는 것

- GUI가 없다. CLI를 먼저 만들었다.
- 파일을 하나 보낼 때마다 왕복이 한 번 생긴다. 작은 파일이 수천 개면 느리다.
- 양쪽이 모두 바뀌었을 때 파일마다 방향을 고르는 기능이 없다. 지금은 `push`와 `pull`을 나눠서 실행한다.
- Windows는 대소문자를 구분하지 않는다. 서버에 `A.py`와 `a.py`가 함께 있으면 충돌한다.
- `watch`가 멈추는 경우가 두 가지 있다. 로컬에 새로 만든 폴더와 같은 이름의 파일이 서버에 있을 때, 그리고 `type = "local"`에서 지운 폴더의 서버 쪽에 동기화하지 않은 파일이 남아 있을 때다. 서버 파일은 지워지지 않는다. 멈추면 원인을 정리하고 다시 실행한다.
- 실제 ssh 접속, Windows, 서버의 Python 3.6~3.8에서는 아직 충분히 확인하지 않았다.

## 개발

### 코드 구조

파일 여섯 개가 네 층으로 쌓여 있다. 위층은 바로 아래층만 부른다.

```
src/remote_local_sync/
 cli.py        [명령줄]   인자를 해석하고, 화면에 출력하고, 지우기 전에 묻는다
   │
 sync.py       [엔진]     비교, 계획(안전 규칙), 실행, watch
   ├ config.py            .relosy/config.toml 읽기, init 틀
   └ state.py             마지막 동기화 기록(.relosy/state.json)
   │
 transport.py  [통신]     LocalTransport: 이 컴퓨터에서 보이는 폴더
   │                      AgentTransport: ssh로 서버에서 agent 실행
   │
 agent.py      [파일 작업] 목록, 지문, 읽기·쓰기, 삭제
                          로컬에서 import하고, 서버에서도 같은 코드가 돈다
```

제품 이름(`relosy`)은 `agent.py`의 `NAME` 한 곳에서 정한다. 설정 폴더, 임시 파일 꼬리표, 명령 안내문은 모두 이 값에서 만들어진다. 이름을 바꿀 때는 `NAME`과 `pyproject.toml`의 `name`, `[project.scripts]` 두 곳을 함께 고친다. 하나라도 빠뜨리면 `tests/test_name.py`가 실패한다. Python 패키지 폴더는 프로젝트 이름인 `remote_local_sync`로 고정되어 있어서, 제품 이름이 바뀌어도 코드 위치는 그대로다.

`agent.py`는 서버의 python이 무엇이든 돌아야 하므로 표준 라이브러리만 쓰고 Python 3.6 문법을 지킨다.

### 테스트와 빌드

Linux bash에서 실행한다.

```bash
# 저장소를 받아서 들어간다.
git clone https://github.com/kbiglight/remote-local-sync
cd remote-local-sync
```

```bash
# 테스트를 모두 돌린다.
uv run pytest
# 기대 결과: 마지막 줄 "49 passed"

# dist/에 설치 파일(wheel과 소스 묶음)을 만든다.
uv build
# 기대 결과: dist/relosy-0.1.0-py3-none-any.whl, dist/relosy-0.1.0.tar.gz
```

동기화 테스트는 같은 시나리오를 두 방식으로 각각 돌린다. 하나는 서버 폴더를 직접 읽고 쓰는 local 방식이다. 다른 하나는 agent를 실제로 띄워 JSON으로 통신하는 방식인데, `ssh` 대신 `sh -c`로 같은 명령 문자열을 실행한다. 실제 ssh 접속은 테스트하지 않는다.

## 비용

모두 무료이고 사용량 한도가 없다. 외부 서비스나 API를 쓰지 않는다. 제약은 SSH 서버와 네트워크 대역폭뿐이다.

- Python: 무료 (PSF 라이선스)
- uv: 무료 (MIT 또는 Apache-2.0 가운데 선택)
- OpenSSH 클라이언트: Windows 10 1809 이상에 들어 있는 기능이라 추가 비용이 없다. Windows Update로 갱신된다

## 라이선스

MIT 라이선스를 따른다. 전문은 [LICENSE](LICENSE)에 있다.
