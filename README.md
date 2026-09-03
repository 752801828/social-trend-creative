# Social Trend Creative

独立的全球外媒热点图案与物品图创作服务。TrendRadar 持续汇总配置好的外媒原生 RSS；本项目通过 TrendRadar MCP 保存来源条目、聚合同一事件，再提取安全视觉方向、生成双阶段提示词，先输出独立图案，再用该图案生成产品效果图。

项目不导入或修改 TrendRadar、Gemini2API 或 Flow2API 源码。TrendRadar 通过 Streamable HTTP MCP 连接，Gemini2API 和 Flow2API 通过 HTTP API 连接。

## 七阶段流水线

评分是独立分析支路：获取热点、分类、提示词和生图不等待评分，也不因评分失败而中断。生图只按“提示词池随机抽图案、成功图案池随机抽产品图”的规则执行。

| 阶段 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- |
| ① 外媒采集 | TrendRadar 原生 RSS/可选热榜 | 通过 TrendRadar MCP 幂等同步文章、帖子和预警，不在此阶段筛选商品价值 | 来源条目池 `source_entries` |
| ② 热点聚类 | 查询窗口内全部来源条目 | 标题聚类同一事件，Gemini 分批翻译、分类并标记风险；`candidate_count` 仅表示 AI 批大小 | 全类型原始热点 |
| ③ 图案提取 | 同一任务的全部原始热点 | Gemini 保留事件中的主体、关键动作和场景，优先转译为可识别的原创漫画或编辑插画；抽象纹样、符号和几何元素只做辅助，或在具体画面不安全时替代 | 可用图案池 `trends` |
| ④ 可卖分评估 | 全部原始热点（独立支路） | Gemini 按购买意图、社媒商业热度、搜索增长、商品适配、受众清晰度、窗口寿命、竞争机会七项指标估算 0–100 分，并给出逐项判断理由、等级和风险；评分失败不阻断后续流程，也不控制生图 | 热点销售候选池 `raw_sellability_pool`，并可选映射到后续 `sellability_pool` |
| ⑤ 提示词生成 | 同一任务全部尚未处理的可用图案 | Gemini 为每个方向建立结构化创意标签、独立图案提示词和参考图产品提示词 | 提示词池 `prompt_pool` |
| ⑥ 图案生成 | 按等级配额从提示词池抽取的图案提示词 | Flow 返回后先清理纯色边缘；检测到假透明、棋盘格或不透明边缘时由 `rembg/u2netp` 提取前景，最后用 Pillow 校验 alpha 并保存 `.png` | 图案资产 `pattern_assets` |
| ⑦ 产品图生成 | 尚未达到等级配额的图案资产 | Flow 通过图生图读取实际透明 PNG，把同一图案印到推荐的单一产品上 | 产品图片与记录 `generations` |

当前产品图只生成两个载体：`vehicle spare-tire cover`（备胎罩）和 `phone case`（手机壳）。图案必须自然贴合两种载体的曲面、边缘、开孔和材质，不得像后期贴图；未来扩展商品时再增加产品白名单。

来源和发布时间是事实证据而不是门禁；没有原文链接的热榜条目仍可进入来源池，系统不会伪造来源，也不设置“最多保留 5 条热点”之类的最终上限。聚类阶段不因类型、敏感性、品牌、人物或视觉价值遗漏热点，而是在 `risk_flags` 标记。图案提取必须保留热点可识别的事件锚点；遇到球队、品牌或真人时用无标志、非肖像化角色替代身份，但保留具体动作和环境。只有无法安全、尊重事实且不侵权转译时才不进入图案池。

图案提示词负责生成无产品、无样机的独立图稿，可选择漫画、图标、徽章、符号、抽象纹样或其他适合事件的形式。实际发送给 Flow 前会统一追加硬性输出规范：只保留可印刷图案像素，图案外部和分离图标之间必须透明，禁止铺满画布的天空、地面、房间、景观、摄影环境、海报底板、色块背景、边框和投影；模型确实无法输出透明通道时才允许纯白 `#FFFFFF`。漫画的情节元素必须收在紧凑轮廓或 vignette 内，不能形成矩形背景场景。产品图阶段会把连接图片边缘的纯白区域视为不印刷的透明区，避免白色方块出现在有色商品上，同时保留图案内部封闭的白色细节。产品提示词默认约 140–240 个英文单词，要求把已生成图案作为精确参考图并保留主体、动作、构图、配色和风格，再补充产品、印刷、材质、镜头和灯光。例如职业橄榄球联合训练冲突应表现为两组穿不同颜色、无队标训练服的橄榄球运动员发生推搡或扭打、队友在训练场劝阻，而不是只生成橄榄球、线条和配色。

当前生产模式已配置 `TRENDRADAR_MCP_URL`，新任务的热点只取自 TrendRadar 已采集的原生 RSS，以及开启 `source_include_hotlists` 后的可选热榜；设置中的“优先地区”和“热点来源平台”仅供未配置 TrendRadar 时的旧 Gemini prompt-first 回退模式使用。管理页的来源分布表示实际来源命中的热点数，不表示图片将发布到哪个平台。

## 对应与随机规则

可卖分是 AI 对美国市场商品测试潜力的估算，不是平台真实销量。总分由七项加权指标组成：购买意图 25、社媒商业热度 20、搜索增长 15、商品适配 15、受众清晰度 10、销售窗口寿命 10、竞争机会 5。A（80–100）、B（65–79）、C（60–64）、D（0–59）只表示分析等级，不再改变生图数量；低分热点仍保留，图案和产品图始终从对应池随机抽取。建议用点击、收藏、转化和广告数据复核。

所有新图案都经过实际前景提取：Flow 返回 JPG 或 PNG 后先由 Pillow 清除与画布边缘连通的纯色背景；若边缘透明比例不足（包括把棋盘格画进图片的“假透明”），再由开源项目 [`rembg`](https://github.com/danielgatis/rembg) 的轻量 `u2netp` 模型直接抠图，最后统一重新编码为带 alpha 的 `.png`。仍无透明通道则拒绝保存，不会把 JPG 冒充透明 PNG。模型文件保存在持久化目录 `data/rembg/u2netp.onnx`，避免容器重建后重复下载。

旧任务创建时没有评分记录，所以页面会显示“待评分”。进入 `/sellability` 点击“补算历史评分”会直接处理全部原始热点，即使旧任务尚未形成可用图案方向也能评分；新建任务自动评分，并把热点等级配额映射到其后续图案方向。启动时会修复旧任务中错误为 0 的 `candidate_count`，避免有热点卡片却显示 `0 / 0`。若服务机尚未拉到包含 `raw_sellability_pool` 的提交，评分页面仍会为空。

销售候选页会持续显示“已评分方向 / 全部方向 / 待评分方向”、当前任务 ID、历史任务完成数和进度条；补算结束显示完成状态，异常时直接显示错误原因。评分卡会在每个历史任务完成后逐批出现，不需要等整个历史补算全部结束。

页面中的“评分规则、判断内容与生图配额”可展开查看七项权重、每项具体判断对象、该指标影响销售的理由、通用强弱分档、A–D 分数区间和对应生图数量，并明确总分由服务端汇总、低分不删除热点、AI 估算不能当作真实搜索量或销量。

评分对象是“全部原始热点”，不再要求热点先进入可用图案池。因此只有原始热点、尚未提取图案的历史任务也能补算；进度总数来自各任务 `candidate_count`，评分结果存入 `raw_sellability_pool`。评分请求与分类/提示词请求分开，评分失败不会阻止分类、提示词或生图；图案和产品图始终从各自合格池随机抽取，分数只用于查看与排序。

- 外媒同步独立运行，只更新来源条目池，不创建创作任务。
- 点击“获取热点”会先同步来源，使用查询窗口内全部来源条目建立热点，再在同一个任务中依次完成图案和提示词池。
- 热点聚类时生成的中文标题和中文摘要会写回对应来源条目；“原始资讯”卡片优先展示中文翻译，同时保留英文原标题和详情中的原文摘要。尚未进入热点聚类的新来源显示原文，并在下一次“获取热点”时补齐翻译。
- 提示词阶段不随机抽样，而是为每个尚未处理的视觉方向生成一对图案/产品提示词；重复执行只补齐缺少的条目。
- 图案生成阶段随机抽取提示词，可手动指定 1–30 张；`used_count` 记录图案提示词被消费的次数。
- 产品图阶段只消费已成功生成且尚未达到配额的图案资产，并通过 Flow 图生图传入实际图案；当前产品白名单只有备胎罩和手机壳，产品图保存 `prompt_id` 和 `pattern_asset_id`。
- 销售候选阶段为每个可用图案方向保存七项指标、总分、等级、风险、推荐产品和配额；图案/产品生图只补足该方向尚未达到的等级配额。
- 已形成可用图案池的轮次禁止重新提取，避免级联删除已有提示词和图片。

## 运行方式

“获取热点”固定建立热点、图案方向和提示词三池；“一键完整流水线”继续随机抽取提示词，依次生成独立图案和对应产品图。外媒来源同步默认每 10 分钟、三池默认每 165 分钟（2 小时 45 分）、两步生图默认每 90 分钟（1.5 小时），三个开关均默认关闭。`candidate_count` 是 Gemini 分批处理大小，不再限制最终热点总数。

顶部导航按工作阶段收敛为四个入口，底层数据表和任务关系不合并：

- `/`：总览、统计、自动化设置和任务档案。
- `/sources`：信息采集；组内标签切换“媒体源”和 `/signals`“原始资讯”。
- `/acquire`：AI 创意；组内标签切换“全部热点”、`/trends`“可用图案”和 `/prompts`“生成提示词”。
- `/patterns`：生图工坊中的图案图库；展示独立图案并支持点击放大。
- `/images`：生图工坊中的产品图库；展示引用图案资产生成的产品图。

`/signals`、`/trends`、`/prompts` 继续作为可直接访问的深链接，但不再占用顶部导航。TrendRadar `:8080` 入口移入“信息采集”摘要区；独立“关联服务”栏以及 RSSHub、NewsNow 入口均已移除。

各视图卡片统一按内容创建日期倒序排列，不需要先选择任务；点击卡片会只打开被点击的热点、图案、提示词或图片，并在顶部附带所属任务摘要。页面打开后自动连接同源 API，不再要求输入管理密钥。

界面采用 StyleKit `Apple 风格`：Apple 灰 `#f5f5f7` 背景、白色内容面、Apple 蓝 `#0071e3` 强调色、系统 SF Pro 字体栈、克制圆角和微妙阴影；不使用渐变背景、纸张纹理、植物装饰或外部字体。

媒体源、原始资讯、全部热点、可用图案、提示词、图案图库和产品图库统一采用卡片懒加载：首批只获取并渲染 24 张，滚动接近底部再从分页 API 请求下一批；轮询数据未变化时保留当前渲染进度，图片同时使用浏览器原生延迟加载和异步解码。媒体源总数很小，仍一次获取但按相同批次渲染。

任务运行期间的状态轮询不会重新发起或作废同一内容页尚未完成的卡片请求。SQLite 为各池的 `created_at` 倒序分页、来源 `fetched_at` 统计和来源展示日期建立专用索引；`/api/state` 的平台分布由 SQLite `json_each` 聚合，不再把全部历史趋势载入 Python 逐条解析；全类型热点首批分页也只读取形成当前页所需的任务 JSON。

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
- `GET /api/cards/{pool}?limit=24&offset=0`：分页读取 `acquire`、`trends`、`prompts`、`patterns` 或 `images` 池卡片。
- `POST /api/runs/discover`：新建轮次并依次执行①热点获取、②图案提取、③提示词生成。
- `POST /api/runs/{id}/classify`：对现有轮次执行②可用图案提取；路径名为兼容旧客户端保留。
- `POST /api/runs/{id}/prompts`：执行③补齐全部提示词；为兼容旧客户端仍接受 `count`，但不再随机截断。
- `POST /api/runs/{id}/patterns`：执行⑤随机生成独立图案；JSON 可传 `{"count":3}`。
- `POST /api/runs/{id}/products`：执行⑥从未消费图案生成产品图；JSON 可传 `{"count":3}`。
- `POST /api/runs/{id}/generate`：兼容入口，按⑤→⑥连续执行两步生图。
- `POST /api/runs/full`：新建轮次并连续执行全部六阶段。
- `GET /api/runs/{id}`：全类型原始热点、可用图案池、提示词池、来源和图片详情。
- `POST /api/runs/{id}/cancel`：终止当前任务。
- `DELETE /api/runs/{id}`：删除轮次及本地图片。

任一时刻只允许一个阶段任务运行，冲突返回 HTTP `409`。

## 数据

- `runs`：流水线轮次、原始响应、阶段和统计。
- `source_entries`：TrendRadar 来源条目快照，以规范化 URL/来源身份幂等去重。
- `source_sync_state`：最近同步状态、时间、抓取量和新增量。
- `trends`：AI 提取后的可用图案池；表名为兼容旧数据库保留。
- `prompt_pool`：保存结构化 `creative_tags`、`pattern_prompt`、产品提示词、来源热点、状态和使用次数；标签维度开放取值，便于后续做标签替换变体。
- `pattern_assets`：独立图案的 Flow 请求、图片、模型、耗时和错误。
- `generations`：产品图请求、对应 `prompt_id` 与 `pattern_asset_id`、模型、图片、耗时和错误。
- 图片：`data/assets/<run>/<trend>/`。

启动时会自动创建 `pattern_assets`，为旧 `prompt_pool` 补充 `pattern_prompt`，并为旧 `generations` 补充 `pattern_asset_id`；旧产品图保留且显示为没有关联图案的历史记录。

## 验证

```cmd
python -m unittest discover -s tests -v
python -m compileall -q app
docker compose config --quiet
```
进度栏按“热点”统计，而不是“可用图案方向”：显示已评分热点 / 全部热点 / 待评分热点。原始热点还未进入图案池时，也能正常计算和展示销售候选。
TrendRadar MCP 采集失败时，页面和任务错误会展开 `TaskGroup` 子异常并显示具体工具名（例如 `get_latest_rss`）及连接/协议原因；不再只显示无法定位的 `unhandled errors in a TaskGroup`。

Gemini 的长 JSON 响应会先按严格 JSON 解析，遇到尾逗号、单引号或未加引号键名等常见类 JSON 输出时使用 JSON5 兼容解析；缺逗号、截断等可修复结构再交给 `json-repair` 尝试恢复。分类、评分和提示词等高输出阶段每批最多 5 条，降低模型截断概率；`candidate_count` 仍只控制逻辑批次意图，不代表热点总数。上游 500 会采用递增退避重试。
若分类或提示词批次在重试后仍返回非 JSON，系统会自动拆为更小批次直至单条；只有持续失败的单条被跳过，不再让数百条热点的整轮任务一起失败。提示词缺失项使用本地回退提示词继续后续生图。
MCP 调用默认最多等待 300 秒；若刚完成来源同步，创作任务会复用 10 分钟内的同步结果，避免重复拉取 500 条 RSS。超时会将任务标记为失败并显示具体工具名。
总览页提供“清理 0 热点任务”按钮；它只删除没有可解析热点的空任务，含有热点、提示词、图案或产品图的任务不会受影响。当前卡在采集阶段的空任务会先取消再删除。
