# Social Trend Creative

独立的全球社媒热点物品图创作服务。系统把原始社媒信号、可用创意角度、完整生图提示词和最终图片分别保存，Flow 不再直接消费热点记录。

项目不导入或修改 Gemini2API、Flow2API 源码，只通过 OpenAI 兼容 HTTP API 连接。

## 四阶段流水线

| 阶段 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- |
| ① 热点获取 | 全球社媒与优先地区/来源平台设置 | Gemini 获取最近时间窗口中的事件、梗、情绪、审美、社群、季节节点和视觉符号；此时不选物品、不写生图提示词 | 原始热点 |
| ② AI 拆分分类 | 原始热点 | Gemini 把宽泛热点拆成可独立使用的创意角度、合并重复项并分类；一个原始热点可以拆出多个条目 | 热点池 `trends` |
| ③ 提示词生成 | 从热点池随机抽取的条目 | Gemini 为每个抽中的条目选择一种适合印刷的物品，并写明图案、位置、比例、印刷处理、物品底色、材质、镜头和灯光 | 提示词池 `prompt_pool` |
| ④ 生图 | 从提示词池随机抽取的提示词 | Flow 按提示词生成单一物品效果图 | 图片与生成记录 `generations` |

可用物品包括杯子、随行杯、手机壳、T 恤、卫衣、帆布袋、抱枕、毯子、备胎罩、贴纸、海报及其他可印刷物品。图案必须自然贴合物品的曲面、褶皱、接缝和材质，不得像后期贴图。

热点来源和发布时间是可选参考，不是分类、提示词或生图门禁；系统不会伪造来源，也不再设置“最多保留 5 条热点”之类的最终上限。重复、空内容、明确不安全、依赖商标、版权角色或真人肖像的方向仍会排除。

设置中的“热点来源平台”用于告诉 Gemini 优先从 X、TikTok、Instagram、YouTube、Reddit 等来源发现信号；管理页的来源分布表示各来源命中的热点数，不表示图片将发布到哪个平台。

## 随机抽取规则

- 单独生成提示词池时可通过 `count` 指定随机抽取的热点池条数；不传时按 `candidate_count` 随机抽取，若热点池不足则使用全部条目。
- 同一轮次可以多次运行提示词生成，每次新增提示词池条目，不覆盖已有提示词和图片。
- 生图只从提示词池随机抽取。`count` 未提供时使用设置项 `images_per_trend` 作为“每轮随机生图数”的兼容字段。
- 提示词池条目可重复使用；`used_count` 记录被生图任务消费的次数，每张图片保存对应 `prompt_id`。
- 已形成热点池的轮次禁止重新分类，避免级联删除已有提示词和图片。

## 运行方式

管理页既支持按 ①②③④ 分步执行，也支持“一键完整流水线”。手动完整流水线固定执行全部四阶段；定时任务执行前三阶段，只有打开“定时任务自动生图”时才执行第四阶段。自动调度和定时自动生图默认关闭。

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
- `POST /api/runs/{id}/classify`：对现有轮次执行②AI 拆分分类。
- `POST /api/runs/{id}/prompts`：执行③随机生成提示词池；JSON 可传 `{"count": 5}`，也可传 `{}`。
- `POST /api/runs/{id}/generate`：执行④从提示词池随机生图；JSON 可传 `{"count": 3}`，也可传 `{}`。
- `POST /api/runs/full`：新建轮次并连续执行①②③④。
- `GET /api/runs/{id}`：原始热点、热点池、提示词池、来源和图片详情。
- `POST /api/runs/{id}/cancel`：终止当前任务。
- `DELETE /api/runs/{id}`：删除轮次及本地图片。

任一时刻只允许一个阶段任务运行，冲突返回 HTTP `409`。

## 数据

- `runs`：流水线轮次、原始响应、阶段和统计。
- `trends`：AI 拆分分类后的热点池。
- `prompt_pool`：可复用提示词、来源热点、状态和使用次数。
- `generations`：Flow 请求、对应 `prompt_id`、模型、图片、耗时和错误。
- 图片：`data/assets/<run>/<trend>/`。

启动时会自动创建 `prompt_pool`，并为旧 `generations` 表补充 `prompt_id`，无需手工迁移旧 SQLite。

## 验证

```cmd
python -m unittest discover -s tests -v
python -m compileall -q app
```
