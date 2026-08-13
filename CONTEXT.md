# Social Trend Creative

The project turns worldwide social signals into categorized creative inputs, reusable image prompts, and product renderings.

## Language

**Pipeline run**:
One pass through acquisition, trend-pool formation, prompt-pool formation, and optional image generation.
_Avoid_: Trend run, discovery run

**Raw trend**:
An unclassified social signal returned by worldwide acquisition before AI splitting and categorization.
_Avoid_: Verified trend

**Trend-pool entry**:
An independently usable, AI-split and categorized creative angle derived from one or more raw trends.
_Avoid_: Candidate, topic

**Prompt-pool entry**:
A concrete product-rendering prompt derived from a randomly selected trend-pool entry; image generation consumes this entry, never a trend directly.
_Avoid_: Visual brief

**Generation task**:
One Flow request for one randomly selected prompt-pool entry and one selected image model.

**Product rendering**:
A realistic image of one physical item with trend-derived artwork printed directly on its usable surface, respecting curvature, seams, folds, material, placement, and scale.

**Prompt-first discovery**:
Asking Gemini Web to research public sources and return raw trends. It is not proof of live browsing; real evidence is retained when available but is not a pipeline gate.

**Priority region**:
A region Gemini should cover first; worldwide acquisition may still return stronger signals from elsewhere.
