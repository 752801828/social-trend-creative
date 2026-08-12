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

这是独立的海外社媒热点图案创作服务：

1. Gemini2API 发现最近时间窗口内的海外社媒事件、梗、情绪、审美、社群和视觉符号。
2. Gemini2API 第二次把候选整理为可印在杯子、手机壳、衣服、罩类、贴纸和海报等载体上的原创图案方向。
3. 本服务保留可用来源并执行重复、空内容和安全检查；来源与时间不作为生图门禁。
4. 手动主流程获取热点后，Flow2API 为全部可用候选直接生成独立图案；不生成带杯子、手机壳、衣服等载体的商品样机。
5. SQLite 保存轮次、热点、证据、提示词、图片、耗时和错误。

项目不得导入、复制或修改 Gemini2API/Flow2API 源码，也不得启动、停止或重建它们的容器。两个上游只通过 OpenAI 兼容 HTTP API 连接。

## 工作流与状态

- `ready`：热点图案方向可用且未被判定为重复、空内容、不安全或依赖商标、版权角色、真人肖像；无论是否有证据都可以直接生图。
- `needs_review`：仅为兼容旧轮次保留，新发现流程不再因来源时间缺失进入此状态。
- `rejected`：重复、空内容或明确不安全的候选，禁止生图。
- 任一时刻只允许一个发现或生成任务执行，冲突返回 HTTP `409`。
- 定时任务及其自动生图默认关闭；手动完整流程会直接生图，上线前必须先人工验证数轮。
- Prompt-first discovery 不能证明 Gemini 一定进行了实时联网搜索；不得伪造来源，但来源 URL 和发布时间仅作参考，不阻止热点图案生成。

## 当前默认策略

- 每日时间：`09:00`
- 时区：`Asia/Shanghai`
- 回溯窗口：24 小时
- 地区：美国、英国、欧洲、全球英语区
- 平台：X、TikTok、Instagram、YouTube、Reddit
- 候选数：10
- 最终热点数：与候选数一致，不再另设保留上限
- 每个热点图片数：1
- Gemini 发现模型：`gemini-pro-thinking`
- Gemini 核验模型：`gemini-flash`
- Flow 默认模型：`gemini-3.1-flash-image-landscape`
- 生图并发：2，接口允许范围 1–5
- 定时调度：关闭
- 手动完整流程：获取后直接生图
- 定时任务自动生图：关闭
- 飞书通知：关闭

可选模型以 `app/service.py` 中的 `GEMINI_MODELS` 和 `FLOW_MODELS` 为唯一事实来源。Flow 模型列表不包含 2K/4K 别名。

## 主要文件

- `app/main.py`：FastAPI 入口、Bearer 鉴权和管理 API。
- `app/service.py`：配置、SQLite、Gemini 热点视觉提取与复核、Flow 图案生成、调度、安全筛选和飞书通知。
- `static/index.html`：独立管理页面。
- `tests/test_service.py`：核心解析、商品候选筛选和数据行为测试。
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
- `POST /api/runs/full`：获取社媒热点并为全部可用候选直接生成可印刷图案
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
3. 不把提示词返回的“已联网”当作证据，也不伪造来源；来源和时间仅作为可选参考，不作为商品生图门禁。
4. 不默认开启定时任务或定时自动生图；手动完整流程按热点图案模式直接生图。
5. 每次功能修改同步更新测试和 `docs/PROJECT_OVERVIEW_AND_CHANGELOG.md`。
6. 默认推送 `752801828/social-trend-creative` 的 `main` 分支。
7. 每次推送后返回服务机更新命令。

## 当前完成状态

- 独立 FastAPI 服务、SQLite、管理页面和 Docker 部署已完成。
- Gemini 两阶段热点视觉提取与复核、可选来源保存和安全筛选已完成。
- 人工选题、Flow 多模型随机生图、并发、图片落盘和失败记录已完成。
- 每日调度、自动生图开关、连接检查和可选飞书通知已完成。
- 管理页面的统计、轮次详情、来源链接、图片预览、终止和删除已完成。
- 热点图案导向发现、无证据可生图、取消最终保留上限以及手动获取后直接生图已完成。
- 生成图片支持点击放大查看；平台统计表示各来源平台命中的可用热点数量，不表示发布渠道。
- 当前测试共 12 项，覆盖图案提示词、无证据候选、取消最终上限、手动直接生图、图片放大、状态恢复和鉴权隔离。

新对话应以仓库实际 `main` 分支为准，不依赖原对话上下文。
