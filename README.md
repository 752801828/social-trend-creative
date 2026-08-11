# Social Trend Creative

独立的海外社媒热点创作服务：Gemini发现和核验热点，人工确认后由Flow生成原创编辑视觉。项目不导入或修改 Flow2API、Gemini2API 源码，只通过HTTP API连接。

## 当前工作流

1. 每日定时或手动启动热点发现。
2. Gemini按最近时间窗口查询 X、TikTok、Instagram、YouTube、Reddit 等公开信息。
3. 第二次Gemini请求核验候选、来源、时效和视觉价值。
4. 本地门禁检查HTTP(S)来源和证据发布时间：
   - `ready`：至少一条证据时间位于窗口内；允许自动或人工生图。
   - `needs_review`：来源URL存在但时间缺失；只允许人工勾选生图。
   - `rejected`：无有效来源、全部过期或核验拒绝；禁止生图。
5. Flow按所选热点、模型和数量并发生图。
6. 独立SQLite保存轮次、热点、证据、提示词、图片、耗时和错误。

> Prompt-first discovery 不能证明Gemini一定执行了实时搜索。本服务把来源和发布时间作为生图门禁，并默认关闭自动调度与自动生图。上线前应先人工验证数轮。

## 本地启动

```cmd
cd /d C:\Users\WY001\Documents\反代\social-trend-creative
copy .env.example .env
notepad .env
docker compose up -d --build
curl http://localhost:5920/health
```

打开：`http://localhost:5920/`

`.env` 至少配置：

- `ADMIN_KEY`：独立管理页面密钥。
- `GEMINI_BASE_URL` / `GEMINI_API_KEY`。
- `FLOW_BASE_URL` / `FLOW_API_KEY`。
- `FEISHU_WEBHOOK_URL` / `FEISHU_SIGNING_SECRET`：可选。
- `PUBLIC_BASE_URL`：可选；配置后飞书通知可附生成图片链接。

Docker Desktop中使用宿主机服务时，默认地址为：

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

除 `/`、`/health` 和生成图片外，管理API要求：

```http
Authorization: Bearer <ADMIN_KEY>
```

- `GET /api/state`：配置、连接、统计和轮次。
- `PUT /api/config`：更新每日策略。
- `POST /api/connections/test`：测试Gemini/Flow连接。
- `POST /api/runs/discover`：只发现和核验热点。
- `POST /api/runs/full`：按配置执行完整一轮。
- `GET /api/runs/{id}`：轮次、热点、来源和图片详情。
- `POST /api/runs/{id}/generate`：为人工选择的热点生图。
- `POST /api/runs/{id}/cancel`：终止当前轮次。
- `DELETE /api/runs/{id}`：删除轮次及本地图片。

## 验证

```cmd
python -m unittest discover -s tests -v
```

