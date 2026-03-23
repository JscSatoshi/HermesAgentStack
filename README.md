# Copilot Local Agent Stack

![Docker](https://img.shields.io/badge/Docker-3%20services-2496ED?logo=docker&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.6%2B-3776AB?logo=python&logoColor=white)
![OpenClaw](https://img.shields.io/badge/OpenClaw-18789-111111)
![SkillServer](https://img.shields.io/badge/SkillServer-3000-0B8F6A)
![SearXNG](https://img.shields.io/badge/SearXNG-8080-FF6A00)

本地 AI Agent 全栈：OpenClaw + GitHub Copilot + 网页搜索/浏览能力。

## 快速开始

**依赖**：Docker、Python 3.6+、GitHub Copilot Enterprise/Business

1. 克隆仓库并进入目录
2. 执行一键部署
3. 打开 OpenClaw UI，输入配对令牌完成绑定

```bash
git clone <repo-url> && cd Copilotclaw
python3 deploy.py        # 首次部署（自动 OAuth 授权 + 构建镜像 + 启动）
```

部署完成后打开 OpenClaw UI，输入 `.env` 中的 `OPENCLAW_GATEWAY_TOKEN` 完成配对。

> 提示：如果配对失败，先执行 `python3 deploy.py --check` 查看健康状态与待批准设备。

## 访问地址

| 服务 | 地址 |
|------|------|
| OpenClaw UI | http://localhost:18789（密码见 `.env` → `AUTH_PASSWORD`）|

## 命令

```bash
python3 deploy.py            # 完整部署（首次使用推荐）
python3 deploy.py --start    # 启动，重启（令牌已有）
python3 deploy.py --build    # 重建/补齐镜像
python3 deploy.py --newtoken # 仅重新获取 GitHub Copilot token
python3 deploy.py --check    # 健康检查 + 自动批准待配对设备
python3 deploy.py --logs     # 查看所有容器日志（按任意键退出）
python3 deploy.py --stop     # 停止所有容器
python3 deploy.py --help     # 显示帮助
```


## 架构

```
浏览器
  │
  ▼
OpenClaw :18789          ← Agent UI + Copilot 模型
  │  (容器内 HTTP)
  ▼
SkillServer :3000        ← FastAPI + Playwright Chromium
  │  (容器内 HTTP)
  ▼
SearXNG :8080            ← 本地搜索引擎（不对外暴露）
```

三个容器通过 `ai-bridge` Docker 内网通信，仅 OpenClaw 端口对外暴露。

Agent 通过 **Skill**（注入系统提示的 Markdown）学会调用 `http://skillserver:3000/...`，实现网页搜索与浏览器操控。

## 项目结构

```
├── deploy.py                        # 一键部署脚本
├── docker-compose.yml               # 容器编排（3 服务）
├── Dockerfile.openclaw              # OpenClaw 镜像（node:22-bookworm-slim）
├── .env                             # 密钥（自动生成，勿提交至 git）
├── openclaw/
│   ├── openclaw.json                # OpenClaw 配置（端口/模型/Skills）
│   ├── skills/
│   │   └── web/SKILL.md            # Web 搜索/浏览 Skill（注入 Agent 系统提示）
├── skillserver/
│   ├── Dockerfile.skillserver       # python:3.12-slim + Playwright Chromium
│   ├── requirements.txt             # fastapi / uvicorn / httpx / playwright
│   ├── server.py                    # FastAPI 服务（8 个 HTTP 端点）
│   └── web_core.py                  # 可复用 Web 核心逻辑（搜索/浏览/截图）
└── searxng/
    └── settings.yml                 # SearXNG 配置
```

## 服务说明

### openclaw（端口 18789，对外）
- 镜像：`openclaw:local`（`node:22-bookworm-slim`，官方安装脚本写入）
- 模型：`github-copilot/claude-sonnet-4.6`（在 `openclaw.json` 中修改）
- 内置 web 工具已禁用，改由 Skill + SkillServer 提供搜索/浏览能力
- `openclaw.json` 采用「暂存 → 复制」挂载方式（挂载到 `/tmp/openclaw.json.src:ro`，启动时复制到 `/home/node/.openclaw/`），确保容器内配置文件可写
- 持久化卷：`openclaw-home`（配置/配对信息）、`openclaw-data`（工作区）、`screenshot-media`（截图共享卷，与 SkillServer 共用）

### skillserver（端口 3000，仅内网）
- 镜像：`skillserver:local`（`python:3.12-slim` + Playwright Chromium）
- FastAPI REST 服务（`server.py` + 核心逻辑 `web_core.py`），提供以下端点：

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /search` | SearXNG 快速搜索（返回摘要片段）|
| `GET /deep_search` | 浏览器渲染后抓取完整页面正文 |
| `GET /navigate` | 访问单个 URL，返回页面文本（支持 `format=html`）|
| `GET /extract_text` | 按 CSS selector 提取指定元素文本 |
| `GET /extract_links` | 提取页面所有链接（最多 200 条）|
| `GET /headlines` | 提取页面所有标题（h1–h6，最多 200 条）|
| `GET /screenshot` | 截图保存至共享卷，返回 `MEDIA:` 路径供 OpenClaw 渲染 |

### searxng（端口 8080，仅内网）
- 官方 `searxng/searxng:latest` 镜像，不对外暴露
- 由 `skillserver` 调用；密钥 `SEARXNG_SECRET` 自动生成写入 `.env`

## Skill 说明

`openclaw/skills/web/SKILL.md` 在 Agent 启动时注入系统提示，教会 Agent：
- 用 `curl http://skillserver:3000/search?q=...` 搜索
- 用 `curl http://skillserver:3000/navigate?url=...` 阅读网页
- 用 `curl http://skillserver:3000/deep_search?q=...` 深度研究
- 用 `curl http://skillserver:3000/screenshot?url=...` 截图

无需修改 OpenClaw 配置，即可扩展 Agent 能力。

## 配置说明

**改模型**：编辑 `openclaw/openclaw.json` → `agents.defaults.model.primary`，然后 `--start` 重启生效。

可用的 GitHub Copilot 模型示例：`github-copilot/claude-sonnet-4.6`、`github-copilot/gpt-5.4`、`github-copilot/gpt-4o`

**重建镜像**：`python3 deploy.py --build` 同时重建 `openclaw:local` 和 `skillserver:local`，并确保 `searxng/searxng:latest` 存在（缺失时自动拉取）。首次构建含 Playwright Chromium，约需 3–5 分钟。

**时区**：所有容器默认使用 `Asia/Shanghai` 时区（`TZ` 环境变量 + `/etc/localtime` 挂载），如需修改请编辑 `docker-compose.yml` 中三个服务的 `TZ` 值。

**端口冲突**：`openclaw.json`（监听端口）、`docker-compose.yml`（映射端口）、`deploy.py`（健康检查端口）三处须一致。

**修改密码**：编辑 `.env` 中的 `AUTH_PASSWORD`（首次部署自动随机生成），修改后运行 `--start` 重启生效。

## 密钥（.env，自动管理）

| 变量 | 说明 |
|------|------|
| `COPILOT_GITHUB_TOKEN` | GitHub OAuth 令牌（`deploy.py` 自动通过设备流程获取）|
| `OPENCLAW_GATEWAY_TOKEN` | 网关配对令牌（自动生成，首次打开 OpenClaw UI 时输入）|
| `AUTH_PASSWORD` | OpenClaw UI 登录密码（首次部署自动生成随机值）|
| `SEARXNG_SECRET` | SearXNG 加密密钥（自动生成）|

## 安全措施

- **URL 校验**：SkillServer 所有接受 URL 参数的端点均校验 scheme，仅允许 `http/https`，防止 SSRF 攻击
- **Shell 注入防护**：`deploy.py` 中所有拼接到 shell 命令的参数均通过 `shlex.quote()` 转义
- **密钥自动生成**：`AUTH_PASSWORD`、`OPENCLAW_GATEWAY_TOKEN`、`SEARXNG_SECRET` 首次部署自动生成随机值
- **令牌遮蔽**：日志输出自动遮蔽 `ghu_` 令牌，防止泄露
- **最小暴露**：仅 OpenClaw 端口 18789 对外，SkillServer 和 SearXNG 仅在 Docker 内网可访问
- **.env 勿提交**：`.env` 含敏感密钥，已在 `.gitignore` 中排除（如未排除请手动添加）

## FAQ

| 问题 | 解决方案 |
|------|---------|
| Gateway Token 在哪 | `.env` 文件 → `OPENCLAW_GATEWAY_TOKEN` |
| Copilot 401 / 403 | 运行 `python3 deploy.py --newtoken` 重新授权 |
| 模型改了没生效 | 改 `openclaw.json` 后须运行 `--start` 重启容器同步配置 |
| Agent 不会搜索 | 检查 `--check` 中 SkillServer 连通性；确认 `skills/web/SKILL.md` 已挂载 |
| SkillServer 启动慢 | Playwright Chromium 首次启动需几秒，属正常现象 |
| `resource busy or locked, rename openclaw.json` | 确认 `openclaw.json` 未直接以 `:ro` 挂载到目标路径（应挂载到 `/tmp/` 再复制）|
