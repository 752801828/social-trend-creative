# Social Trend Creative

The project turns worldwide social signals into categorized creative inputs, reusable image prompts, and product renderings.

## Language

**Source entry**:
One immutable article, post, alert, or ranked item collected by TrendRadar from an overseas-media feed, hotlist, or RSSHub route before event clustering.
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
An original, production-safe, product-independent visual direction extracted from a raw trend; one raw trend may yield zero, one, or multiple entries.
_Avoid_: Trend-pool entry, candidate, topic

**Prompt-pool entry**:
A concrete product-rendering prompt deterministically created for a pattern-pool entry; every unprocessed pattern receives one prompt, while image generation randomly consumes prompt-pool entries.
_Avoid_: Visual brief

**Generation task**:
One Flow request for one randomly selected prompt-pool entry and one selected image model.

**Product rendering**:
A realistic image of one physical item with trend-derived artwork printed directly on its usable surface, respecting curvature, seams, folds, material, placement, and scale.

**Prompt-first discovery**:
Asking Gemini Web to research public sources and return raw trends. It is not proof of live browsing; real evidence is retained when available but is not a pipeline gate.

**Feed-backed acquisition**:
Creating raw trends from locally stored TrendRadar source entries, with RSSHub used only to make otherwise unavailable sources subscribable.
_Avoid_: RSS discovery, TrendRadar run

**Priority region**:
A region Gemini should cover first; worldwide acquisition may still return stronger signals from elsewhere.
