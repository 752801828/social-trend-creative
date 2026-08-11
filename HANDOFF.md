# Social Trend Creative 新对话交接

本文件用于把本项目完整移交到新的 Codex 对话。后续不要在原 Gemini2API/Flow2API 对话中继续开发本项目。

## 新对话启动语

把下面这段直接发送到新对话：

```text
请接手 social-trend-creative 项目。先完整阅读仓库根目录的 AGENTS.md、HANDOFF.md、CONTEXT.md、README.md，以及 docs/PROJECT_OVERVIEW_AND_CHANGELOG.md；然后检查 git status、当前分支和最近提交，不要重复已经完成的工作。项目必须保持独立，只通过 HTTP API 调用 Gemini2API 和 Flow2API。每次代码、配置、UI、API、数据或部署修改都要同步更新 docs/PROJECT_OVERVIEW_AND_CHANGELOG.md；不要提交 .env、API Key、Cookie、Webhook、签名密钥、数据库或生成图片。完成修改后运行测试、提交并推送 main，再返回服务机的 git pull 与 docker compose 更新命令。
```

## 仓库与路径

- GitHub：<https://github.com/752801828/social-trend-creative>
- 默认分支：`main`
- 本机目录：`C:\Users\WY001\Documents\反代\social-trend-creative`
- 服务机目录：`D:\social-trend-creative`
- 服务端口：`5920`
- Docker 服务与容器：`social-trend-creative`

开始工作前执行：

```cmd
cd /d C:\Users\WY001\Documents\反代\social-trend-creative
git status -sb
git log -5 --oneline
git pull --ff-only origin main
```

## 项目目标与边界

这是独立的海外社媒热点创作服务：

1. Gemini2API 发现最近时间窗口内的海外社媒热点。
2. Gemini2API 第二次核验候选热点、证据 URL、发布时间和视觉价值。
3. 本服务执行来源、时效和安全门禁并保存审核状态。
4. 人工选择热点后，Flow2API 根据热点生成原创编辑视觉。
5. SQLite 保存轮次、热点、证据、提示词、图片、耗时和错误。

项目不得导入、复制或修改 Gemini2API/Flow2API 源码，也不得启动、停止或重建它们的容器。两个上游只通过 OpenAI 兼容 HTTP API 连接。

## 工作流与状态

- `ready`：至少一个有效 HTTP(S) 证据位于配置的时间窗口内，可以人工生图；开启自动生图时也可自动生成。
- `needs_review`：存在证据 URL，但发布时间缺失或无法解析，只能人工勾选生图。
- `rejected`：无有效来源、证据全部过期或 Gemini 核验拒绝，禁止生图。
- 任一时刻只允许一个发现或生成任务执行，冲突返回 HTTP `409`。
- 定时任务和自动生图默认关闭，上线前必须先人工验证数轮。
- Prompt-first discovery 不能证明 Gemini 一定进行了实时联网搜索；证据 URL 和发布时间门禁是必要保护，不能删除。

## 当前默认策略

- 每日时间：`09:00`
- 时区：`Asia/Shanghai`
- 回溯窗口：24 小时
- 地区：美国、英国、欧洲、全球英语区
- 平台：X、TikTok、Instagram、YouTube、Reddit
- 候选数：10
- 最终热点数：5
- 每个热点图片数：1
- Gemini 发现模型：`gemini-pro-thinking`
- Gemini 核验模型：`gemini-flash`
- Flow 默认模型：`gemini-3.1-flash-image-landscape`
- 生图并发：2，接口允许范围 1–5
- 定时调度：关闭
- 自动生图：关闭
- 飞书通知：关闭

可选模型以 `app/service.py` 中的 `GEMINI_MODELS` 和 `FLOW_MODELS` 为唯一事实来源。Flow 模型列表不包含 2K/4K 别名。

## 主要文件

- `app/main.py`：FastAPI 入口、Bearer 鉴权和管理 API。
- `app/service.py`：配置、SQLite、Gemini 发现与核验、Flow 生图、调度、门禁和飞书通知。
- `static/index.html`：独立管理页面。
- `tests/test_service.py`：核心解析、门禁和数据行为测试。
- `docker-compose.yml` / `Dockerfile`：独立容器部署。
- `data/`：SQLite 和生成图片挂载目录；运行数据不进入 Git。
- `docs/PROJECT_OVERVIEW_AND_CHANGELOG.md`：项目总览与必须持续更新的变更日志。

## 环境变量

从 `.env.example` 复制生成 `.env`，不要把真实值提交到 Git：

```env
ADMIN_KEY=<管理密钥>
GEMINI_BASE_URL=http://host.docker.internal:5918
GEMINI_API_KEY=<Gemini2API Key>
FLOW_BASE_URL=http://host.docker.internal:38000
FLOW_API_KEY=<Flow2API Key>
PUBLIC_BASE_URL=
FEISHU_WEBHOOK_URL=
FEISHU_SIGNING_SECRET=
DATA_DIR=/app/data
PORT=5920
```

`PUBLIC_BASE_URL`、飞书 Webhook 和签名密钥为可选项。不得在日志、文档、测试或提交中写入真实密钥。

## 管理 API

除 `/`、`/health` 和 `/assets/...` 外，管理 API 使用：

```http
Authorization: Bearer <ADMIN_KEY>
```

- `GET /api/state`
- `PUT /api/config`
- `POST /api/connections/test`
- `POST /api/runs/discover`
- `POST /api/runs/full`
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/generate`
- `POST /api/runs/{run_id}/cancel`
- `DELETE /api/runs/{run_id}`

## 数据与持久化

- SQLite：`data/trend-creative.db`
- 图片：`data/assets/<run>/<trend>/`
- Docker 挂载：`./data:/app/data`
- `.gitignore` 排除 `.env`、数据库、缓存和生成媒体。

删除轮次会同时删除对应数据库记录和本地生成图片。修改删除逻辑前必须保留运行中任务保护和路径范围校验。

## 验证命令

```cmd
cd /d C:\Users\WY001\Documents\反代\social-trend-creative
python -m pytest -q
docker compose config
```

部署后：

```cmd
curl http://localhost:5920/health
docker compose ps
docker compose logs --tail=100 social-trend-creative
```

预期健康响应：

```json
{"status":"ok","service":"social-trend-creative"}
```

## 服务机首次部署

```cmd
cd /d D:\
git clone https://github.com/752801828/social-trend-creative.git D:\social-trend-creative
cd /d D:\social-trend-creative
copy /Y .env.example .env
notepad .env
docker compose up -d --build
curl http://localhost:5920/health
```

如果目标目录已经存在，不要直接删除；先确认其中的 `.env` 和 `data` 是否需要保留。

## 服务机后续更新

```cmd
cd /d D:\social-trend-creative
git pull --ff-only origin main
docker compose up -d --build
docker compose ps
curl http://localhost:5920/health
```

## 后续修改规则

1. 先读取本文件及 `AGENTS.md`，再检查实际代码和 Git 状态。
2. 不把 Gemini2API/Flow2API 的账号、Cookie、浏览器 Profile 或代理逻辑耦合进本项目。
3. 不把提示词返回的“已联网”当作证据；门禁必须基于结构化来源和时间。
4. 不默认开启定时任务或自动生图。
5. 每次功能修改同步更新测试和 `docs/PROJECT_OVERVIEW_AND_CHANGELOG.md`。
6. 默认推送 `752801828/social-trend-creative` 的 `main` 分支。
7. 每次推送后返回服务机更新命令。

## 当前完成状态

- 独立 FastAPI 服务、SQLite、管理页面和 Docker 部署已完成。
- Gemini 两阶段发现与核验、来源时间门禁已完成。
- 人工选题、Flow 多模型随机生图、并发、图片落盘和失败记录已完成。
- 每日调度、自动生图开关、连接检查和可选飞书通知已完成。
- 管理页面的统计、轮次详情、来源链接、图片预览、终止和删除已完成。
- 初始测试共 8 项，交接时全部通过。

新对话应以仓库实际 `main` 分支为准，不依赖原对话上下文。
