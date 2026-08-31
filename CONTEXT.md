# Social Trend Creative

The project turns worldwide social signals into categorized creative inputs, reusable image prompts, and product renderings.

## Language

**Source entry**:
One immutable article, post, alert, or ranked item collected by TrendRadar from a configured native feed or optional hotlist before event clustering.
_Avoid_: Raw trend, article candidate

**Source cluster**:
One or more source entries grouped as reports of the same real-world event or shared social topic before a pipeline run snapshots it as a raw trend.
_Avoid_: Feed group, source batch

**Pipeline run**:
One shared task lineage through inclusive trend acquisition, pattern-pool extraction, and prompt-pool formation, followed by optional random image generation.
_Avoid_: Trend run, discovery run

**Raw trend**:
One factual event or social topic formed from a source cluster and snapshotted into a pipeline run before commercial or visual suitability is considered; protected, sensitive, and non-visual topics remain present with risk markers.
_Avoid_: Verified trend

**Source sync**:
An idempotent import of recent TrendRadar MCP results into the local source-entry pool; it may add evidence but does not itself create a pipeline run.
_Avoid_: Trend acquisition, pipeline run

**Pattern-pool entry**:
An original, production-safe, product-independent visual translation of a raw trend that preserves a recognizable event anchor: generic subjects, defining action or interaction, and setting. Concrete original comic or editorial scenes are preferred; abstraction supports the narrative or replaces it only when literal depiction would be unsafe. Protected identities are generalized without erasing the event. One raw trend may yield one or multiple entries, and yields none only when no safe, respectful, non-infringing translation is possible.
_Avoid_: Trend-pool entry, candidate, topic

**Prompt-pool entry**:
A paired standalone-artwork prompt and reference-image product prompt deterministically created for a pattern-pool entry. Every unprocessed direction receives one pair; pattern generation randomly consumes entries.
_Avoid_: Visual brief

**Pattern asset**:
A standalone Flow-generated printable artwork—such as an icon, badge, comic, emblem, symbol, abstract repeat, or decorative motif—with transparent pixels outside the design and no product, mockup, full-canvas scene, poster rectangle, frame, backdrop, or cast shadow. Uniform pure white is the only fallback when alpha transparency is technically unavailable. It records its prompt-pool entry.

**Pattern generation task**:
One Flow text-to-image request for one randomly selected prompt-pool entry and one image model.

**Product generation task**:
One Flow image-to-image request that sends a successful pattern asset as a Base64 reference image and renders that same artwork on one physical product.

**Product rendering**:
A realistic image of one physical item with a specific stored pattern asset printed directly on its usable surface, respecting curvature, seams, folds, material, placement, and scale.

**Prompt-first discovery**:
Asking Gemini Web to research public sources and return raw trends. It is not proof of live browsing; real evidence is retained when available but is not a pipeline gate.

**Feed-backed acquisition**:
Creating raw trends from locally stored TrendRadar source entries collected from configured native RSS feeds or optional hotlists.
_Avoid_: RSS discovery, TrendRadar run

**Priority region**:
A region Gemini should cover first; worldwide acquisition may still return stronger signals from elsewhere.
# 2026-08-26 业务补充

- **销售候选（Sellability candidate）**：经过 AI 可卖分估算的原始热点。`raw_sellability_pool` 保存热点级判断；分类后通过 `source_candidate_id` 将配额复制到方向级 `sellability_pool`，仅作美国市场商品测试的决策支持。
- **可卖等级与配额**：A 80–100 分生成 3 个图案、每图案 2 个产品图；B 65–79 分生成 1×2；C 60–64 分生成 1×2；D 0–59 分生成 1×1。低分不删除热点。
- **真实透明 PNG**：Flow 输出先做边缘清理；假透明、棋盘格或边缘仍不透明时由 `rembg/u2netp` 提取前景，再经 Pillow alpha 检测后统一保存 `.png`；`has_transparency=1` 才表示可直接下载。
- **历史评分补算（Sellability backfill）**：为引入销售候选池之前创建的任务补齐评分；新任务自动评分，旧任务必须通过补算入口生成 `sellability_pool` 记录。
- **评分进度（Sellability progress）**：`/api/state.sellability` 中的持久数据计数加进程内任务状态；显示全部、已评分、待评分方向和当前历史任务。服务重启后任务状态回到 idle，但数据库计数仍准确。
- **原始热点评分（Raw sellability score）**：可卖分首先关联 `run_id + candidate_id`，不依赖图案池；`raw_sellability_pool` 是销售候选的事实来源，后续图案方向通过 `source_candidate_id` 继承配额。
- **筛选排序**：热点、销售候选、图案及产品卡片 API 支持关键词、分类、等级和可卖分排序；图案池额外支持透明/未检出筛选，并保持分页懒加载。
页面使用 `total_hotspots`、`scored_hotspots`、`pending_hotspots` 统计原始热点而非后续图案方向；旧 `*_directions` 字段仅为兼容保留。
