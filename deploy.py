#!/usr/bin/env python3
"""
🚀 Copilot Local Agent Stack — 一键部署脚本
详细用法请运行: python3 deploy.py --help
"""

import argparse
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ════════════════════════════  常量  ════════════════════════════

COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
OPENCLAW_PORT     = 18789

# curl 健康检查可接受的 HTTP 状态码
OPENCLAW_OK_CODES: Tuple[str, ...] = ("200", "401", "302")

# ════════════════════════════  终端输出  ════════════════════════════

def _c(code: str, text: str) -> str:
    """包裹 ANSI 颜色码，终端不支持时原样返回。"""
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text

def info(msg: str) -> None:    print(f"  {_c('1;34', 'ℹ')} {msg}")
def ok(msg: str) -> None:      print(f"  {_c('1;32', '✔')} {msg}")
def warn(msg: str) -> None:    print(f"  {_c('1;33', '⚠')} {msg}")
def fail(msg: str) -> None:    print(f"  {_c('1;31', '✘')} {msg}")
def header(msg: str) -> None:  print(f"\n{_c('1;36', '━' * 50)}\n  {_c('1;36', msg)}\n{_c('1;36', '━' * 50)}")
def step(n: int, msg: str) -> None: print(f"\n  {_c('1;35', f'[{n}]')} {_c('1', msg)}")

def _mask_tokens(text: str) -> str:
    """遮蔽文本中的 ghu_ 令牌，避免泄露。"""
    return re.sub(r'ghu_\S+', 'ghu_********', text)

# ════════════════════════════  底层工具  ════════════════════════════

def _shell_quote(s: str) -> str:
    """对 shell 参数进行安全引用，防止注入。"""
    return shlex.quote(s)


def run(cmd: str, *, check: bool = True, capture: bool = True,
        timeout: int = 120) -> Optional[str]:
    """执行 shell 命令，返回 stdout（capture=True）或空串（capture=False）。

    失败（check=True 且返回码非零）或超时返回 None。
    注意：capture=False 时成功返回空字符串 ""，需用 `is None` 判断失败。
    """
    try:
        r = subprocess.run(cmd, shell=True, capture_output=capture,
                           text=True, timeout=timeout)
        if check and r.returncode != 0:
            return None
        return r.stdout.strip() if capture else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

def cmd_exists(name: str) -> bool:
    """检查命令是否在 PATH 中"""
    return shutil.which(name) is not None

def compose_cmd() -> str:
    """返回可用的 docker compose 命令（优先插件形式，其次独立命令）。"""
    if run("docker compose version", check=True, timeout=10) is not None:
        return "docker compose"
    if cmd_exists("docker-compose"):
        return "docker-compose"
    return "docker compose"

def http_json(method: str, url: str, data: Optional[Any] = None,
              headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """发送 HTTP 请求并解析 JSON 响应（不依赖 requests 库）。"""
    hdr = {"Accept": "application/json"}
    if headers:
        hdr.update(headers)
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

def get_project_dir() -> Path:
    """返回脚本所在目录（项目根）"""
    return Path(__file__).resolve().parent

def _check_http(port: int, ok_codes: Tuple[str, ...] = ("200",)) -> bool:
    """对 localhost:<port> 做 HTTP 状态码检查，命中 ok_codes 返回 True。"""
    result = run(f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{port}",
                 check=False, timeout=10)
    return bool(result and result.strip("'") in ok_codes)


def _show_cmd_output(cmd: str, label: str, *, mask: bool = True) -> Optional[str]:
    """执行命令并显示输出。无输出时打印警告。返回原始输出。"""
    out = run(cmd, check=False)
    if out:
        print(f"\n{_mask_tokens(out) if mask else out}\n")
    else:
        warn(f"无法获取{label}（容器可能未运行）")
    return out


# ════════════════════════════  环境检查  ════════════════════════════

def ensure_docker_running() -> bool:
    """检查 Docker 引擎是否可用（不自动安装）。"""
    result = run("docker info", check=True, timeout=10)
    if result is not None:
        ok("Docker 引擎运行中")
        return True
    fail("Docker 引擎未运行。请先安装并启动 Docker: https://docs.docker.com/get-docker/")
    return False

# ════════════════════════════  Copilot OAuth Device Flow  ════════════════════════════

def copilot_device_flow() -> Optional[str]:
    """执行 GitHub Copilot OAuth Device Flow"""
    step(3, "GitHub Copilot 授权 (OAuth Device Flow)")

    info("正在发起 Device Code 请求...")
    resp = http_json("POST", "https://github.com/login/device/code", {
        "client_id": COPILOT_CLIENT_ID,
        "scope": "copilot"
    })

    if "error" in resp and not resp.get("device_code"):
        fail(f"Device Code 请求失败: {resp}")
        return None

    device_code = resp.get("device_code")
    user_code = resp.get("user_code")
    if not device_code or not user_code:
        fail(f"Device Code 响应缺少字段: {resp}")
        return None
    verification_uri = resp.get("verification_uri", "https://github.com/login/device")
    interval = resp.get("interval", 5)
    expires_in = resp.get("expires_in", 900)

    print(f"""
  ┌─────────────────────────────────────────────────┐
  │  请在浏览器中打开: {_c('1;4', verification_uri):50s}│
  │  输入验证码:       {_c('1;33', user_code):50s}│
  └─────────────────────────────────────────────────┘
""")

    # 尝试自动打开浏览器
    if platform.system() == "Darwin":
        run(f"open {verification_uri}", check=False)
    elif platform.system() == "Linux" and cmd_exists("xdg-open"):
        run(f"xdg-open {verification_uri}", check=False)

    info("等待用户在浏览器中完成授权...")

    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        token_resp = http_json("POST", "https://github.com/login/oauth/access_token", {
            "client_id": COPILOT_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
        })

        error = token_resp.get("error")
        if error == "authorization_pending":
            continue
        elif error == "slow_down":
            interval = token_resp.get("interval", interval + 5)
            continue
        elif error == "expired_token":
            fail("验证码已过期，请重新运行脚本。")
            return None
        elif error == "access_denied":
            fail("授权被拒绝。")
            return None
        elif error:
            fail(f"授权失败: {error}")
            return None

        access_token = token_resp.get("access_token")
        if access_token:
            ok("授权成功！令牌已获取")
            return access_token

    fail("等待超时，请重新运行脚本。")
    return None

# ════════════════════════════  令牌与配置管理  ════════════════════════════

def _copilot_token_exchange(token: str) -> Dict[str, Any]:
    """用 ghu_ 令牌向 GitHub 换取会话信息（端点/会话令牌）"""
    return http_json("GET", "https://api.github.com/copilot_internal/v2/token", headers={
        "Authorization": f"token {token}",
        "User-Agent": "GitHubCopilotChat/0.22.2024"
    })


def update_compose_token(project_dir: Path, token: str) -> None:
    """更新 .env 文件中的 COPILOT_GITHUB_TOKEN（docker-compose 通过 ${} 引用）"""
    _write_env_var(project_dir, "COPILOT_GITHUB_TOKEN", token,
                   "GitHub Copilot OAuth Token (由 deploy.py 自动管理)")
    ok(".env 令牌已更新")


def read_env_token(project_dir: Path) -> Optional[str]:
    """从 .env 文件读取现有 Copilot token"""
    return _read_env_var(project_dir, "COPILOT_GITHUB_TOKEN")


def _read_env_var(project_dir: Path, name: str) -> Optional[str]:
    """从 .env 文件读取指定变量值。支持无引号、单引号、双引号。"""
    env_file = project_dir / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == name:
            val = val.strip()
            # 去除行尾注释（未被引号包裹的部分）
            if val and val[0] in ('"', "'"):
                val = val.strip(val[0])
            else:
                val = val.split("#")[0].strip()
            return val if val else None
    return None


def _write_env_var(project_dir: Path, name: str, value: str, comment: str = "") -> None:
    """在 .env 文件中写入或更新指定变量。"""
    env_file = project_dir / ".env"
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        found = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, _ = stripped.partition("=")
            if key.strip() == name:
                lines[i] = f"{name}={value}"
                found = True
                break
        if found:
            new_content = "\n".join(lines) + "\n"
        else:
            prefix = f"\n# {comment}\n" if comment else "\n"
            new_content = "\n".join(lines) + f"{prefix}{name}={value}\n"
    else:
        prefix = f"# {comment}\n" if comment else ""
        new_content = f"{prefix}{name}={value}\n"
    env_file.write_text(new_content)


def _ensure_env_secret(project_dir: Path, name: str, comment: str = "") -> str:
    """确保 .env 中存在指定密钥，缺失则自动生成 43 字符随机值。"""
    existing = _read_env_var(project_dir, name)
    if existing:
        return existing
    value = secrets.token_urlsafe(32)
    _write_env_var(project_dir, name, value, comment)
    ok(f"{name} 已自动生成并写入 .env")
    return value


# ════════════════════════════  Gateway Token & 设备配对  ════════════════════════════

def sync_gateway_token_to_config(project_dir: Path, token: str) -> None:
    """将网关令牌同步写入 openclaw/openclaw.json（JSON 不支持 ${VAR} 替换）。"""
    config_file = project_dir / "openclaw" / "openclaw.json"
    if not config_file.exists():
        return
    try:
        config = json.loads(config_file.read_text())
        if config.get("gateway", {}).get("auth", {}).get("token") != token:
            config.setdefault("gateway", {}).setdefault("auth", {})["token"] = token
            config_file.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
            ok("openclaw.json 网关令牌已同步")
    except (json.JSONDecodeError, OSError) as exc:
        warn(f"无法同步 openclaw.json: {exc}")


def ensure_gateway_token(project_dir: Path) -> str:
    """确保网关令牌存在，缺失则自动生成。返回令牌值。"""
    token = _ensure_env_secret(project_dir, "OPENCLAW_GATEWAY_TOKEN", "网关通信 Token")
    if len(token) < 12:
        warn("OPENCLAW_GATEWAY_TOKEN 太短，建议使用更长的随机值。")
    return token


def _extract_request_ids(output: str) -> List[str]:
    """从文本输出中提取设备 ID（UUID 格式或 64 位十六进制）。"""
    ids = re.findall(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        output,
    )
    ids += re.findall(r"[0-9a-fA-F]{64}", output)
    return ids


def _list_devices_json() -> Dict[str, Any]:
    """通过 `openclaw devices list --json` 获取设备数据。

    返回原始 JSON 字典，格式: {"pending": [...], "paired": [...]}。
    解析失败返回空字典。
    """
    out = run("docker exec openclaw openclaw devices list --json", check=False)
    if not out:
        return {}
    try:
        data = json.loads(out)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _approve_pending() -> int:
    """批准所有待审批设备，返回成功批准的数量。"""
    pending = _list_devices_json().get("pending", [])
    if not pending:
        # 回退：从文本输出正则提取 UUID
        txt = run("docker exec openclaw openclaw devices list", check=False) or ""
        pending = [{"requestId": rid} for rid in _extract_request_ids(txt)]
    approved = 0
    for d in pending:
        req_id = d.get("requestId", "")
        if not req_id:
            continue
        client = d.get("clientMode") or d.get("client", "")
        plat   = d.get("platform", "")
        label  = f"{client} / {plat}" if (client or plat) else req_id
        info(f"检测到待审批设备，正在批准: {label}")
        result = run(f"docker exec openclaw openclaw devices approve {req_id}", check=False)
        if result is not None:
            ok(f"设备已批准: {label}")
            approved += 1
        else:
            warn(f"设备批准失败，请手动执行: docker exec -it openclaw openclaw devices approve {req_id}")
    return approved


def _ensure_device_paired() -> None:
    """等待浏览器配对请求，自动批准，确认成功后打印结果。最多等 60 秒。"""
    info("等待浏览器配对请求（请打开 http://localhost:18789 输入 Gateway Token）...")

    approved_total = 0
    deadline = time.time() + 60
    while time.time() < deadline:
        n = _approve_pending()
        approved_total += n
        if approved_total > 0:
            break
        # 检查是否已有配对设备
        paired = run("docker exec openclaw openclaw devices list 2>&1", check=False)
        if paired and "Paired" in paired and "operator" in paired:
            ok("已有配对设备，跳过等待")
            _print_paired_summary()
            return
        remaining = int(deadline - time.time())
        info(f"等待配对中（剩余 {remaining}s）...")
        time.sleep(5)

    if approved_total > 0:
        ok(f"配对成功！共批准 {approved_total} 台设备")
        _print_paired_summary()
    else:
        warn("60s 内未检测到配对请求，可手动执行:")
        print(f"    docker exec openclaw openclaw devices list")
        print(f"    docker exec openclaw openclaw devices approve <requestId>")


def _print_paired_summary() -> None:
    """打印已配对设备数量。"""
    data = _list_devices_json()
    paired = data.get("paired", [])
    info(f"当前已配对设备: {len(paired)} 台")


# ════════════════════════════  构建 OpenClaw 镜像  ════════════════════════════

OPENCLAW_IMAGE_NAME    = "openclaw:local"
SKILLSERVER_IMAGE_NAME = "skillserver:local"
SEARXNG_IMAGE_NAME     = "searxng/searxng:latest"


def _cleanup_build_image(base: str) -> None:
    """删除构建基础镜像（如 node:22-bookworm），释放磁盘空间。"""
    if not _image_exists(base):
        return
    info(f"清理构建基础镜像: {base}")
    if run(f"docker rmi {base}", check=False, timeout=30) is not None:
        ok(f"已删除 {base}")
    else:
        warn(f"删除 {base} 失败（可能被其他容器使用）")


def build_openclaw_image(project_dir: Path) -> bool:
    """构建 OpenClaw 及 SkillServer Docker 镜像。"""
    header("🔨 构建镜像")

    # OpenClaw
    step(1, f"构建 Docker 镜像: {OPENCLAW_IMAGE_NAME}")
    info("首次构建约需 2-3 分钟，后续重启无需重建...")
    result = run(
        f"cd {_shell_quote(str(project_dir))} && {compose_cmd()} build openclaw",
        capture=False, check=False, timeout=1800,
    )
    if result is None:
        fail("OpenClaw 镜像构建失败")
        return False
    if not _image_exists(OPENCLAW_IMAGE_NAME):
        fail(f"镜像 {OPENCLAW_IMAGE_NAME} 未找到")
        return False
    ok(f"镜像构建成功: {OPENCLAW_IMAGE_NAME}")
    _print_image_info(OPENCLAW_IMAGE_NAME)
    _cleanup_build_image("node:22-bookworm-slim")

    # SkillServer
    step(2, f"构建 Docker 镜像: {SKILLSERVER_IMAGE_NAME}")
    info("首次构建需安装 Playwright + Chromium，约需 3-5 分钟...")
    result = run(
        f"cd {_shell_quote(str(project_dir))} && {compose_cmd()} build skillserver",
        capture=False, check=False, timeout=1800,
    )
    if result is None:
        fail("SkillServer 镜像构建失败")
        return False
    if not _image_exists(SKILLSERVER_IMAGE_NAME):
        fail(f"镜像 {SKILLSERVER_IMAGE_NAME} 未找到")
        return False
    ok(f"镜像构建成功: {SKILLSERVER_IMAGE_NAME}")
    _print_image_info(SKILLSERVER_IMAGE_NAME)
    _cleanup_build_image("python:3.12-slim")
    return True


def _print_image_info(image: str) -> None:
    """打印镜像摘要信息（大小、创建时间）。"""
    out = run(
        f"docker image inspect {_shell_quote(image)} --format '{{{{.Size}}}}|{{{{.Created}}}}'",
        check=False, timeout=10,
    )
    if not out:
        return
    parts = out.strip("'").split("|", 1)
    if parts[0].isdigit():
        info(f"镜像大小: {int(parts[0]) / (1024 * 1024):.1f} MB")
    if len(parts) > 1:
        info(f"创建时间: {parts[1][:19]}")


def _image_exists(image: str) -> bool:
    """检查本地是否已有指定 Docker 镜像。"""
    result = run(
        f"docker image inspect {_shell_quote(image)} --format '{{{{.Id}}}}'",
        check=False, timeout=10,
    )
    return bool(result)


def ensure_searxng_image() -> bool:
    """确保 searxng 运行时镜像存在，缺失时自动拉取。"""
    if _image_exists(SEARXNG_IMAGE_NAME):
        ok(f"镜像已就绪: {SEARXNG_IMAGE_NAME}")
        return True

    info(f"缺少运行时镜像，正在拉取: {SEARXNG_IMAGE_NAME}")
    if run(f"docker pull {_shell_quote(SEARXNG_IMAGE_NAME)}",
           capture=False, check=False, timeout=600) is None:
        fail(f"镜像拉取失败: {SEARXNG_IMAGE_NAME}")
        return False

    if not _image_exists(SEARXNG_IMAGE_NAME):
        fail(f"镜像未找到: {SEARXNG_IMAGE_NAME}")
        return False

    ok(f"镜像拉取成功: {SEARXNG_IMAGE_NAME}")
    return True


# ════════════════════════════  服务管理  ════════════════════════════


def _show_gateway_token(token: str) -> None:
    """打印遮蔽的网关令牌提示框，供浏览器首次连接时输入。"""
    masked = token[:6] + "***" if len(token) > 6 else token
    print(f"\n  ┌─────────────────────────────────────────────────┐")
    print(f"  │  {_c('1;33', 'Gateway Token')} (浏览器连接时输入):              │")
    print(f"  │  {_c('1;32', masked):50s}│")
    print(f"  │  完整值见 .env → OPENCLAW_GATEWAY_TOKEN          │")
    print(f"  └─────────────────────────────────────────────────┘\n")


def _sync_default_model(project_dir: Path) -> None:
    """读取 openclaw.json 中的默认模型，通过 CLI 同步到已有 agent。"""
    config_path = project_dir / "openclaw" / "openclaw.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        model_cfg = cfg.get("agents", {}).get("defaults", {}).get("model", {})

        # 同步 primary model
        model = model_cfg.get("primary")
        if not model:
            return
        result = run(
            f"docker exec openclaw openclaw models set {_shell_quote(str(model))}",
            check=False,
            timeout=15,
        )
        if result is not None:
            ok(f"默认模型已同步: {model}")
        else:
            warn(f"模型同步失败: {model}（容器可能未就绪）")

        # 同步 image model
        image_model = model_cfg.get("image")
        if image_model:
            result = run(
                f"docker exec openclaw openclaw models set-image {_shell_quote(str(image_model))}",
                check=False,
                timeout=15,
            )
            if result is not None:
                ok(f"图像模型已同步: {image_model}")
            else:
                warn(f"图像模型同步失败: {image_model}")
    except Exception as e:
        warn(f"读取模型配置失败: {e}")


def start_services(project_dir: Path, *, auto_pair: bool = True,
                   use_prepared_images: bool = False) -> bool:
    """校验网关令牌 → 启动容器 → 健康检查 → 自动配对。

    注意: --start 使用 force-recreate 重建容器，但不会清空已配对设备。
    """
    # 准备令牌与密钥
    gateway_token = ensure_gateway_token(project_dir)
    sync_gateway_token_to_config(project_dir, gateway_token)
    _ensure_env_secret(project_dir, "SEARXNG_SECRET", "SearXNG 加密密钥")
    _ensure_env_secret(project_dir, "AUTH_PASSWORD", "OpenClaw UI 登录密码")

    # 启动容器
    info("启动容器...")
    up_args = "-d --force-recreate"
    if use_prepared_images:
        # --start 仅使用本地镜像，不在该步骤触发 pull/build。
        up_args += " --no-build --pull never"
    if run(f"cd {_shell_quote(str(project_dir))} && {compose_cmd()} up {up_args}",
           capture=False, check=False, timeout=120) is None:
        fail("容器启动失败")
        return False

    services_ok = verify_services()

    # 同步 openclaw.json 中的默认模型到已有 agent
    _sync_default_model(project_dir)

    # 显示令牌
    _show_gateway_token(gateway_token)

    # 完整部署时自动等待并批准设备配对（最多 60 秒）
    if auto_pair:
        _ensure_device_paired()
    else:
        info("启动完成")
    return services_ok


def _check_connectivity() -> bool:
    """检查 OpenClaw HTTP 连通性及 SkillServer → SearXNG 容器间通信。"""
    all_ok = True

    # OpenClaw HTTP
    if _check_http(OPENCLAW_PORT, OPENCLAW_OK_CODES):
        ok(f"OpenClaw 运行中 → http://localhost:{OPENCLAW_PORT}")
    else:
        fail(f"OpenClaw 未响应 (端口 {OPENCLAW_PORT})")
        all_ok = False

    # SkillServer health endpoint（从 openclaw 容器侧访问）
    skillserver_ok = run(
        'docker exec openclaw curl -s --max-time 5 -o /dev/null -w "%{http_code}" http://skillserver:3000/health',
        check=False, timeout=15,
    )
    if skillserver_ok and skillserver_ok.strip("'\"") in ("200", "307"):
        ok("SkillServer 容器间通信正常 → http://skillserver:3000/health")
    else:
        warn(f"SkillServer 响应异常（状态码: {skillserver_ok}），可能仍在启动中")
        all_ok = False

    # SearXNG（从 skillserver 容器侧访问）
    searxng_ok = run(
        "docker exec skillserver python3 -c \""
        "import urllib.request, sys; "
        "r=urllib.request.urlopen('http://searxng:8080/search?q=test&format=json', timeout=5); "
        "sys.exit(0 if b'results' in r.read() else 1)\"",
        check=True, timeout=15,
    )
    if searxng_ok is not None:
        ok("SearXNG 容器间通信正常 → http://searxng:8080")
    else:
        fail("SearXNG 容器间通信异常")
        all_ok = False

    return all_ok


def verify_services() -> bool:
    """等待容器就绪 → 检查通信，返回是否全部正常。"""
    containers = {
        "openclaw":    "OpenClaw",
        "skillserver": "SkillServer",
        "searxng":     "SearXNG",
    }
    inspect_fmt = ("'{{.State.Status}}|{{if .State.Health}}"
                   "{{.State.Health.Status}}{{else}}no-healthcheck{{end}}'")
    deadline = time.time() + 60
    ready = {name: False for name in containers}
    while time.time() < deadline:
        all_ready = True
        for name, label in containers.items():
            status = run(
                "docker inspect --format %s %s" % (inspect_fmt, name),
                check=False, timeout=10,
            )
            if not status:
                fail(f"{label} 容器不存在或未启动")
                return False

            state, health = status.strip("'").split("|", 1)
            if state != "running":
                fail(f"{label} 容器状态异常: {state}")
                return False

            is_ready = health in ("healthy", "no-healthcheck")
            ready[name] = is_ready
            if not is_ready:
                all_ready = False

        if all_ready:
            break
        time.sleep(3)

    for name, label in containers.items():
        if ready[name]:
            ok(f"{label} 已启动")
        else:
            warn(f"{label} 容器尚未就绪（超时）")

    return _check_connectivity()


def stop_services() -> None:
    """停止全部容器。"""
    info("正在停止所有服务...")
    project_dir = get_project_dir()
    run(f"cd {_shell_quote(str(project_dir))} && {compose_cmd()} down",
        capture=False, check=False, timeout=60)
    ok("所有服务已停止")


# ════════════════════════════  主流程  ════════════════════════════

def _resolve_token(project_dir: Path) -> str:
    """已有有效令牌时询问是否复用，否则走 OAuth Device Flow。"""
    existing = read_env_token(project_dir)

    if existing and existing.startswith("ghu_"):
        print(f"\n  当前令牌: ********")
        choice = input(f"  {_c('1;33', '?')} 是否重新获取令牌？[y/N] ").strip().lower()
        if choice != "y":
            ok("使用现有令牌")
            return existing

    token = copilot_device_flow()
    if not token:
        fail("无法获取 Copilot 令牌，部署中止。")
        sys.exit(1)
    return token


def _print_deploy_result(project_dir: Path, success: bool) -> None:
    """打印部署完成摘要。"""
    header("部署完成")
    auth_pw = _read_env_var(project_dir, "AUTH_PASSWORD") or "(见 .env)"
    if success:
        print(f"""
  {_c('1;32', 'OpenClaw UI')} : http://localhost:{OPENCLAW_PORT}
  {_c('1;32', '登录密码')}    : {auth_pw}

  {_c('1;33', '令牌过期')}: 令牌会过期，届时运行 python3 deploy.py 重新获取。
  {_c('1;33', '检查状态')}: 运行 python3 deploy.py --check 查看令牌与服务状态。
""")
    else:
        print(f"""
  {_c('1;31', '部分服务未就绪')}，请检查:
    {compose_cmd()} ps
    docker logs openclaw
""")


def full_deploy(project_dir: Path) -> None:
    """完整部署：Docker 检查 → 构建镜像 → OAuth 授权 → 配置端点 → 启动服务。"""
    header("🚀 Copilot Local Agent Stack 一键部署")

    # 1. 检查 Docker 引擎
    step(1, "检查 Docker 引擎")
    if not ensure_docker_running():
        sys.exit(1)

    # 2. 构建镜像（openclaw + skillserver，已有则询问是否重建）
    need_build = True
    if _image_exists(OPENCLAW_IMAGE_NAME) and _image_exists(SKILLSERVER_IMAGE_NAME):
        ok(f"本地镜像已存在: {OPENCLAW_IMAGE_NAME}, {SKILLSERVER_IMAGE_NAME}")
        _print_image_info(OPENCLAW_IMAGE_NAME)
        _print_image_info(SKILLSERVER_IMAGE_NAME)
        need_build = input(f"  {_c('1;33', '?')} 是否重新构建镜像？[y/N] ").strip().lower() == "y"
    else:
        info("本地镜像不存在，首次构建...")

    if need_build and not build_openclaw_image(project_dir):
        fail("镜像构建失败，部署中止。")
        sys.exit(1)

    # 确保 searxng 运行时镜像可用（缺失时自动拉取）
    if not ensure_searxng_image():
        fail("运行时镜像检查失败，部署中止。")
        sys.exit(1)

    # 3. Copilot 授权 —— 已有令牌时询问是否复用
    token = _resolve_token(project_dir)

    # 4. 配置端点
    step(4, "更新令牌配置")
    update_compose_token(project_dir, token)

    # 5. 启动服务
    step(5, "启动服务")
    success = start_services(project_dir, auto_pair=False)
    _show_cmd_output(f"{compose_cmd()} ps", "容器状态", mask=False)

    # 6. 部署结果
    _print_deploy_result(project_dir, success)


def refresh_token_only(project_dir: Path) -> None:
    """仅重新获取 Copilot token 并写入 .env，不启动或停止容器。"""
    header("🔐 重新获取 Copilot 令牌")
    token = copilot_device_flow()
    if not token:
        fail("无法获取 Copilot 令牌。")
        sys.exit(1)

    step(1, "更新令牌配置")
    update_compose_token(project_dir, token)

    step(2, "验证令牌")
    if _check_copilot_token(project_dir):
        ok("新令牌已生效")
    else:
        fail("新令牌写入成功，但验证失败，请稍后重试。")
        sys.exit(1)


def _extract_overview(status_output: str) -> str:
    """从 openclaw status 输出中提取 Overview 表格部分。

    解析失败时返回原始输出。
    """
    lines = status_output.splitlines()
    result: List[str] = []
    capturing = False
    for line in lines:
        if line.strip().startswith("Overview"):
            capturing = True
            continue
        if capturing:
            if line.strip() == "" and result and result[-1].startswith("└"):
                break
            result.append(line)
    return "\n".join(result) if result else status_output


def _check_copilot_token(project_dir: Path) -> bool:
    """检查 Copilot 令牌有效性。有效返回 True，缺失/失效返回 False。"""
    token = read_env_token(project_dir)
    if not token:
        fail("未找到 token，请先运行: python3 deploy.py")
        return False

    info("检查令牌有效性: ********")
    resp = _copilot_token_exchange(token)

    err_msg = resp.get("error") or resp.get("error_details", {}).get("code")
    if err_msg:
        detail = resp.get("error", resp.get("error_details", {}).get("message", "unknown"))
        fail(f"令牌已失效: {detail}")
        return False

    ok("令牌有效" + ("（ghu_ 可正常换取会话，OpenClaw 自动续期）" if resp.get("token") else ""))
    return True


def _show_device_status() -> None:
    """显示设备配对状态并自动批准待审批设备。"""
    data = _list_devices_json()
    pending = data.get("pending", [])
    paired = data.get("paired", [])

    if pending:
        warn(f"{len(pending)} 台设备待审批")
        _approve_pending()
    for d in paired:
        client = d.get("clientMode", "unknown")
        plat = d.get("platform", "unknown")
        role = d.get("role", "-")
        scopes = ", ".join(d.get("scopes", []))
        ok(f"{client} ({plat})  role={role}  scopes=[{scopes}]")
    if paired:
        info(f"共 {len(paired)} 台设备已配对")
    if not pending and not paired:
        info("暂无配对设备")


def check_health(project_dir: Path) -> bool:
    """综合健康检查：容器 → HTTP → 令牌 → 配对 → 网关 → 状态 → 模型。"""
    all_ok = True

    # 1. 容器状态
    step(1, "容器状态")
    _show_cmd_output(f"{compose_cmd()} ps", "容器状态", mask=False)

    # 2. 服务通信检查
    step(2, "服务通信检查")
    if not _check_connectivity():
        all_ok = False

    # 3. Copilot 令牌
    step(3, "Copilot 令牌")
    if not _check_copilot_token(project_dir):
        return False

    # 4. 设备配对
    step(4, "设备配对")
    _show_device_status()

    # 5. 网关状态
    step(5, "网关状态")
    _show_cmd_output("docker exec openclaw openclaw gateway status", "网关状态")

    # 6. OpenClaw 状态（仅 Overview）
    step(6, "OpenClaw 状态")
    oc_out = run("docker exec openclaw openclaw status", check=False)
    if oc_out:
        print(f"\n{_mask_tokens(_extract_overview(oc_out))}\n")
    else:
        warn("无法获取 OpenClaw 状态（容器可能未运行）")

    # 7. 模型配置
    step(7, "模型配置")
    _show_cmd_output("docker exec openclaw openclaw models status", "模型配置")

    # 8. 可用模型
    step(8, "可用模型 (github-copilot)")
    models_out = run(
        "docker exec openclaw openclaw models list --all 2>&1 | grep github-copilot",
        check=False,
    )
    if models_out:
        print(f"\n{models_out}\n")
        ok("github-copilot 模型可用")
    else:
        warn("未检测到 github-copilot 模型（可能需要配置 models.json）")

    return all_ok


def show_logs() -> None:
    """实时显示所有容器的合并日志（按任意键退出）。"""
    import termios
    import tty
    import threading

    header("📋 容器日志")
    containers = ["openclaw", "skillserver", "searxng"]
    running = [c for c in containers
               if run(f"docker inspect -f '{{{{.State.Running}}}}' {c}", check=False, timeout=5) == "true"]
    if not running:
        warn("没有正在运行的容器，请先启动服务。")
        return
    info(f"跟踪容器: {', '.join(running)}")
    print(f"  {_c('1;33', '按任意键退出')}\n")

    # 为每个容器启动独立的 docker logs 进程，避免 compose 的 ANSI 格式化
    procs = []
    max_name = max(len(c) for c in running)
    _COLORS = ["1;36", "1;32", "1;33", "1;35", "1;34"]

    def _stream_one(name: str, proc: subprocess.Popen, color: str) -> None:
        prefix = _c(color, f"{name:<{max_name}}") + " | "
        if proc.stdout is None:
            return
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace")
            sys.stdout.write(prefix + line)
            sys.stdout.flush()

    for i, c in enumerate(running):
        p = subprocess.Popen(
            ["docker", "logs", "--follow", "--tail=50", c],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        procs.append(p)
        threading.Thread(target=_stream_one, args=(c, p, _COLORS[i % len(_COLORS)]), daemon=True).start()

    def _wait_key() -> None:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        for p in procs:
            p.terminate()

    threading.Thread(target=_wait_key, daemon=True).start()
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
    print()
    ok("日志查看已退出")


def show_help() -> None:
    """显示命令用法速查"""
    help_text = f"""
{_c('1;36', '━' * 60)}
    {_c('1;36', '🚀 Copilot Local Agent Stack — 命令速查')}
{_c('1;36', '━' * 60)}

    {_c('1;32', 'python3 deploy.py')}          {_c('1', '完整部署（首次使用推荐）')}
        Docker 检查 → 构建镜像 → OAuth 授权 → 启动容器

    {_c('1;32', 'python3 deploy.py --start')}   {_c('1', '启动，重启（令牌已有）')}
        检查令牌 → 启动容器 → 健康检查

    {_c('1;32', 'python3 deploy.py --newtoken')} {_c('1', '仅重新获取 GitHub Copilot token')}
        执行 OAuth Device Flow 并更新 .env 中 COPILOT_GITHUB_TOKEN

    {_c('1;32', 'python3 deploy.py --build')}   {_c('1', '重建/补齐镜像')}
        重建 openclaw 与 skillserver，确保 searxng 镜像存在（缺失自动拉取）

    {_c('1;32', 'python3 deploy.py --check')}   {_c('1', '健康检查 + 自动批准待配对设备')}
        容器 → HTTP → 令牌 → 配对 → 网关 → 模型

    {_c('1;32', 'python3 deploy.py --stop')}    {_c('1', '停止所有容器')}
        停止所有容器（不删除设备配对）

    {_c('1;32', 'python3 deploy.py --logs')}    {_c('1', '查看所有容器日志（按任意键退出）')}
        openclaw / skillserver / searxng 日志合并显示，按任意键退出

    {_c('1;32', 'python3 deploy.py --help')}    {_c('1', '显示本帮助')}

    详细文档请查看 README.md

{_c('1;36', '━' * 60)}
"""
    print(help_text)


def parse_args(argv: List[str]) -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--help", action="store_true", help="显示帮助")
    group.add_argument("--stop", action="store_true", help="停止所有容器")
    group.add_argument("--check", action="store_true", help="健康检查 + 自动批准待配对设备")
    group.add_argument("--start", action="store_true", help="启动，重启（令牌已有）")
    group.add_argument("--newtoken", action="store_true", help="仅重新获取 Copilot 令牌并写入 .env")
    group.add_argument("--build", action="store_true", help="重建 openclaw/skillserver 并确保 searxng 镜像存在")
    group.add_argument("--logs", action="store_true", help="查看所有容器日志（按任意键退出）")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])

    if args.help:
        show_help()
        return

    # 所有非 help 分支都需要项目目录，统一 chdir 一次
    project_dir = get_project_dir()
    os.chdir(project_dir)

    if args.logs:
        show_logs()
    elif args.stop:
        stop_services()
    elif args.build:
        step(1, "检测 Docker 环境")
        if not ensure_docker_running():
            sys.exit(1)
        if not build_openclaw_image(project_dir):
            sys.exit(1)
        step(2, "确保 searxng 镜像")
        if not ensure_searxng_image():
            sys.exit(1)
    elif args.check:
        header("🔍 综合健康检查")
        if not check_health(project_dir):
            sys.exit(1)
    elif args.start:
        header("⚡ 快速启动")
        step(1, "检查 Docker 引擎")
        if not ensure_docker_running():
            sys.exit(1)
        # 校验 .env 中的 Copilot 令牌
        step(2, "检查 Copilot 令牌")
        token = read_env_token(project_dir)
        if not token:
            fail("未找到 COPILOT_GITHUB_TOKEN，请先运行: python3 deploy.py 完成完整部署")
            sys.exit(1)
        ok(f"令牌已加载: {token[:8]}********")
        step(3, "启动服务")
        start_services(project_dir, auto_pair=False, use_prepared_images=True)
    elif args.newtoken:
        refresh_token_only(project_dir)
    else:
        full_deploy(project_dir)


if __name__ == "__main__":
    main()
