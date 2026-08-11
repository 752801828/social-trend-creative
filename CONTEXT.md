# Domain glossary

- **Trend run**: One discovery cycle for a configured time window. A run owns discovery, verification, and optional generation work.
- **Trend candidate**: A social topic returned by Gemini before evidence and freshness checks finish.
- **Verified trend**: A candidate with at least one HTTP(S) evidence URL and a Gemini verification decision that permits manual review.
- **Ready trend**: A verified trend with evidence dated inside the configured time window. Only ready trends may be generated automatically.
- **Needs-review trend**: A verified trend whose evidence time is missing or cannot be parsed. It may be generated only by explicit manual selection.
- **Rejected trend**: A candidate removed because its source, freshness, safety, or verification failed.
- **Generation task**: One Flow request for one trend and one selected image model.
- **Prompt-first discovery**: Asking Gemini Web to research public sources and return evidence. It is not treated as proof of live browsing; evidence is preserved and gated before generation.

