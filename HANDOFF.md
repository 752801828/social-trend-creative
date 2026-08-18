# Social Trend Creative 新对话交接

本文件用于把项目完整移交到新的 Codex 对话。后续不要在 Gemini2API/Flow2API 项目中继续开发本项目。

## 新对话启动语

```text
请接手 social-trend-creative 项目。先完整阅读仓库根目录的 AGENTS.md、HANDOFF.md、CONTEXT.md、README.md，以及 docs/PROJECT_OVERVIEW_AND_CHANGELOG.md；然后检查 git status、当前分支和最近提交，不要重复已经完成的工作。项目必须保持独立，只通过 HTTP API 调用 Gemini2API 和 Flow2API。每次代码、配置、UI、API、数据或部署修改都要同步更新 docs/PROJECT_OVERVIEW_AND_CHANGELOG.md；不要提交 .env、API Key、Cookie、Webhook、签名密钥、数据库或生成图片。完成修改后运行测试，再汇报 Git 状态；只有用户明确要求时才提交或推送。
```

## 仓库与路径

- GitHub：<https://github.com/752801828/social-trend-creative>
- 默认分支：`main`
- 本机目录：`C:\Users\WY001\Documents\反代\social-trend-creative`
- 服务机目录：`D:\social-trend-creative`
- 服务端口：`5920`
- Docker 服务：`social-trend-creative`

开始工作前执行：

```cmd
cd /d C:\Users\WY001\Documents\反代\social-trend-creative
git status -sb
git log -5 --oneline
```

注意：在 `C:\Users\WY001` 直接执行 `git push` 会提示 `not a git repository`；必须先进入上述仓库目录。

## 当前架构

系统由独立来源同步和四个创作阶段组成：

1. 来源同步：通过 TrendRadar Streamable HTTP MCP 幂等保存原生 RSS/可选热榜条目到 `source_entries`。
2. 热点获取：将查询窗口内全部来源条目按事件聚类，Gemini 分批翻译、分类并标记风险，形成全类型原始热点。
3. 图案提取：从全部原始热点分批提取安全、原创、可复用、与产品无关的视觉图案方向，写入 `trends`。
4. 提示词生成：为全部尚未处理的可用图案逐条生成产品效果图提示词，写入 `prompt_pool`。
5. 生图：随机抽取 `prompt_pool` 条目交给 Flow，把图案直接生成在产品上，图片记录保存对应 `prompt_id`。

核心关系为：

```text
Source entry → Source cluster → Raw trend → Pattern-pool entry → Prompt-pool entry → Generation task
```

来源同步不创建任务。点击“获取热点”会同步来源并在同一任务中建立热点、图案和提示词三池。只有生图随机抽取；提示词池条目可以重复用于多次生图，`used_count` 记录使用次数。

## 业务边界

- 全球搜索，配置地区只是优先覆盖，不是排他过滤。
- “热点来源平台”是信号来源，不是图片发布渠道或目标平台。
- 来源 URL 和发布时间为可选参考，不是流水线门禁；不得伪造来源。
- 不设最终最多保留热点数量；不能因 `Exceeds the maximum accepted limit of 5 trends` 拒绝原始热点。
- `candidate_count` 仅为 Gemini 聚类注释、图案提取和提示词生成的批处理大小，不是热点总数上限。
- 第二阶段只输出可安全原创化的图案方向；无法脱离商标、版权角色、真人肖像、仇恨符号、成人/暴力表达或错误信息的方向不得进入可用图案池。
- 第三阶段可选择杯子、随行杯、手机壳、T 恤、卫衣、帆布袋、抱枕、毯子、备胎罩、贴纸、海报或其他可印刷物品。
- 图案必须自然服从物品曲面、褶皱、接缝、材质、位置和比例；每个提示词只生成一个主要物品。
- 项目不得导入、复制或修改 Gemini2API/Flow2API 源码，也不得管理其容器。

## 状态与执行规则

- `awaiting_classification`：全类型原始热点已获取，等待图案提取。
- `trend_pool_ready`：可用图案提取完成；内部状态名为兼容旧记录保留。
- `prompt_pool_ready`：提示词池已生成或追加。
- `completed` / `partial` / `failed` / `cancelled`：生图或阶段最终结果。
- `ready`：可用图案池或提示词池条目可用。
- 任一时刻只允许一个阶段任务运行，冲突返回 HTTP `409`。
- 手动“获取热点”固定建立三池，“一键完整流水线”继续随机产品生图。
- 热点获取和随机生图拥有独立间隔；`enabled` 控制前三阶段任务，`auto_generate` 控制定时随机生图。间隔允许 15–1440 分钟，服务重启后从当前时间周期继续，不集中补跑。
- 调度和定时自动生图默认均关闭。
- 来源同步拥有独立开关和间隔，默认关闭、默认 10 分钟；来源条目默认保留 30 天，热榜导入默认关闭。

## 当前默认策略

- 热点、图案与提示词生成间隔：165 分钟（2 小时 45 分）
- 随机生图间隔：90 分钟（1.5 小时）
- 时区：`Asia/Shanghai`
- 回溯窗口：24 小时
- 地区：美国为主要市场，全球英语区及少量印尼、日本来源用于补充可能影响美国市场的国际事件
- 来源平台：X、TikTok、Instagram、YouTube、Reddit
- AI 批处理大小：10，不限制最终热点总数
- 每轮随机生图数：5，可配置 1–30（内部兼容字段仍名为 `images_per_trend`）
- Gemini 获取模型：`gemini-pro-thinking`
- Gemini 分类/提示词模型：`gemini-flash`
- Flow 默认模型：`gemini-3.1-flash-image-landscape`
- 生图并发：2，接口允许 1–5
- 定时调度、定时自动生图、飞书通知：关闭

可选模型以 `app/service.py` 中的 `GEMINI_MODELS` 和 `FLOW_MODELS` 为唯一事实来源。Flow 模型列表不包含 2K/4K 别名。

## 主要文件

- `app/main.py`：FastAPI、来源池和创作流水线 API；页面与 API 默认同源直连。
- `app/service.py`：TrendRadar MCP、SQLite、事件聚类、Gemini/Flow 调用、调度、安全筛选和飞书通知。
- `static/index.html`：来源、来源条目、三池、生图、详情和图片放大界面。
- `tests/test_service.py`：解析、阶段隔离、提示词池消费、取消恢复和自动连接测试。
- `CONTEXT.md`：领域术语和统一命名。
- `docs/PROJECT_OVERVIEW_AND_CHANGELOG.md`：必须持续更新的架构总览与变更日志。
- `data/`：SQLite 和生成图片，不能进入 Git。

## 管理 API

管理页面和 API 默认不鉴权，浏览器打开页面后自动加载状态。服务端口 `5920` 只应开放在可信内网；公网部署必须在反向代理层补充认证和 HTTPS。

- `GET /api/state`
- `GET /api/system/update`：读取最近一次项目更新状态
- `POST /api/system/update`：请求服务机更新器拉取 `main`、重建并重启本项目
- `PUT /api/config`
- `POST /api/connections/test`
- `POST /api/runs/discover`：①②③同一轮次顺序执行
- `POST /api/runs/{run_id}/classify`：②拆分分类
- `POST /api/runs/{run_id}/prompts`：③为全部未处理图案补齐提示词；旧 `count` 参数保留兼容但不参与抽样
- `POST /api/runs/{run_id}/generate`：④随机提示词生图，JSON 为 `{}` 或 `{"count":3}`
- `POST /api/runs/full`：①②③④完整流水线
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/cancel`
- `DELETE /api/runs/{run_id}`

## 管理页面

- `/`：全局总览、统计、连接、设置和一键完整流水线。
- `/sources`：外媒来源、条目数量、最后内容与同步时间。
- `/signals`：按发布日期倒序展示来源条目，可按来源筛选；点击只打开该条目。
- 操作栏提供 TrendRadar `:8080`、NewsNow `:4444` 外部前端入口，主机名取当前页面地址；端口连通性由部署环境负责。
- `/acquire`、`/trends`、`/prompts`、`/images` 均直接展示跨任务内容卡片，按内容日期倒序排列，无需先选轮次。
- 点击任意内容卡片只打开对应热点、图案、提示词或图片，顶部同时显示所属任务摘要；图片本身仍可单独点击放大。
- 任务详情可补齐中断的图案或提示词阶段，并可为随机生图手动指定 1–30 张。

这些是独立 URL 和独立模块界面，但由同一份 `static/index.html` 原生路由视图实现，避免重复前端代码。页面通过现有任务详情 API 汇总内容卡片，不新增前端框架。

视觉规范沿用 StyleKit `Japanese Fresh（日系清新风）`，背景调整为 `#e8eee8` 灰绿色纸张，卡片使用 `#fffdf6` 暖白色，并提高文字、边框和阴影对比度；标题继续使用 Yeseva One，正文使用 Karla。

## 数据与迁移

- SQLite：`data/trend-creative.db`
- 图片：`data/assets/<run>/<trend>/`
- Docker 挂载：`./data:/app/data`
- `trends` 是可用图案池（旧表名保留）；`prompt_pool` 是提示词池；`generations.prompt_id` 追踪图片使用的提示词。
- 启动时自动创建 `prompt_pool`，并为旧 `generations` 表补 `prompt_id`，不需要手工迁移。
- 删除轮次会级联删除可用图案池、提示词池、生成记录及本地图片；必须保留运行中保护和路径范围检查。

## 环境变量

```env
GEMINI_BASE_URL=http://host.docker.internal:5918
GEMINI_API_KEY=<Gemini2API Key>
FLOW_BASE_URL=http://host.docker.internal:38000
FLOW_API_KEY=<Flow2API Key>
TRENDRADAR_MCP_URL=http://host.docker.internal:3333/mcp
PUBLIC_BASE_URL=
FEISHU_WEBHOOK_URL=
FEISHU_SIGNING_SECRET=
DATA_DIR=/app/data
PORT=5920
```

上游真实密钥不得写入代码、日志、文档、测试或 Git。TrendRadar MCP 服务机地址为 `http://127.0.0.1:3333/mcp`，Docker 内必须使用 `host.docker.internal`。

## 验证与部署

```cmd
cd /d C:\Users\WY001\Documents\反代\social-trend-creative
python -m unittest discover -s tests -v
python -m compileall -q app
docker compose config
```

服务机更新：

```cmd
cd /d D:\social-trend-creative
git pull --ff-only origin main
docker compose up -d --build
docker compose ps
curl http://localhost:5920/health
```

首次启用管理页“更新并重启”按钮时，在服务机用具备 GitHub 拉取权限和 Docker Desktop 权限的当前 Windows 用户安装常驻更新器：

```powershell
cd D:\social-trend-creative
powershell -ExecutionPolicy Bypass -File .\scripts\install-update-watcher.ps1
```

按钮通过同源 `POST /api/system/update` 写入共享 `data` 更新请求；计划任务 `SocialTrendCreativeUpdater` 执行 `git pull --ff-only origin main` 和 `docker compose up -d --build social-trend-creative`。它不会管理 Flow/Gemini 容器；运行中的业务任务或服务机未提交修改都会阻止更新。更新状态写入 `data/update-status.json`，日志写入 `data/update-watcher.log`。

## 后续修改规则

1. 先读取 `AGENTS.md`、本文件、`CONTEXT.md`、`README.md` 和持续变更日志，再检查代码与 Git。
2. 不把上游账号、Cookie、浏览器 Profile 或代理逻辑耦合进本项目。
3. 每次代码、配置、UI、API、数据或部署修改同步更新 `docs/PROJECT_OVERVIEW_AND_CHANGELOG.md`，必要时也更新本文件和 README。
4. 不默认开启定时任务或定时自动生图。
5. 完成修改后运行测试并报告 Git 状态；提交和推送须遵循用户当次要求。

## 当前完成状态

- TrendRadar MCP 来源同步、来源条目池、事件聚类和四阶段创作流水线已完成。
- 全类型原始热点、可用图案池、提示词池和生成记录已分开持久化。
- 全量图案对应提示词、随机提示词产品生图及 `prompt_id` 追踪已完成。
- 管理页已拆成总览、外媒来源、来源条目和四个创作池 URL 模块；整体使用 StyleKit 日系清新风。
- 物品图、来源可选、全球优先地区、图片放大、取消/删除、调度和通知均保留。
- 当前测试以仓库最新 `python -m unittest discover -s tests -v` 结果为准。

新对话应以仓库实际工作树和 `main` 分支为准，不依赖原对话上下文。
