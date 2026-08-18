# Use TrendRadar MCP as the source boundary

Social Trend Creative consumes overseas-media entries through TrendRadar's Streamable HTTP MCP endpoint and never reads TrendRadar storage directly. TrendRadar owns native RSS collection and feed history, while this project owns idempotent source-entry storage, event clustering, creative pools, and product generation; this keeps TrendRadar upgrades isolated while preserving traceable evidence for every raw trend.
