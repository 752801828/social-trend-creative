# Social Trend Creative

独立的全球外媒热点物品图创作服务。TrendRadar 持续汇总外媒 RSS，RSSHub 将没有原生 Feed 的站点转换为可订阅来源；本项目通过 TrendRadar MCP 保存来源条目、聚合同一事件，再提取安全可用图案、生成产品提示词和最终图片。

项目不导入或修改 TrendRadar、RSSHub、newsnow、Gemini2API 或 Flow2API 源码。TrendRadar 通过 Streamable HTTP MCP 连接，Gemini2API 和 Flow2API 通过 HTTP API 连接。

## 五阶段流水线

| 阶段 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- |
| ① 外媒采集 | TrendRadar RSS/热榜，RSSHub 路由 | 通过 TrendRadar MCP 幂等同步文章、帖子和预警，不在此阶段筛选商品价值 | 来源条目池 `source_entries` |
| ② 热点聚类 | 查询窗口内全部来源条目 | 标题聚类同一事件，Gemini 分批翻译、分类并标记风险；`candidate_count` 仅表示 AI 批大小 | 全类型原始热点 |
| ③ 图案提取 | 同一任务的全部原始热点 | Gemini 分批提取安全、原创、可复用且不依赖商标、版权角色或真人肖像的视觉方向 | 可用图案池 `trends` |
| ④ 提示词生成 | 同一任务全部尚未处理的可用图案 | Gemini 为每个图案建立对应产品提示词，写明图案、位置、比例、印刷处理、产品底色、材质、镜头和灯光 | 提示词池 `prompt_pool` |
| ⑤ 生图 | 从提示词池随机抽取的提示词 | Flow 把图案直接生成在单一产品上 | 图片与生成记录 `generations` |

可用物品包括杯子、随行杯、手机壳、T 恤、卫衣、帆布袋、抱枕、毯子、备胎罩、贴纸、海报及其他可印刷物品。图案必须自然贴合物品的曲面、褶皱、接缝和材质，不得像后期贴图。

来源和发布时间是事实证据而不是门禁；没有原文链接的热榜条目仍可进入来源池，系统不会伪造来源，也不设置“最多保留 5 条热点”之类的最终上限。聚类阶段不因类型、敏感性、品牌、人物或视觉价值遗漏热点，而是在 `risk_flags` 标记；图案提取阶段才排除无法安全原创化的方向。

设置中的“热点来源平台”用于告诉 Gemini 优先从 X、TikTok、Instagram、YouTube、Reddit 等来源发现信号；管理页的来源分布表示各来源命中的热点数，不表示图片将发布到哪个平台。

## 对应与随机规则

- 外媒同步独立运行，只更新来源条目池，不创建创作任务。
- 点击“获取热点”会先同步来源，使用查询窗口内全部来源条目建立热点，再在同一个任务中依次完成图案和提示词池。
- 提示词阶段不再随机抽样，而是为该轮次每个尚未处理的可用图案生成一条提示词；重复执行只补齐缺少的提示词。
- 只有生图阶段随机抽取提示词。手动生图可在任务详情中指定 1–30 张；未提供数量时使用设置项 `images_per_trend`，默认 5。
- 提示词池条目可重复使用；`used_count` 记录被生图任务消费的次数，每张图片保存对应 `prompt_id`。
- 已形成可用图案池的轮次禁止重新提取，避免级联删除已有提示词和图片。

## 运行方式

“获取热点”固定建立热点、图案和提示词三池；“一键完整流水线”继续随机生图。外媒来源同步默认每 10 分钟、三池默认每 165 分钟（2 小时 45 分）、随机生图默认每 90 分钟（1.5 小时），三个开关均默认关闭。`candidate_count` 是 Gemini 分批处理大小，不再限制最终热点总数。

每个模块都有独立页面，并可从顶部模块导航进入：

- `/sources`：展示外媒来源、条目数量和最后更新时间；点击来源筛选其条目。
- `/signals`：展示来源条目卡片；点击只查看对应文章或预警的原始信息。
- `/acquire`：展示聚类后的全部热点卡片。
- `/trends`：直接展示所有任务的可用图案卡片。
- `/prompts`：直接展示所有任务的完整提示词卡片。
- `/images`：直接展示全部生成图片，图片可点击放大。

操作栏下方提供 TrendRadar、RSSHub 和 NewsNow 前端入口，使用当前页面主机名自动生成 `8080`、`1200`、`4444` 端口链接并在新窗口打开。链接不代理流量；目标容器、Windows 防火墙和云安全组必须允许对应端口访问。

各池卡片统一按内容创建日期倒序排列，不需要先选择任务；点击卡片会只打开被点击的热点、图案、提示词或图片，并在顶部附带所属任务摘要。页面打开后自动连接同源 API，不再要求输入管理密钥。

界面沿用 StyleKit `Japanese Fresh（日系清新风）`，但将页面底色加深为灰绿色纸张、提高文字和边框对比度，并为内容卡片增加清晰阴影，避免大面积纯白难以辨认。

未配置 `TRENDRADAR_MCP_URL` 时，系统保留旧的 Gemini prompt-first 发现作为兼容回退；该模式不能证明实时联网。生产环境应配置 TrendRadar MCP。

## 本地启动

```cmd
cd /d C:\Users\WY001\Documents\反代\social-trend-creative
copy .env.example .env
notepad .env
docker compose up -d --build
curl http://localhost:5920/health
```

打开 `http://localhost:5920/`。

`.env` 至少配置：

- `GEMINI_BASE_URL` / `GEMINI_API_KEY`。
- `FLOW_BASE_URL` / `FLOW_API_KEY`。
- `TRENDRADAR_MCP_URL`：TrendRadar Streamable HTTP MCP；Docker 中使用 `http://host.docker.internal:3333/mcp`。
- `FEISHU_WEBHOOK_URL` / `FEISHU_SIGNING_SECRET`：可选。
- `PUBLIC_BASE_URL`：可选；配置后飞书通知可附生成图片链接。

Docker Desktop 中使用宿主机服务时，默认地址为：

```text
http://host.docker.internal:5918
http://host.docker.internal:38000
http://host.docker.internal:3333/mcp
```

## 服务机部署

将整个目录复制到 `D:\social-trend-creative`，准备 `.env` 后运行：

```cmd
cd /d D:\social-trend-creative
docker compose up -d --build
docker compose ps
curl http://localhost:5920/health
```

独立服务不会启动、停止或重建 TrendRadar、RSSHub、newsnow、Flow 或 Gemini 容器。

TrendRadar、RSSHub 和 newsnow 继续在各自目录独立部署。本项目不读 TrendRadar SQLite；RSSHub 路由先订阅到 TrendRadar，再由本项目统一从 MCP 获取。

### 网页一键更新

首次部署本功能后，在服务机用当前拥有 GitHub 拉取权限、且能运行 Docker Desktop 的 Windows 用户执行一次：

```powershell
cd D:\social-trend-creative
powershell -ExecutionPolicy Bypass -File .\scripts\install-update-watcher.ps1
```

计划任务 `SocialTrendCreativeUpdater` 会在该用户登录时常驻。管理页的“更新并重启”按钮写入 `data/update-request.json`；更新器确认仓库位于 `main` 且工作区干净后，执行 `git pull --ff-only origin main`、只重建 `social-trend-creative` 服务并等待健康检查恢复。状态显示在按钮与提示消息中，详细日志保存在 `data/update-watcher.log`。热点或生图任务执行中、工作区有未提交修改时会安全拒绝更新。

## API

管理页面和 API 默认不鉴权，打开页面即自动连接。`5920` 端口必须只开放在可信内网；如需公网访问，应在反向代理层增加登录、访问控制和 HTTPS。

- `GET /api/state`：配置、连接、统计和轮次。
- `GET /api/system/update`：读取最近一次项目更新状态。
- `POST /api/system/update`：请求服务机更新器拉取 `main`、重建并重启本项目。
- `PUT /api/config`：更新每日策略。
- `POST /api/connections/test`：测试 TrendRadar MCP、Gemini 和 Flow 连接。
- `GET /api/sources`：来源同步状态、外媒来源和条目统计。
- `POST /api/sources/sync`：立即启动一次 TrendRadar 来源同步。
- `GET /api/signals?limit=1000&source_id=...`：按日期倒序读取来源条目，可按来源筛选。
- `GET /api/signals/{id}`：读取单个来源条目。
- `POST /api/runs/discover`：新建轮次并依次执行①热点获取、②图案提取、③提示词生成。
- `POST /api/runs/{id}/classify`：对现有轮次执行②可用图案提取；路径名为兼容旧客户端保留。
- `POST /api/runs/{id}/prompts`：执行③补齐全部提示词；为兼容旧客户端仍接受 `count`，但不再随机截断。
- `POST /api/runs/{id}/generate`：执行④从提示词池随机生图；JSON 可传 `{"count": 3}`，也可传 `{}`。
- `POST /api/runs/full`：新建轮次并连续执行①②③④。
- `GET /api/runs/{id}`：全类型原始热点、可用图案池、提示词池、来源和图片详情。
- `POST /api/runs/{id}/cancel`：终止当前任务。
- `DELETE /api/runs/{id}`：删除轮次及本地图片。

任一时刻只允许一个阶段任务运行，冲突返回 HTTP `409`。

## 数据

- `runs`：流水线轮次、原始响应、阶段和统计。
- `source_entries`：TrendRadar 来源条目快照，以规范化 URL/来源身份幂等去重。
- `source_sync_state`：最近同步状态、时间、抓取量和新增量。
- `trends`：AI 提取后的可用图案池；表名为兼容旧数据库保留。
- `prompt_pool`：可复用提示词、来源热点、状态和使用次数。
- `generations`：Flow 请求、对应 `prompt_id`、模型、图片、耗时和错误。
- 图片：`data/assets/<run>/<trend>/`。

启动时会自动创建 `prompt_pool`，并为旧 `generations` 表补充 `prompt_id`，无需手工迁移旧 SQLite。

## 验证

```cmd
python -m unittest discover -s tests -v
python -m compileall -q app
docker compose config --quiet
```
