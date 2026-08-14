# Social Trend Creative

The project turns worldwide social signals into categorized creative inputs, reusable image prompts, and product renderings.

## Language

**Pipeline run**:
One pass through inclusive trend acquisition, pattern-pool extraction, prompt-pool formation, and optional image generation.
_Avoid_: Trend run, discovery run

**Raw trend**:
Any factual social signal returned by worldwide acquisition before commercial or visual suitability is considered; protected, sensitive, and non-visual topics remain present with risk markers.
_Avoid_: Verified trend

**Pattern-pool entry**:
An original, production-safe, product-independent visual direction extracted from a raw trend; one raw trend may yield zero, one, or multiple entries.
_Avoid_: Trend-pool entry, candidate, topic

**Prompt-pool entry**:
A concrete product-rendering prompt derived from a randomly selected pattern-pool entry; image generation consumes this entry, never a raw trend directly.
_Avoid_: Visual brief

**Generation task**:
One Flow request for one randomly selected prompt-pool entry and one selected image model.

**Product rendering**:
A realistic image of one physical item with trend-derived artwork printed directly on its usable surface, respecting curvature, seams, folds, material, placement, and scale.

**Prompt-first discovery**:
Asking Gemini Web to research public sources and return raw trends. It is not proof of live browsing; real evidence is retained when available but is not a pipeline gate.

**Priority region**:
A region Gemini should cover first; worldwide acquisition may still return stronger signals from elsewhere.
