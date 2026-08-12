# Domain glossary

- **Trend run**: One discovery cycle for a configured time window. A run owns discovery, verification, and optional generation work.
- **Trend candidate**: A social event, meme, phrase, mood, aesthetic, community, seasonal moment, or visual symbol returned by Gemini.
- **Verified trend**: A candidate whose original artwork and recommended physical product have been reviewed by Gemini; evidence remains optional reference data.
- **Ready trend**: A non-duplicate, non-empty, safe visual opportunity that may be generated immediately, with or without evidence.
- **Needs-review trend**: A legacy status retained for existing records; new discovery runs no longer use evidence availability to assign it.
- **Rejected trend**: A candidate removed because it is duplicate, empty, unsafe, or depends on a trademark, copyrighted character, or real-person likeness.
- **Generation task**: One Flow request for one trend and one selected image model.
- **Prompt-first discovery**: Asking Gemini Web to research public sources and return visual-trend opportunities. It is not treated as proof of live browsing; real evidence is preserved when available but is not a generation gate.
- **Product rendering**: A realistic image of one physical item with the trend-derived artwork printed directly on its usable surface, respecting curvature, seams, folds, material, placement, and scale.
- **Priority region**: A configured region Gemini should cover first; worldwide discovery may still return stronger trends from other regions.
