# Social Trend Creative

独立的全球社媒热点物品图创作服务。系统先保存配置数量内所有类型的原始社媒热点，再提取安全可用图案、生成完整产品提示词和最终图片；Flow 不直接消费原始热点。

项目不导入或修改 Gemini2API、Flow2API 源码，只通过 OpenAI 兼容 HTTP API 连接。

## 四阶段流水线

| 阶段 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- |
| ① 热点获取 | 全球社媒与优先地区/来源平台设置 | Gemini 在配置数量上限内获取新闻、政治、人物、娱乐、品牌、体育、商业、科技、争议、梗、审美和小众社群等所有类型热点；只记录事实和风险，不按商品或图案价值筛选 | 全类型原始热点 |
| ② 图案提取 | 全类型原始热点 | Gemini 提取安全、原创、可复用且不依赖商标、版权角色或真人肖像的视觉方向；一个热点可以产生零个、一个或多个图案 | 可用图案池 `trends` |
| ③ 提示词生成 | 从可用图案池随机抽取的条目 | Gemini 为每个抽中的图案选择一种适合印刷的产品，并写明图案、位置、比例、印刷处理、产品底色、材质、镜头和灯光 | 提示词池 `prompt_pool` |
| ④ 生图 | 从提示词池随机抽取的提示词 | Flow 把图案直接生成在单一产品上 | 图片与生成记录 `generations` |

可用物品包括杯子、随行杯、手机壳、T 恤、卫衣、帆布袋、抱枕、毯子、备胎罩、贴纸、海报及其他可印刷物品。图案必须自然贴合物品的曲面、褶皱、接缝和材质，不得像后期贴图。

热点来源和发布时间是可选参考，不是采集门禁；系统不会伪造来源，也不再设置“最多保留 5 条热点”之类的最终上限。第一阶段不因类型、敏感性、品牌、人物或视觉价值遗漏热点，而是在 `risk_flags` 标记；第二阶段才排除无法安全原创化、依赖商标、版权角色、真人肖像、仇恨、成人或暴力表达的图案方向。

设置中的“热点来源平台”用于告诉 Gemini 优先从 X、TikTok、Instagram、YouTube、Reddit 等来源发现信号；管理页的来源分布表示各来源命中的热点数，不表示图片将发布到哪个平台。

## 随机抽取规则

- 单独生成提示词池时可通过 `count` 指定随机抽取的可用图案条数；不传时按 `candidate_count` 随机抽取，若图案池不足则使用全部条目。
- 同一轮次可以多次运行提示词生成，每次新增提示词池条目，不覆盖已有提示词和图片。
- 生图只从提示词池随机抽取。`count` 未提供时使用设置项 `images_per_trend` 作为“每轮随机生图数”的兼容字段。
- 提示词池条目可重复使用；`used_count` 记录被生图任务消费的次数，每张图片保存对应 `prompt_id`。
- 已形成可用图案池的轮次禁止重新提取，避免级联删除已有提示词和图片。

## 运行方式

管理页既支持按 ①②③④ 分步执行，也支持“一键完整流水线”。手动完整流水线固定执行全部四阶段；定时任务执行前三阶段，只有打开“定时任务自动生图”时才执行第四阶段。自动调度和定时自动生图默认关闭。

每个模块都有独立页面，并可从顶部模块导航进入：

- `/acquire`：按轮次查看所有类型原始热点、传播原因、来源平台、风险标记和可选证据。
- `/trends`：按轮次查看 AI 提取后的可用图案池和创意方向。
- `/prompts`：按轮次查看完整提示词、状态、使用次数和提示词 ID。
- `/images`：按轮次查看全部生成图片、模型、耗时和关联提示词，图片可点击放大。

模块页面会列出拥有当前阶段或上一阶段数据的轮次，可直接选择轮次查看池内容并触发对应处理。模块间切换复用同一页面会话中的管理密钥，浏览器仍不持久化密钥。

界面采用 StyleKit `Japanese Fresh（日系清新风）` 设计语言：米白纸张底色、天空蓝/薄荷绿/樱花粉点缀、暖灰发丝线、圆润卡片、大留白、植物线稿，以及 Yeseva One + Karla 字体。动效均为缓慢轻量过渡，并遵循 `prefers-reduced-motion`。

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
- `POST /api/runs/discover`：新建轮次，只执行①热点获取。
- `POST /api/runs/{id}/classify`：对现有轮次执行②可用图案提取；路径名为兼容旧客户端保留。
- `POST /api/runs/{id}/prompts`：执行③随机生成提示词池；JSON 可传 `{"count": 5}`，也可传 `{}`。
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
