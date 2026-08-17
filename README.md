# Social Trend Creative

独立的全球社媒热点物品图创作服务。系统先保存配置数量内所有类型的原始社媒热点，再提取安全可用图案、生成完整产品提示词和最终图片；Flow 不直接消费原始热点。

项目不导入或修改 Gemini2API、Flow2API 源码，只通过 OpenAI 兼容 HTTP API 连接。

## 四阶段流水线

| 阶段 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- |
| ① 热点获取 | 全球社媒与优先地区/来源平台设置 | Gemini 在配置数量上限内获取新闻、政治、人物、娱乐、品牌、体育、商业、科技、争议、梗、审美和小众社群等所有类型热点；只记录事实和风险，不按商品或图案价值筛选 | 全类型原始热点 |
| ② 图案提取 | 全类型原始热点 | Gemini 提取安全、原创、可复用且不依赖商标、版权角色或真人肖像的视觉方向；一个热点可以产生零个、一个或多个图案 | 可用图案池 `trends` |
| ③ 提示词生成 | 同一轮次全部尚未处理的可用图案 | Gemini 为每个图案建立一条对应产品提示词，写明图案、位置、比例、印刷处理、产品底色、材质、镜头和灯光 | 提示词池 `prompt_pool` |
| ④ 生图 | 从提示词池随机抽取的提示词 | Flow 把图案直接生成在单一产品上 | 图片与生成记录 `generations` |

可用物品包括杯子、随行杯、手机壳、T 恤、卫衣、帆布袋、抱枕、毯子、备胎罩、贴纸、海报及其他可印刷物品。图案必须自然贴合物品的曲面、褶皱、接缝和材质，不得像后期贴图。

热点来源和发布时间是可选参考，不是采集门禁；系统不会伪造来源，也不再设置“最多保留 5 条热点”之类的最终上限。第一阶段不因类型、敏感性、品牌、人物或视觉价值遗漏热点，而是在 `risk_flags` 标记；第二阶段才排除无法安全原创化、依赖商标、版权角色、真人肖像、仇恨、成人或暴力表达的图案方向。

设置中的“热点来源平台”用于告诉 Gemini 优先从 X、TikTok、Instagram、YouTube、Reddit 等来源发现信号；管理页的来源分布表示各来源命中的热点数，不表示图片将发布到哪个平台。

## 对应与随机规则

- 热点获取、图案提取和提示词生成属于同一个流水线轮次；点击“获取热点”会依次完成前三阶段。
- 提示词阶段不再随机抽样，而是为该轮次每个尚未处理的可用图案生成一条提示词；重复执行只补齐缺少的提示词。
- 只有生图阶段随机抽取提示词。手动生图可在任务详情中指定 1–30 张；未提供数量时使用设置项 `images_per_trend`，默认 5。
- 提示词池条目可重复使用；`used_count` 记录被生图任务消费的次数，每张图片保存对应 `prompt_id`。
- 已形成可用图案池的轮次禁止重新提取，避免级联删除已有提示词和图片。

## 运行方式

“获取热点”固定在同一任务中执行前三阶段；“一键完整流水线”继续执行到第四阶段。自动化设置使用独立间隔：前三池默认每 165 分钟（2 小时 45 分）运行一次，随机生图默认每 90 分钟（1.5 小时）运行一次；两个开关默认关闭，自动生图会选择最新可用提示词任务。

每个模块都有独立页面，并可从顶部模块导航进入：

- `/acquire`：直接展示所有任务的原始热点卡片。
- `/trends`：直接展示所有任务的可用图案卡片。
- `/prompts`：直接展示所有任务的完整提示词卡片。
- `/images`：直接展示全部生成图片，图片可点击放大。

各池卡片统一按内容创建日期倒序排列，不需要先选择任务；点击卡片会只打开被点击的热点、图案、提示词或图片，并在顶部附带所属任务摘要。模块间切换复用同一页面会话中的管理密钥，浏览器仍不持久化密钥。

界面沿用 StyleKit `Japanese Fresh（日系清新风）`，但将页面底色加深为灰绿色纸张、提高文字和边框对比度，并为内容卡片增加清晰阴影，避免大面积纯白难以辨认。

Prompt-first discovery 不能证明 Gemini 一定执行了实时联网搜索。上线前应先人工验证数轮结果。

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

- `ADMIN_KEY`：独立管理页面密钥。
- `GEMINI_BASE_URL` / `GEMINI_API_KEY`。
- `FLOW_BASE_URL` / `FLOW_API_KEY`。
- `FEISHU_WEBHOOK_URL` / `FEISHU_SIGNING_SECRET`：可选。
- `PUBLIC_BASE_URL`：可选；配置后飞书通知可附生成图片链接。

Docker Desktop 中使用宿主机服务时，默认地址为：

```text
http://host.docker.internal:5918
http://host.docker.internal:38000
```

## 服务机部署

将整个目录复制到 `D:\social-trend-creative`，准备 `.env` 后运行：

```cmd
cd /d D:\social-trend-creative
docker compose up -d --build
docker compose ps
curl http://localhost:5920/health
```

独立服务不会启动、停止或重建 Flow/Gemini 容器。

## API

除 `/`、`/health` 和生成图片外，管理 API 要求：

```http
Authorization: Bearer <ADMIN_KEY>
```

- `GET /api/state`：配置、连接、统计和轮次。
- `PUT /api/config`：更新每日策略。
- `POST /api/connections/test`：测试 Gemini/Flow 连接。
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
- `trends`：AI 提取后的可用图案池；表名为兼容旧数据库保留。
- `prompt_pool`：可复用提示词、来源热点、状态和使用次数。
- `generations`：Flow 请求、对应 `prompt_id`、模型、图片、耗时和错误。
- 图片：`data/assets/<run>/<trend>/`。

启动时会自动创建 `prompt_pool`，并为旧 `generations` 表补充 `prompt_id`，无需手工迁移旧 SQLite。

## 验证

```cmd
python -m unittest discover -s tests -v
python -m compileall -q app
```
