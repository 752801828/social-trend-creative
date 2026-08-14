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

系统是四个彼此独立、可单独触发的阶段：

1. 热点获取：Gemini 在配置数量内获取全球所有类型原始社媒热点，不按产品适配、视觉潜力、品牌、人物或敏感类型过滤；仅事实化记录并标记风险。
2. 图案提取：从原始热点提取安全、原创、可复用、与产品无关的视觉图案方向，写入可用图案池 `trends`；一个热点可以产生零个、一个或多个图案。
3. 提示词生成：为同一轮次全部尚未处理的可用图案逐条生成对应产品效果图提示词，写入 `prompt_pool`；不随机抽样、不重复已有图案。
4. 生图：随机抽取 `prompt_pool` 条目交给 Flow，把图案直接生成在产品上，图片记录保存对应 `prompt_id`。

核心关系为：

```text
Raw trend → Pattern-pool entry → Prompt-pool entry → Generation task
```

点击“获取热点”会在同一轮次依次完成前三阶段。只有第四阶段随机抽取；提示词池条目可以重复用于多次生图，`used_count` 记录使用次数。已形成可用图案池后禁止重新提取，因为替换该池会级联影响现有提示词和图片。

## 业务边界

- 全球搜索，配置地区只是优先覆盖，不是排他过滤。
- “热点来源平台”是信号来源，不是图片发布渠道或目标平台。
- 来源 URL 和发布时间为可选参考，不是流水线门禁；不得伪造来源。
- 不设最终最多保留热点数量；不能因 `Exceeds the maximum accepted limit of 5 trends` 拒绝原始热点。
- 第一阶段在 `candidate_count` 范围内采集全类型热点，不因品牌、人物、政治、争议、成人讨论、赌博、仇恨或暴力类型直接遗漏；敏感内容只做高层事实记录并标记风险。
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
- 手动“获取热点”固定运行前三阶段，“一键完整流水线”固定运行四阶段。
- 热点获取和随机生图拥有独立的每日多时间点；`enabled` 控制前三阶段任务，`auto_generate` 控制定时随机生图。时间字段使用逗号分隔的 `HH:MM`，每类最多 24 个时间点。
- 调度和定时自动生图默认均关闭。

## 当前默认策略

- 每日热点获取时间：默认 `09:00`，可填 `09:00,14:00,20:00`
- 每日随机生图时间：默认 `10:00`，可填 `10:00,15:00,21:00`
- 时区：`Asia/Shanghai`
- 回溯窗口：24 小时
- 地区：美国、英国、欧洲、全球英语区（优先覆盖）
- 来源平台：X、TikTok、Instagram、YouTube、Reddit
- 全类型原始热点数：10
- 每轮随机生图数：5，可配置 1–30（内部兼容字段仍名为 `images_per_trend`）
- Gemini 获取模型：`gemini-pro-thinking`
- Gemini 分类/提示词模型：`gemini-flash`
- Flow 默认模型：`gemini-3.1-flash-image-landscape`
- 生图并发：2，接口允许 1–5
- 定时调度、定时自动生图、飞书通知：关闭

可选模型以 `app/service.py` 中的 `GEMINI_MODELS` 和 `FLOW_MODELS` 为唯一事实来源。Flow 模型列表不包含 2K/4K 别名。

## 主要文件

- `app/main.py`：FastAPI、Bearer 鉴权和四阶段管理 API。
- `app/service.py`：配置、SQLite、四阶段服务、Flow 调用、调度、安全筛选和飞书通知。
- `static/index.html`：四阶段管理页、轮次详情、提示词池和图片放大。
- `tests/test_service.py`：解析、阶段隔离、提示词池消费、取消恢复和鉴权隔离测试。
- `CONTEXT.md`：领域术语和统一命名。
- `docs/PROJECT_OVERVIEW_AND_CHANGELOG.md`：必须持续更新的架构总览与变更日志。
- `data/`：SQLite 和生成图片，不能进入 Git。

## 管理 API

除 `/`、`/health` 和 `/assets/...` 外均使用：

```http
Authorization: Bearer <ADMIN_KEY>
```

- `GET /api/state`
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
- `/acquire`、`/trends`、`/prompts`、`/images` 均直接展示跨任务内容卡片，按内容日期倒序排列，无需先选轮次。
- 点击任意内容卡片打开所属任务详情；图片本身仍可单独点击放大。
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

真实密钥不得写入代码、日志、文档、测试或 Git。

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

## 后续修改规则

1. 先读取 `AGENTS.md`、本文件、`CONTEXT.md`、`README.md` 和持续变更日志，再检查代码与 Git。
2. 不把上游账号、Cookie、浏览器 Profile 或代理逻辑耦合进本项目。
3. 每次代码、配置、UI、API、数据或部署修改同步更新 `docs/PROJECT_OVERVIEW_AND_CHANGELOG.md`，必要时也更新本文件和 README。
4. 不默认开启定时任务或定时自动生图。
5. 完成修改后运行测试并报告 Git 状态；提交和推送须遵循用户当次要求。

## 当前完成状态

- 四阶段后端服务、独立接口和一键完整流水线已完成。
- 全类型原始热点、可用图案池、提示词池和生成记录已分开持久化。
- 全量图案对应提示词、随机提示词产品生图及 `prompt_id` 追踪已完成。
- 管理页已拆成总览和四个独立 URL 模块，可按轮次查看全部热点、可用图案池、提示词池和生图池；整体使用 StyleKit 日系清新风。
- 物品图、来源可选、全球优先地区、图片放大、取消/删除、调度和通知均保留。
- 当前测试共 19 项。

新对话应以仓库实际工作树和 `main` 分支为准，不依赖原对话上下文。
