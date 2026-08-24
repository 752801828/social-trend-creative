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
