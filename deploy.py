#!/usr/bin/env python3
"""Hermes Agent stack deployment helper."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
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
from typing import Dict, Optional, Tuple

COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
DASHBOARD_PORT = 9119
HERMES_API_PORT = 8642
HERMES_WEBUI_PORT = 8787
HERMES_OK_CODES: Tuple[str, ...] = ("200", "204", "301", "302", "307", "308")
HERMES_IMAGE = "hermes-agent:local"
HERMES_WEBUI_IMAGE = "hermes-webui:local"
SKILLSERVER_IMAGE = "skillserver:local"
HTTPS_CERT_DIR = Path("httpscert")
HTTPS_TLS_CONFIG = HTTPS_CERT_DIR / "tls.cnf"
SKILLSERVER_TLS_TARGET_DATE = dt.date(2999, 12, 31)


def _c(code: str, text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text


def info(msg: str) -> None:
    print(f"  {_c('1;34', 'i')} {msg}")


def ok(msg: str) -> None:
    print(f"  {_c('1;32', 'OK')} {msg}")


def fail(msg: str) -> None:
    print(f"  {_c('1;31', 'XX')} {msg}")


def step(num: int, msg: str) -> None:
    print(f"\n  {_c('1;35', f'[{num}]')} {_c('1', msg)}")


def project_dir() -> Path:
    return Path(__file__).resolve().parent


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def run(command: str, *, check: bool = True, capture: bool = True, timeout: int = 600) -> Optional[str]:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if check and result.returncode != 0:
        return None
    return result.stdout.strip() if capture else ""


def compose_cmd() -> str:
    if run("docker compose version", check=True, timeout=10) is not None:
        return "docker compose"
    if shutil.which("docker-compose"):
        return "docker-compose"
    return "docker compose"


def ensure_docker() -> bool:
    if run("docker info", check=True, timeout=10) is not None:
        ok("Docker 引擎运行中")
        return True
    fail("Docker 引擎未运行，请先启动 Docker Desktop")
    return False


def _skillserver_tls_days_remaining() -> int:
    today = dt.datetime.now(dt.UTC).date()
    return max(1, (SKILLSERVER_TLS_TARGET_DATE - today).days)


def _cert_end_year(openssl: str, cert_path: Path) -> Optional[int]:
    result = subprocess.run(
        [openssl, "x509", "-in", str(cert_path), "-noout", "-enddate"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return None
    line = (result.stdout or "").strip()
    if "=" not in line:
        return None
    try:
        return int(line.rsplit(" ", 1)[-1])
    except ValueError:
        return None


def ensure_skillserver_tls(root: Path) -> bool:
    cert_dir = root / HTTPS_CERT_DIR
    tls_config = root / HTTPS_TLS_CONFIG
    ca_key = cert_dir / "local-ca.key"
    ca_cert = cert_dir / "local-ca.crt"
    server_key = cert_dir / "local-https.key"
    server_csr = cert_dir / "local-https.csr"
    server_cert = cert_dir / "local-https.crt"
    fullchain_cert = cert_dir / "local-https.fullchain.crt"
    serial_file = cert_dir / "local-ca.srl"
    required = [ca_key, ca_cert, server_key, server_cert, fullchain_cert]

    openssl = shutil.which("openssl")
    if not openssl:
        fail("未找到 openssl，无法生成共享 HTTPS 自签证书")
        return False

    current_server_end_year = _cert_end_year(openssl, server_cert) if server_cert.exists() else None
    current_ca_end_year = _cert_end_year(openssl, ca_cert) if ca_cert.exists() else None

    if (
        all(path.exists() for path in required)
        and current_server_end_year is not None
        and current_server_end_year >= SKILLSERVER_TLS_TARGET_DATE.year
        and current_ca_end_year is not None
        and current_ca_end_year >= SKILLSERVER_TLS_TARGET_DATE.year
    ):
        ok("复用已有共享 HTTPS 证书")
        return True

    if current_server_end_year or current_ca_end_year:
        info(
            "共享 HTTPS 证书有效期不足，将重签到 "
            f"{SKILLSERVER_TLS_TARGET_DATE.isoformat()}"
        )
    if not tls_config.exists():
        fail(f"缺少 TLS 配置文件: {tls_config}")
        return False

    cert_dir.mkdir(parents=True, exist_ok=True)
    for path in [ca_key, ca_cert, server_key, server_csr, server_cert, fullchain_cert, serial_file]:
        if path.exists():
            path.unlink()

    tls_days = str(_skillserver_tls_days_remaining())

    commands = [
        [
            openssl,
            "req",
            "-x509",
            "-new",
            "-nodes",
            "-sha256",
            "-days",
            tls_days,
            "-keyout",
            str(ca_key),
            "-out",
            str(ca_cert),
            "-subj",
            "/CN=Hermes Local HTTPS CA",
            "-extensions",
            "v3_ca",
            "-config",
            str(tls_config),
        ],
        [
            openssl,
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(server_key),
            "-out",
            str(server_csr),
            "-config",
            str(tls_config),
            "-reqexts",
            "req_ext",
        ],
        [
            openssl,
            "x509",
            "-req",
            "-in",
            str(server_csr),
            "-CA",
            str(ca_cert),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(server_cert),
            "-days",
            tls_days,
            "-sha256",
            "-extfile",
            str(tls_config),
            "-extensions",
            "req_ext",
        ],
    ]

    for args in commands:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            fail("生成共享 HTTPS 证书失败")
            detail = (result.stderr or result.stdout or "").strip()
            if detail:
                print(detail)
            return False

    fullchain_cert.write_bytes(server_cert.read_bytes() + ca_cert.read_bytes())
    ca_key.chmod(0o600)
    server_key.chmod(0o600)
    ok("已生成共享 HTTPS 自签证书")
    return True


def http_json(method: str, url: str, data: Optional[dict] = None, headers: Optional[dict] = None) -> Dict:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    payload = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode())
        except Exception:
            return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}


def fetch_live_models(root: Path, port: int, provider: str = "copilot") -> Optional[Dict]:
    ca_cert = root / HTTPS_CERT_DIR / "local-ca.crt"
    url = f"https://localhost:{port}/api/models/live?provider={urllib.parse.quote(provider)}"
    output = run(
        " ".join(
            shell_quote(part)
            for part in [
                "curl",
                "-sS",
                "--cacert",
                str(ca_cert),
                url,
            ]
        ),
        check=False,
        timeout=30,
    )
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


def print_live_models(payload: Dict) -> None:
    provider = str(payload.get("provider") or "unknown")
    models = payload.get("models") or []
    if not isinstance(models, list):
        fail(f"{provider} 模型列表格式异常")
        return
    ok(f"{provider} 可用模型数量: {len(models)}")
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "")
        label = str(item.get("label") or model_id)
        print(f"    - {model_id} | {label}")


def read_env_var(root: Path, name: str) -> Optional[str]:
    env_file = root / ".env"
    if not env_file.exists():
        return None
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip() or None
    return None


def write_env_var(root: Path, name: str, value: str, comment: Optional[str] = None) -> None:
    env_file = root / ".env"
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    output = []
    replaced = False

    for line in lines:
        if line.strip().startswith(f"{name}="):
            if comment:
                if output and output[-1].startswith("#"):
                    output[-1] = f"# {comment}"
                else:
                    output.append(f"# {comment}")
            output.append(f"{name}={value}")
            replaced = True
            continue
        output.append(line)

    if not replaced:
        if output and output[-1] != "":
            output.append("")
        if comment:
            output.append(f"# {comment}")
        output.append(f"{name}={value}")

    env_file.write_text("\n".join(output).rstrip() + "\n")


def ensure_env_secret(root: Path, name: str, comment: str, length: int = 48) -> str:
    current = read_env_var(root, name)
    if current:
        return current
    token = secrets.token_urlsafe(length)
    write_env_var(root, name, token, comment)
    ok(f"已写入 {name}")
    return token


def ensure_env_defaults(root: Path) -> None:
    if not read_env_var(root, "HERMES_UID"):
        write_env_var(root, "HERMES_UID", str(os.getuid()), "Docker 里 Hermes 进程使用的 UID")
    if not read_env_var(root, "HERMES_GID"):
        write_env_var(root, "HERMES_GID", str(os.getgid()), "Docker 里 Hermes 进程使用的 GID")
    if not read_env_var(root, "HERMES_DASHBOARD_PORT"):
        write_env_var(root, "HERMES_DASHBOARD_PORT", str(DASHBOARD_PORT), "Hermes Dashboard 本地端口")
    if not read_env_var(root, "API_SERVER_PORT"):
        write_env_var(root, "API_SERVER_PORT", str(HERMES_API_PORT), "Hermes OpenAI-compatible API server 端口")
    if not read_env_var(root, "HERMES_WEBUI_PORT"):
        write_env_var(root, "HERMES_WEBUI_PORT", str(HERMES_WEBUI_PORT), "Hermes WebUI 本地端口")
    if not read_env_var(root, "TZ"):
        write_env_var(root, "TZ", "Asia/Shanghai", "时区")
    ensure_env_secret(root, "API_SERVER_KEY", "Hermes API Server Bearer Key")
    ensure_env_secret(root, "SEARXNG_SECRET", "SearXNG Secret")


def copilot_device_flow() -> Optional[str]:
    info("正在发起 GitHub Device Flow")
    payload = http_json(
        "POST",
        "https://github.com/login/device/code",
        {"client_id": COPILOT_CLIENT_ID, "scope": "copilot"},
    )
    device_code = payload.get("device_code")
    user_code = payload.get("user_code")
    verification_uri = payload.get("verification_uri", "https://github.com/login/device")
    interval = payload.get("interval", 5)
    expires_in = payload.get("expires_in", 900)

    if not device_code or not user_code:
        fail(f"Device Flow 初始化失败: {payload}")
        return None

    print("\n  请在浏览器中打开:", verification_uri)
    print("  输入验证码:", user_code)
    print("")

    if platform.system() == "Darwin":
        run(f"open {shell_quote(verification_uri)}", check=False, timeout=5)

    deadline = time.time() + expires_in

    while time.time() < deadline:
        time.sleep(interval)
        token_resp = http_json(
            "POST",
            "https://github.com/login/oauth/access_token",
            {
                "client_id": COPILOT_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )

        error = token_resp.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = token_resp.get("interval", interval + 5)
            continue
        if error:
            fail(f"授权失败: {error}")
            return None

        access_token = token_resp.get("access_token")
        if access_token:
            ok("GitHub 授权成功")
            return access_token

    fail("等待授权超时，请重试")
    return None


def ensure_github_token(root: Path, force: bool = False) -> Optional[str]:
    existing = read_env_var(root, "GITHUB_TOKEN")
    if existing and not force:
        ok("复用已有 GITHUB_TOKEN")
        return existing
    token = copilot_device_flow()
    if not token:
        return None
    write_env_var(root, "GITHUB_TOKEN", token, "GitHub Copilot / GitHub Models token（由 deploy.py 自动管理）")
    ok("已更新 .env 中的 GITHUB_TOKEN")
    return token


def image_exists(image: str) -> bool:
    out = run(f"docker image inspect {shell_quote(image)}", check=False, timeout=20)
    return out is not None


def build_images(root: Path, force: bool = False) -> bool:
    command = f"cd {shell_quote(str(root))} && {compose_cmd()} build"
    if force:
        command += " --no-cache"
    step(1, "构建 Docker 镜像")
    result = subprocess.run(command, shell=True)
    if result.returncode != 0:
        fail("镜像构建失败")
        return False
    required_images = [HERMES_IMAGE, HERMES_WEBUI_IMAGE, SKILLSERVER_IMAGE]
    missing_images = [image for image in required_images if not image_exists(image)]
    if missing_images:
        fail("镜像构建后缺少: " + ", ".join(missing_images))
        return False
    ok("镜像构建完成")
    return True


def start_stack(root: Path) -> bool:
    step(2, "启动 Hermes Stack")
    command = f"cd {shell_quote(str(root))} && {compose_cmd()} up -d"
    result = subprocess.run(command, shell=True)
    if result.returncode != 0:
        fail("容器启动失败")
        return False
    ok("容器已启动")
    return True


def stop_stack(root: Path) -> bool:
    command = f"cd {shell_quote(str(root))} && {compose_cmd()} down"
    result = subprocess.run(command, shell=True)
    if result.returncode != 0:
        fail("停止容器失败")
        return False
    ok("容器已停止")
    return True


def show_logs(root: Path) -> None:
    command = f"cd {shell_quote(str(root))} && {compose_cmd()} logs --tail=200 -f"
    subprocess.run(command, shell=True)


def check_http(port: int, ok_codes: Tuple[str, ...], *, scheme: str = "http", ca_cert: Optional[Path] = None) -> bool:
    command = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}"]
    if ca_cert is not None:
        command.extend(["--cacert", str(ca_cert)])
    command.append(f"{scheme}://localhost:{port}")
    status = run(" ".join(shell_quote(part) for part in command), check=False, timeout=10)
    return bool(status and status.strip("'") in ok_codes)


def wait_until(checker, timeout: int = 90, interval: int = 2) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if checker():
            return True
        time.sleep(interval)
    return checker()


def check_stack(root: Path) -> bool:
    dashboard_port = int(read_env_var(root, "HERMES_DASHBOARD_PORT") or str(DASHBOARD_PORT))
    api_port = int(read_env_var(root, "API_SERVER_PORT") or str(HERMES_API_PORT))
    hermes_webui_port = int(read_env_var(root, "HERMES_WEBUI_PORT") or str(HERMES_WEBUI_PORT))
    shared_ca_cert = root / HTTPS_CERT_DIR / "local-ca.crt"
    step(1, "检查 Hermes Dashboard")
    if wait_until(lambda: check_http(dashboard_port, HERMES_OK_CODES, scheme="https", ca_cert=shared_ca_cert), timeout=90):
        ok(f"Hermes Dashboard 在线: https://localhost:{dashboard_port}")
    else:
        fail(f"Hermes Dashboard 未响应: https://localhost:{dashboard_port}")
        return False

    step(2, "检查 Hermes API Server")
    if wait_until(
        lambda: bool((run(f"curl -fsS http://localhost:{api_port}/health", check=False, timeout=10) or "").find('"status": "ok"') != -1),
        timeout=60,
    ):
        ok(f"Hermes API Server 在线: http://localhost:{api_port}")
    else:
        fail(f"Hermes API Server 未响应: http://localhost:{api_port}")
        return False

    step(3, "检查 Hermes WebUI")
    if wait_until(lambda: check_http(hermes_webui_port, HERMES_OK_CODES, scheme="https", ca_cert=shared_ca_cert), timeout=120):
        ok(f"Hermes WebUI 在线: https://localhost:{hermes_webui_port}")
    else:
        fail(f"Hermes WebUI 未响应: https://localhost:{hermes_webui_port}")
        return False

    step(4, "检查 SkillServer")
    out = run(
        f"cd {shell_quote(str(root))} && {compose_cmd()} exec -T skillserver python -c \"import urllib.request; print(urllib.request.urlopen('https://127.0.0.1:3000/health', timeout=5).read().decode())\"",
        check=False,
        timeout=30,
    )
    if out and '"status":"ok"' in out.replace(" ", ""):
        ok("SkillServer 运行正常")
    else:
        fail("SkillServer 健康检查失败")
        return False

    step(5, "检查 SkillServer -> SearXNG")
    code = run(
        f"cd {shell_quote(str(root))} && {compose_cmd()} exec -T skillserver python -c \"import urllib.request; print(urllib.request.urlopen('http://searxng:8080/', timeout=5).status)\"",
        check=False,
        timeout=30,
    )
    if code and code in {"200", "302", "403"}:
        ok("容器内 SearXNG 连通")
    else:
        fail("SkillServer 无法访问 SearXNG")
        return False

    step(6, "检查容器状态")
    services_output = run(
        f"cd {shell_quote(str(root))} && {compose_cmd()} ps --services --all",
        check=False,
        timeout=30,
    ) or ""
    present_services = {line.strip() for line in services_output.splitlines() if line.strip()}
    required = {"hermes", "dashboard", "hermes-webui", "skillserver", "searxng"}
    missing = sorted(required - present_services)
    if missing:
        fail("缺少容器: " + ", ".join(missing))
        return False
    ok("所有核心容器都存在")

    step(7, "检查 Copilot 可用模型")
    models_payload = fetch_live_models(root, hermes_webui_port, provider="copilot")
    if not models_payload:
        fail("无法获取 Copilot 模型列表")
        return False
    if models_payload.get("provider") != "copilot":
        fail(f"模型接口返回异常 provider: {models_payload.get('provider')}")
        return False
    if not models_payload.get("models"):
        fail("Copilot 模型列表为空")
        return False
    print_live_models(models_payload)
    return True


def deploy(root: Path, *, force_build: bool = False, refresh_token: bool = False) -> int:
    if not ensure_docker():
        return 1

    ensure_env_defaults(root)
    if not ensure_skillserver_tls(root):
        return 1
    if not ensure_github_token(root, force=refresh_token):
        return 1

    if force_build or any(not image_exists(image) for image in [HERMES_IMAGE, HERMES_WEBUI_IMAGE, SKILLSERVER_IMAGE]):
        if not build_images(root, force=force_build):
            return 1
    else:
        ok("本地镜像已存在，跳过构建")

    if not start_stack(root):
        return 1

    print("")
    if check_stack(root):
        dashboard_port = read_env_var(root, "HERMES_DASHBOARD_PORT") or str(DASHBOARD_PORT)
        webui_port = read_env_var(root, "HERMES_WEBUI_PORT") or str(HERMES_WEBUI_PORT)
        ok(f"完成: Dashboard https://localhost:{dashboard_port} | WebUI https://localhost:{webui_port}")
        return 0
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy Hermes Agent stack")
    parser.add_argument("--start", action="store_true", help="启动或重启容器")
    parser.add_argument("--stop", action="store_true", help="停止容器")
    parser.add_argument("--build", action="store_true", help="构建镜像")
    parser.add_argument("--force", action="store_true", help="强制重建镜像")
    parser.add_argument("--newtoken", action="store_true", help="重新获取 GitHub token")
    parser.add_argument("--check", action="store_true", help="检查服务健康状态")
    parser.add_argument("--logs", action="store_true", help="查看容器日志")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_dir()

    if args.stop:
        return 0 if stop_stack(root) else 1
    if args.logs:
        show_logs(root)
        return 0
    if args.check:
        return 0 if check_stack(root) else 1
    if args.build and not args.start:
        if not ensure_docker():
            return 1
        ensure_env_defaults(root)
        if not ensure_skillserver_tls(root):
            return 1
        return 0 if build_images(root, force=args.force) else 1
    if args.newtoken and not args.start:
        ensure_env_defaults(root)
        return 0 if ensure_github_token(root, force=True) else 1
    if args.start:
        if not ensure_docker():
            return 1
        ensure_env_defaults(root)
        if not ensure_skillserver_tls(root):
            return 1
        if args.newtoken and not ensure_github_token(root, force=True):
            return 1
        if args.build or args.force:
            if not build_images(root, force=args.force):
                return 1
        return 0 if start_stack(root) and check_stack(root) else 1
    return deploy(root, force_build=args.force or args.build, refresh_token=args.newtoken)


if __name__ == "__main__":
    raise SystemExit(main())
