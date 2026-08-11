# Social Trend Creative

独立的海外社媒商品热点创作服务：Gemini发现适合选品、商品策划和营销创意的热点，Flow直接生成原创商品视觉。项目不导入或修改 Flow2API、Gemini2API 源码，只通过HTTP API连接。

## 当前工作流

1. 每日定时或手动启动商品热点发现。
2. Gemini按最近时间窗口从所选来源平台寻找产品、品类、消费需求、审美、使用场景和营销机会。
3. 第二次Gemini请求完善商品角度和商业视觉方向，仅排除重复、空内容或明确不安全的候选。
4. 证据URL和发布时间作为可选参考保存；缺失、无法解析或过期都不阻止生图，也不再设置“最终最多保留数”。
5. 管理页主流程会为全部 `ready` 候选直接调用Flow生图；定时任务是否自动生图仍由独立开关控制。
6. 独立SQLite保存轮次、热点、证据、提示词、图片、耗时和错误。

> Prompt-first discovery 不能证明Gemini一定执行了实时搜索。系统不会伪造来源；来源缺失不影响商品创意生图。自动调度默认关闭，上线前应先人工验证数轮。

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
- `POST /api/runs/full`：发现商品热点，并为全部可用候选直接生图。
- `GET /api/runs/{id}`：轮次、热点、来源和图片详情。
- `POST /api/runs/{id}/generate`：为人工选择的热点生图。
- `POST /api/runs/{id}/cancel`：终止当前轮次。
- `DELETE /api/runs/{id}`：删除轮次及本地图片。

## 验证

```cmd
python -m unittest discover -s tests -v
```
