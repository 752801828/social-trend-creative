# Social Trend Creative

独立的全球外媒热点物品图创作服务。TrendRadar 持续汇总配置好的外媒原生 RSS；本项目通过 TrendRadar MCP 保存来源条目、聚合同一事件，再提取安全可用图案、生成产品提示词和最终图片。

项目不导入或修改 TrendRadar、Gemini2API 或 Flow2API 源码。TrendRadar 通过 Streamable HTTP MCP 连接，Gemini2API 和 Flow2API 通过 HTTP API 连接。

## 五阶段流水线

| 阶段 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- |
| ① 外媒采集 | TrendRadar 原生 RSS/可选热榜 | 通过 TrendRadar MCP 幂等同步文章、帖子和预警，不在此阶段筛选商品价值 | 来源条目池 `source_entries` |
| ② 热点聚类 | 查询窗口内全部来源条目 | 标题聚类同一事件，Gemini 分批翻译、分类并标记风险；`candidate_count` 仅表示 AI 批大小 | 全类型原始热点 |
| ③ 图案提取 | 同一任务的全部原始热点 | Gemini 将普通事件和非视觉话题转译为安全、原创、可复用的抽象纹样、漫画插画、符号图形、几何构成或装饰图案，不要求热点本身是印花题材 | 可用图案池 `trends` |
| ④ 提示词生成 | 同一任务全部尚未处理的可用图案 | Gemini 为每个图案建立对应产品提示词，写明图案、位置、比例、印刷处理、产品底色、材质、镜头和灯光 | 提示词池 `prompt_pool` |
| ⑤ 生图 | 从提示词池随机抽取的提示词 | Flow 把图案直接生成在单一产品上 | 图片与生成记录 `generations` |

可用物品包括杯子、随行杯、手机壳、T 恤、卫衣、帆布袋、抱枕、毯子、备胎罩、贴纸、海报及其他可印刷物品。图案必须自然贴合物品的曲面、褶皱、接缝和材质，不得像后期贴图。

来源和发布时间是事实证据而不是门禁；没有原文链接的热榜条目仍可进入来源池，系统不会伪造来源，也不设置“最多保留 5 条热点”之类的最终上限。聚类阶段不因类型、敏感性、品牌、人物或视觉价值遗漏热点，而是在 `risk_flags` 标记。图案提取默认尝试把每个热点做非字面视觉转译，只有在抽象化后仍无法安全、尊重事实且不侵权表达时才不进入图案池。

当前生产模式已配置 `TRENDRADAR_MCP_URL`，新任务的热点只取自 TrendRadar 已采集的原生 RSS，以及开启 `source_include_hotlists` 后的可选热榜；设置中的“优先地区”和“热点来源平台”仅供未配置 TrendRadar 时的旧 Gemini prompt-first 回退模式使用。管理页的来源分布表示实际来源命中的热点数，不表示图片将发布到哪个平台。

## 对应与随机规则

- 外媒同步独立运行，只更新来源条目池，不创建创作任务。
- 点击“获取热点”会先同步来源，使用查询窗口内全部来源条目建立热点，再在同一个任务中依次完成图案和提示词池。
- 热点聚类时生成的中文标题和中文摘要会写回对应来源条目；“原始资讯”卡片优先展示中文翻译，同时保留英文原标题和详情中的原文摘要。尚未进入热点聚类的新来源显示原文，并在下一次“获取热点”时补齐翻译。
- 提示词阶段不再随机抽样，而是为该轮次每个尚未处理的可用图案生成一条提示词；重复执行只补齐缺少的提示词。
- 只有生图阶段随机抽取提示词。手动生图可在任务详情中指定 1–30 张；未提供数量时使用设置项 `images_per_trend`，默认 5。
- 提示词池条目可重复使用；`used_count` 记录被生图任务消费的次数，每张图片保存对应 `prompt_id`。
- 已形成可用图案池的轮次禁止重新提取，避免级联删除已有提示词和图片。

## 运行方式

“获取热点”固定建立热点、图案和提示词三池；“一键完整流水线”继续随机生图。外媒来源同步默认每 10 分钟、三池默认每 165 分钟（2 小时 45 分）、随机生图默认每 90 分钟（1.5 小时），三个开关均默认关闭。`candidate_count` 是 Gemini 分批处理大小，不再限制最终热点总数。

顶部导航按工作阶段收敛为四个入口，底层数据表和任务关系不合并：

- `/`：总览、统计、自动化设置和任务档案。
- `/sources`：信息采集；组内标签切换“媒体源”和 `/signals`“原始资讯”。
- `/acquire`：AI 创意；组内标签切换“全部热点”、`/trends`“可用图案”和 `/prompts`“生成提示词”。
- `/images`：成品图库；直接展示全部生成图片并支持点击放大。

`/signals`、`/trends`、`/prompts` 继续作为可直接访问的深链接，但不再占用顶部导航。TrendRadar `:8080` 入口移入“信息采集”摘要区；独立“关联服务”栏以及 RSSHub、NewsNow 入口均已移除。

各视图卡片统一按内容创建日期倒序排列，不需要先选择任务；点击卡片会只打开被点击的热点、图案、提示词或图片，并在顶部附带所属任务摘要。页面打开后自动连接同源 API，不再要求输入管理密钥。

界面采用 StyleKit `Apple 风格`：Apple 灰 `#f5f5f7` 背景、白色内容面、Apple 蓝 `#0071e3` 强调色、系统 SF Pro 字体栈、克制圆角和微妙阴影；不使用渐变背景、纸张纹理、植物装饰或外部字体。

媒体源、原始资讯、全部热点、可用图案、提示词和成品图库统一采用卡片懒加载：首批只获取并渲染 24 张，滚动接近底部再从分页 API 请求下一批；轮询数据未变化时保留当前渲染进度，图片同时使用浏览器原生延迟加载和异步解码，避免一次下载或创建上千张卡片。媒体源总数很小，仍一次获取但按相同批次渲染。

`/api/state` 中的任务列表只返回状态、数量和时间等摘要，不再附带每轮完整 Gemini 原始响应；用户点击具体卡片或任务时才通过详情接口读取完整内容，降低首屏与轮询传输量。

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

独立服务不会启动、停止或重建 TrendRadar、Flow 或 Gemini 容器。

TrendRadar 继续独立部署。本地 NewsNow 服务已暂停且不再提供管理页入口；TrendRadar 中文热榜保留使用其 `platforms.api_url` 配置的数据接口。本项目不读 TrendRadar SQLite；外媒原生 RSS 由 TrendRadar 统一采集，再由本项目从 MCP 获取。

### TrendRadar 原生 RSS

[`awesome-rss-feeds`](https://github.com/plenaryapp/awesome-rss-feeds) 可作为选源目录：它按国家和主题提供 OPML，但不应整包导入。目录中包含重复源、播客、YouTube、旧 HTTP 地址和少量无法被标准 XML 解析的条目；同时 TrendRadar MCP 每次最多返回 500 条最新 RSS，源过多会让高频媒体挤占同步窗口。

编辑服务机 `D:\TrendRadar\config\config.yaml` 中已有的 `rss.feeds`，不要再添加第二个顶层 `rss`。下面采用“美国优先、全球补充”：30 个核心源中 24 个是美国媒体或美国市场导向栏目，另外 6 个只补充可能影响美国市场的国际事件。新闻、商业、科技、文化、时尚、设计、科学、体育和网络文化都进入热点池，商品图案适配只在后续图案提取阶段判断。

```yaml
rss:
  enabled: true
  freshness_filter:
    enabled: true
    max_age_days: 2
  feeds:
    # 美国新闻和商业
    - id: "nyt-home"
      name: "The New York Times"
      url: "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"
    - id: "google-news-us"
      name: "Google News US"
      url: "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
    - id: "yahoo-news-most-viewed"
      name: "Yahoo News Most Viewed"
      url: "https://news.yahoo.com/rss/mostviewed"
    - id: "npr-world"
      name: "NPR World"
      url: "https://feeds.npr.org/1004/rss.xml"
    - id: "cnbc-top-news"
      name: "CNBC Top News"
      url: "https://www.cnbc.com/id/100003114/device/rss/rss.html"
    - id: "latimes-world-nation"
      name: "Los Angeles Times World & Nation"
      url: "https://www.latimes.com/world-nation/rss2.0.xml"
    - id: "politico-playbook"
      name: "Politico Playbook"
      url: "https://rss.politico.com/playbook.xml"
    - id: "fortune"
      name: "Fortune"
      url: "https://fortune.com/feed"

    # 美国科技、科学和体育
    - id: "ars-technica"
      name: "Ars Technica"
      url: "https://feeds.arstechnica.com/arstechnica/index"
    - id: "engadget"
      name: "Engadget"
      url: "https://www.engadget.com/rss.xml"
    - id: "the-verge"
      name: "The Verge"
      url: "https://www.theverge.com/rss/index.xml"
    - id: "cnet"
      name: "CNET"
      url: "https://www.cnet.com/rss/news/"
    - id: "wired-science"
      name: "Wired Science"
      url: "https://www.wired.com/feed/category/science/latest/rss"
    - id: "science-daily"
      name: "ScienceDaily"
      url: "https://www.sciencedaily.com/rss/all.xml"
    - id: "espn-top"
      name: "ESPN Top News"
      url: "https://www.espn.com/espn/rss/news"

    # 美国文化、时尚、设计和网络趋势
    - id: "nyt-style"
      name: "The New York Times Style"
      url: "https://rss.nytimes.com/services/xml/rss/nyt/FashionandStyle.xml"
    - id: "elle-fashion"
      name: "ELLE Fashion"
      url: "https://www.elle.com/rss/fashion.xml/"
    - id: "fashionista"
      name: "Fashionista"
      url: "https://fashionista.com/.rss/excerpt/"
    - id: "variety"
      name: "Variety"
      url: "https://variety.com/feed/"
    - id: "pitchfork-news"
      name: "Pitchfork News"
      url: "https://pitchfork.com/rss/news/"
    - id: "petapixel"
      name: "PetaPixel"
      url: "https://petapixel.com/feed/"
    - id: "know-your-meme"
      name: "Know Your Meme"
      url: "https://knowyourmeme.com/newsfeed.rss"
    - id: "design-milk"
      name: "Design Milk"
      url: "https://design-milk.com/category/interior-design/feed/"
    - id: "atlas-obscura"
      name: "Atlas Obscura"
      url: "https://www.atlasobscura.com/feeds/latest"

    # 国际补充
    - id: "bbc-world"
      name: "BBC World"
      url: "https://feeds.bbci.co.uk/news/world/rss.xml"
    - id: "guardian-world"
      name: "The Guardian World"
      url: "https://www.theguardian.com/world/rss"
    - id: "aljazeera"
      name: "Al Jazeera"
      url: "https://www.aljazeera.com/xml/rss/all.xml"
    - id: "dw-english"
      name: "DW English"
      url: "https://rss.dw.com/rdf/rss-en-all"
    - id: "tribunnews-id"
      name: "Tribunnews Indonesia"
      url: "https://www.tribunnews.com/rss"
    - id: "japan-times"
      name: "The Japan Times"
      url: "https://www.japantimes.co.jp/feed/topstories/"
```

扩充时从仓库的 `recommended/with_category/*.opml` 或 `countries/with_category/*.opml` 复制单个 `xmlUrl`，为它补一个稳定且唯一的 `id`；每次只增加 5–10 个源并观察一天。优先保留能持续返回新条目的外媒，删除连续失败、长期不更新或与现有源高度重复的订阅。OPML 是选源资料，TrendRadar 实际配置仍是上面的 YAML 列表。

当前网络无法直连海外站点时，还需修改同一文件的 `advanced` 段。代理运行在 Windows 宿主机时，容器内必须使用 `host.docker.internal`，不能写 `127.0.0.1`：

```yaml
advanced:
  crawler:
    request_interval: 2000
    use_proxy: true
    default_proxy: "http://host.docker.internal:<代理端口>"
  rss:
    request_interval: 1000
    timeout: 30
    use_proxy: true
    proxy_url: "http://host.docker.internal:<代理端口>"
```

修改后执行：

```cmd
cd /d D:\TrendRadar\docker
docker compose restart trendradar trendradar-mcp
docker exec trendradar python manage.py run
docker logs --tail 200 trendradar
curl.exe -X POST http://localhost:5920/api/sources/sync
curl.exe http://localhost:5920/api/sources
```

### TrendRadar 原生 AI 分析与飞书

TrendRadar 的原生 AI 报告只用于人工查看和飞书通知；Social Trend Creative 仍使用自己的 Gemini 流水线生成热点池、图案池和提示词池，两者互不替代。配置采用“美国 RSS 为主、中文热榜补充”，在 `D:\TrendRadar\config\config.yaml` 的现有段落中修改下列字段，不要追加重复的顶层配置：

上述非敏感默认配置已提交到 [`752801828/TrendRadar@822ce07`](https://github.com/752801828/TrendRadar/commit/822ce07)；服务机拉取 `master` 后只需在私有 `docker\.env` 中填写 AI Key 和飞书 Webhook。

```yaml
schedule:
  enabled: true
  preset: "night_owl"

platforms:
  enabled: true

rss:
  enabled: true

display:
  regions:
    hotlist: true
    new_items: false
    rss: true
    standalone: false
    ai_analysis: true

notification:
  enabled: true
  channels:
    feishu:
      webhook_url: ""

ai_analysis:
  enabled: true
  language: "Chinese"
  mode: "follow_report"
  max_news_for_analysis: 150
  include_rss: true
  include_standalone: false
  include_rank_timeline: false

ai_translation:
  enabled: true
  language: "中文"
  scope:
    hotlist: false
    rss: true
    standalone: false
```

`night_owl` 使用 `Asia/Shanghai` 时区：每天 `15:00–17:00` 做一次当前热点分析，`22:00–01:00` 做一次全天汇总；两个窗口均只分析和推送一次，其他时间继续采集但不打扰。TrendRadar 每 30 分钟唤醒一次即可命中上述窗口。

编辑服务机 `D:\TrendRadar\docker\.env`，复用 Social Trend Creative 已在使用的 Gemini2API Key，并填入飞书群自定义机器人的 Webhook。下面的值不得提交 Git：

```env
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/<飞书机器人Webhook>
AI_ANALYSIS_ENABLED=true
AI_API_KEY=<与social-trend-creative的GEMINI_API_KEY相同>
AI_MODEL=openai/gemini-flash
AI_API_BASE=http://host.docker.internal:5918/v1
CRON_SCHEDULE=*/30 * * * *
RUN_MODE=cron
IMMEDIATE_RUN=true
```

TrendRadar 原生飞书渠道只读取 Webhook，不读取 `FEISHU_SIGNING_SECRET`。飞书机器人应使用关键词或 IP 白名单安全策略；如果启用了签名校验，原生推送会失败。多个群的 Webhook 用半角分号 `;` 分隔。

`.env` 变化必须重建容器才能进入环境，单独执行 `restart` 不会更新这些变量。服务机执行：

```cmd
cd /d D:\TrendRadar\docker
docker compose up -d --force-recreate --no-deps --pull never trendradar
docker logs --tail 300 trendradar
```

下一个 `night_owl` 时间窗口会生成 AI 报告并推送飞书。需要立即验证时，可暂时把 `schedule.enabled` 改为 `false`，执行一次 `docker exec trendradar python manage.py run`，确认收到消息后再恢复为 `true`；测试期间仍会使用相同的 RSS、AI 和通知总开关。

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
- `GET /api/signals?limit=24&offset=0&source_id=...`：分页、按日期倒序读取来源条目，可按来源筛选。
- `GET /api/signals/{id}`：读取单个来源条目。
- `GET /api/cards/{pool}?limit=24&offset=0`：分页读取 `acquire`、`trends`、`prompts` 或 `images` 池卡片。
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
