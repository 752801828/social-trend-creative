# Use TrendRadar MCP as the source boundary

Social Trend Creative consumes overseas-media entries through TrendRadar's Streamable HTTP MCP endpoint and never reads TrendRadar or RSSHub storage directly. TrendRadar owns collection and feed history, RSSHub only adapts non-RSS sources, and this project owns idempotent source-entry storage, event clustering, creative pools, and product generation; this keeps third-party upgrades isolated while preserving traceable evidence for every raw trend.
