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

系统由独立来源同步和五个创作阶段组成：

1. 来源同步：通过 TrendRadar Streamable HTTP MCP 幂等保存原生 RSS/可选热榜条目到 `source_entries`。
2. 热点获取：将查询窗口内全部来源条目按事件聚类，Gemini 分批翻译、分类并标记风险，形成全类型原始热点。
3. 图案提取：把全部原始热点分批转译为安全、原创、可复用、与产品无关的视觉方向，优先保留主体、关键动作和场景并形成可识别的漫画或编辑插画；抽象元素只辅助具体叙事，或在具体画面不安全时替代，写入 `trends`。
4. 提示词生成：为全部尚未处理的视觉方向逐条生成结构化 `creative_tags`、`pattern_prompt` 和参考图产品提示词，写入 `prompt_pool`。
5. 图案生成：随机抽取提示词交给 Flow，生成无产品的独立漫画、图标、徽章、符号、抽象纹样或其他图案，写入 `pattern_assets`。
6. 产品图生成：通过 Flow 图生图传入实际图案资产，把同一图案印在产品上，记录 `prompt_id` 和 `pattern_asset_id`。

核心关系为：

```text
Source entry → Source cluster → Raw trend → Pattern-pool entry → Prompt-pool entry → Pattern asset → Product rendering
```

来源同步不创建任务。点击“获取热点”会同步来源并在同一任务中建立热点、视觉方向和提示词三池。只有图案生成随机抽取；提示词池可重复生成不同图案，`used_count` 记录图案生成次数。产品图只消费成功且尚未生成对应产品图的图案资产。

热点聚类产生的 `topic_zh` 和 `summary_zh` 同步写回该来源簇中的 `source_entries.title_zh/summary_zh`。“原始资讯”卡片和详情优先显示中文，保留英文原标题和原文摘要；尚未参加聚类的新条目暂时显示原文，下一次获取热点时补齐。

## 业务边界

- 配置 TrendRadar MCP 后，新任务只使用 TrendRadar 原生 RSS/可选热榜；地区和“热点来源平台”仅供未配置 MCP 时的旧 Gemini prompt-first 回退模式使用。
- “热点来源平台”即使在回退模式中也只表示信号来源，不是图片发布渠道或目标平台。
- 来源 URL 和发布时间为可选参考，不是流水线门禁；不得伪造来源。
- 不设最终最多保留热点数量；不能因 `Exceeds the maximum accepted limit of 5 trends` 拒绝原始热点。
- `candidate_count` 仅为 Gemini 聚类注释、图案提取和提示词生成的批处理大小，不是热点总数上限。
- 第二阶段默认尝试转译每个热点，必须保留可识别的事件锚点。球队、品牌和真人身份改为无标志、非肖像化的通用角色，但不得因此删除热点的关键动作、冲突和环境；只有无法安全、尊重事实且不侵权表达时才不进入可用图案池。
- 第三阶段为每个方向输出独立图案提示词和约 140–240 个英文单词的参考图产品提示词；图案可以是漫画、图标、徽章、符号、抽象纹样等，但必须与事件相关。
- 图案请求在调用 Flow 前统一由 `_isolated_pattern_prompt()` 套用最高优先级无背景规范，因此旧提示词池同样生效：只输出透明背景 PNG 的可印刷像素，禁止全画布场景、海报矩形、色块底板、边框、投影和产品；透明通道不可用时只允许纯白 `#FFFFFF`。漫画情节只能位于紧凑图案轮廓或 vignette 内。产品参考提示词会把连接图片边缘的纯白区域视为透明不印刷区，并保留图案内部白色细节。
- 第三阶段可选择杯子、随行杯、手机壳、T 恤、卫衣、帆布袋、抱枕、毯子、备胎罩、贴纸、海报或其他可印刷物品。
- 图案必须自然服从物品曲面、褶皱、接缝、材质、位置和比例；每个提示词只生成一个主要物品。
- 项目不得导入、复制或修改 Gemini2API/Flow2API 源码，也不得管理其容器。

## 状态与执行规则

- `awaiting_classification`：全类型原始热点已获取，等待图案提取。
- `trend_pool_ready`：可用图案提取完成；内部状态名为兼容旧记录保留。
- `prompt_pool_ready`：提示词池已生成或追加。
- `pattern_assets_ready`：独立图案已生成，可继续生成产品图。
- `completed` / `partial` / `failed` / `cancelled`：生图或阶段最终结果。
- `ready`：可用图案池或提示词池条目可用。
- 任一时刻只允许一个阶段任务运行，冲突返回 HTTP `409`。
- 手动“获取热点”固定建立三池，“一键完整流水线”依次执行独立图案和参考图产品图。
- 热点获取和两步生图拥有独立间隔；`enabled` 控制前三池任务，`auto_generate` 控制定时执行“图案→产品”。间隔允许 15–1440 分钟，服务重启后从当前时间周期继续，不集中补跑。
- 调度和定时自动生图默认均关闭。
- 来源同步拥有独立开关和间隔，默认关闭、默认 10 分钟；来源条目默认保留 30 天，热榜导入默认关闭。

## 当前默认策略

- 热点、图案与提示词生成间隔：165 分钟（2 小时 45 分）
- 随机图案与产品图间隔：90 分钟（1.5 小时）
- 时区：`Asia/Shanghai`
- 回溯窗口：24 小时
- 地区：当前 RSS 清单以美国媒体为主、全球英语外媒补充；仅回退模式读取地区设置
- 来源平台：TrendRadar 配置的原生 RSS；X、TikTok、Instagram、YouTube、Reddit 仅为回退模式设置
- AI 批处理大小：10，不限制最终热点总数
- 每轮随机图案/产品数：5，可配置 1–30（内部兼容字段仍名为 `images_per_trend`）
- Gemini 获取模型：`gemini-pro-thinking`
- Gemini 分类/提示词模型：`gemini-flash`
- Flow 默认模型：`gemini-3.1-flash-image-landscape`
- 生图并发：2，接口允许 1–5
- 定时调度、定时自动生图、飞书通知：关闭

可选模型以 `app/service.py` 中的 `GEMINI_MODELS` 和 `FLOW_MODELS` 为唯一事实来源。Flow 模型列表不包含 2K/4K 别名。

## 主要文件

- `app/main.py`：FastAPI、来源池和创作流水线 API；页面与 API 默认同源直连。
- `app/service.py`：TrendRadar MCP、SQLite、事件聚类、Gemini/Flow 调用、调度、安全筛选和飞书通知。
- Gemini 共用调用会先校验响应 JSON；模型返回缺少双引号、尾逗号等无效 JSON 时在既有尝试次数内自动重试，并兼容字符串中的裸控制字符，避免单个坏批次直接终止长任务。
- `static/index.html`：来源、来源条目、三池、生图、详情和图片放大界面。
- `tests/test_service.py`：解析、阶段隔离、提示词池消费、取消恢复和自动连接测试。
- `CONTEXT.md`：领域术语和统一命名。
- `docs/PROJECT_OVERVIEW_AND_CHANGELOG.md`：必须持续更新的架构总览与变更日志。
- `data/`：SQLite 和生成图片，不能进入 Git。

## 2026-08-26 接手说明

- 新增 `raw_sellability_pool` 热点评分池：全部原始热点都能独立评分，不依赖可用图案池；新任务可通过 `source_candidate_id` 复制评分供页面查看，但评分不控制生图配额、不阻断后续阶段。
- 图案生成现在使用 `Pillow + rembg/u2netp`：先清除纯色边缘，检测到假透明、棋盘格或不透明边缘时直接提取前景，再重新编码为 `.png`；只有检测到 alpha 通道才入库。模型固定保存在 `/app/data/rembg/u2netp.onnx`。
- 旧任务不会凭空出现评分；`POST /api/sellability/backfill` 和销售候选页“补算历史评分”会一次补齐所有缺分方向，并保留原任务终态。
- `/api/state.sellability` 返回全部/已评分/待评分方向数、当前任务、历史任务总数和完成数；销售候选页用它展示实时补算进度、完成状态或错误。每完成一个任务，该任务的评分卡立即可见。
- 销售候选页提供原生 `<details>` 评分规则面板，列出七项权重、判断内容、商业理由、通用分档、A–D 区间和生图配额，并声明服务端汇总总分、低分不删除及 AI 估算边界。
- 启动迁移会根据 `raw_discovery` 修复旧任务为 0 的 `candidate_count`；历史补算和 `/api/state.sellability` 均以原始热点数为准，解决有热点但进度显示 `0 / 0`。
- 可卖分主对象已改为原始热点：`raw_sellability_pool` 用 `(run_id,candidate_id)` 保存评分，即使任务还没有 `trends` 也能补算和展示；图案方向再按 `source_candidate_id` 继承评分与配额。进度中的总数是历史原始热点数，不是可用图案方向数。
- 评分规则面板逐项写明判断内容、商业原因和通用强弱分档；每张评分卡的 `metrics[].judgement` 是模型针对该热点生成的实际评分理由。
- A/B/C/D 仅是分析等级；图案和产品图都从对应池真正随机抽取，评分未完成或失败不影响生图。热点、销售候选、图案图库和产品图库支持分页筛选与排序；新增 `/sellability`。
- 当前产品生成白名单固定为 `vehicle spare-tire cover`（备胎罩）和 `phone case`（手机壳）；主体标签保持开放枚举，但产品提示词和产品图选择不得输出其它载体。
- 评分请求与分类/提示词/生图完全独立；`discover`/`full` 不等待评分，评分失败不阻断后续流程。图案和产品阶段均使用 `random.sample` 从当前池随机抽取，分数只用于销售候选页面筛选排序。
- 服务机若无法访问 Docker Hub，需先在运行容器执行 `docker exec social-trend-creative pip install --no-cache-dir "Pillow>=10,<12"`，再采用 `docker cp`、`docker commit` 和 `docker compose up -d --force-recreate --no-build` 更新。

## 管理 API

管理页面和 API 默认不鉴权，浏览器打开页面后自动加载状态。服务端口 `5920` 只应开放在可信内网；公网部署必须在反向代理层补充认证和 HTTPS。

- `GET /api/state`
- `GET /api/system/update`：读取最近一次项目更新状态
- `POST /api/system/update`：请求服务机更新器拉取 `main`、重建并重启本项目
- `PUT /api/config`
- `POST /api/connections/test`
- `POST /api/runs/discover`：①②③同一轮次顺序执行
- `POST /api/runs/{run_id}/classify`：②拆分分类
- `POST /api/runs/{run_id}/sellability`：④计算可卖分、等级、风险和生成配额
- `POST /api/sellability/backfill`：为全部历史缺分任务补算可卖分
- `POST /api/runs/{run_id}/prompts`：③为全部未处理图案补齐提示词；旧 `count` 参数保留兼容但不参与抽样
- `POST /api/runs/{run_id}/patterns`：④随机生成独立图案，JSON 为 `{}` 或 `{"count":3}`
- `POST /api/runs/{run_id}/products`：⑤用未消费图案生成产品图
- `POST /api/runs/{run_id}/generate`：兼容入口，连续执行④⑤
- `POST /api/runs/full`：完整流水线
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/cancel`
- `DELETE /api/runs/{run_id}`

## 管理页面

- `/`：全局总览、统计、连接、设置和一键完整流水线。
- `/sources`：顶部“信息采集”入口；组内标签切换媒体源和 `/signals` 原始资讯，点击来源可筛选其条目。
- `/acquire`：顶部“AI 创意”入口；组内标签切换全部热点、`/trends` 可用图案和 `/prompts` 生成提示词。
- `/sellability`：销售候选；展示 AI 可卖分、七项指标、风险、推荐物品和等级配额，点击卡片展开完整判断。
- `/patterns`：顶部“生图工坊”入口中的图案图库，展示独立图案。
- `/images`：生图工坊中的产品图库，展示引用实际图案生成的产品图片。
- TrendRadar `:8080` 入口位于信息采集摘要区；独立关联服务栏、RSSHub 和 NewsNow 入口均已移除。
- 点击任意内容卡片只打开对应热点、图案、提示词或图片，顶部同时显示所属任务摘要；图片本身仍可单独点击放大。
- 任务详情可补齐中断阶段，并可分别为随机图案和产品图指定 1–30 张。

旧 URL 保持可直接访问，但顶部仅呈现总览、信息采集、AI 创意和生图工坊四个工作区。全部视图由同一份 `static/index.html` 原生路由实现；生图工坊用组内标签区分图案图库和产品图库。

视觉规范采用 StyleKit `Apple 风格`：`#f5f5f7` 页面背景、白色内容面、`#0071e3` 强调色、`-apple-system` SF Pro 字体栈、8px 卡片圆角和轻阴影；旧日系样式块已停用，不再加载外部字体，植物装饰和纸张纹理不显示。

所有池页面通过共用 `renderLazyCards` 分批渲染，首批和后续批次均为 24 张；原始资讯使用 `/api/signals?limit=24&offset=...`，热点、销售候选、视觉方向、提示词、图案资产和产品图使用 `/api/cards/{pool}?limit=24&offset=...`。热点和图案池支持关键词、分类、等级、可卖分排序，图案池另支持透明 PNG 筛选。IntersectionObserver 在距视口约 600px 时获取并追加下一批，图片使用原生 `loading="lazy"` 和 `decoding="async"`。

状态轮询只在页面签名实际变化时重新加载卡片；同一页已有请求尚未返回时不会递增 `moduleLoadToken`，因此慢查询不会被 3 秒运行态轮询连续作废。各卡片表按 `created_at` 建有倒序索引，来源按 `fetched_at` 和 `COALESCE(published_at,fetched_at)` 建有统计及展示顺序索引；`dashboard()` 使用 SQLite `json_each` 直接聚合平台分布，全类型热点分页通过游标按需解析任务 JSON。`/api/state`、卡片、来源和任务详情等同步查询使用普通 FastAPI 路由，由线程池执行。

`list_runs()` 只查询任务摘要字段，禁止重新加入 `raw_discovery` 或 `raw_verification`；完整 AI 响应只由单任务详情读取，避免 `/api/state` 首屏和轮询重复传输大字段。

## 数据与迁移

- SQLite：`data/trend-creative.db`
- 图片：`data/assets/<run>/<trend>/`
- Docker 挂载：`./data:/app/data`
- `trends` 是视觉方向池（旧表名保留）；`prompt_pool` 保存结构化 `creative_tags`、图案/产品提示词；`pattern_assets` 和 `generations` 会随关联提示词返回这些标签。
- 启动时自动创建 `pattern_assets`，为旧 `prompt_pool` 补 `pattern_prompt` 和 `creative_tags`，并为旧 `generations` 补 `pattern_asset_id`，不需要手工迁移。
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

- TrendRadar MCP 来源同步、来源条目池、事件聚类和五阶段创作流水线已完成。
- 全类型原始热点、视觉方向池、双提示词池、独立图案资产和产品图记录已分开持久化。
- 随机提示词先生成图案、产品图再通过 Base64 参考图使用同一图案，`prompt_id` 与 `pattern_asset_id` 均可追踪。
- 管理页已收敛为总览、信息采集、AI 创意和生图工坊四个顶部工作区；生图工坊包含图案图库和产品图库。
- 物品图、来源可选、全球优先地区、图片放大、取消/删除、调度和通知均保留。
- 当前测试以仓库最新 `python -m unittest discover -s tests -v` 结果为准。

新对话应以仓库实际工作树和 `main` 分支为准，不依赖原对话上下文。
页面进度优先使用 `/api/state.sellability.total_hotspots`、`scored_hotspots`、`pending_hotspots`，显示“热点”口径；旧 `*_directions` 字段仍保留兼容。
TrendRadar 同步后台任务会捕获并记录异常；`safe_error()` 会展开 `ExceptionGroup` 子错误。排查采集失败时先看 `source_state.sync.error` 和 `docker logs`，其中应包含 MCP 工具名及具体网络/协议原因。
Gemini JSON 解析先走标准 JSON，随后用 `json5` 兼容尾逗号、单引号和裸键名，再用 `json-repair` 尝试恢复缺逗号/截断等结构；分类、评分、提示词等高输出阶段单批最多 5 条，500 类 HTTP 错误重试采用 5/10 秒退避。服务机需要同步安装 `json5>=0.9,<1` 和 `json-repair>=0.30,<1`。
MCP 工具调用使用 `MCP_TIMEOUT_SECONDS=300` 超时边界；采集任务会复用最近 10 分钟成功的来源同步，避免重复请求。旧卡住的 acquisition 任务需取消后重新创建。
新增 `POST /api/runs/cleanup-empty` 和总览页清理按钮，用于删除 `candidate_count=0` 且没有可解析原始热点的任务；删除前会取消当前卡住的空采集任务，已有内容的任务不受影响。服务启动和 `/api/state` 读取任务列表时还会自动删除超过 10 分钟的空任务残留，当前活动任务保留，避免页面继续显示旧的“进行中”轮次。
