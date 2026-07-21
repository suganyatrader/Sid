---
name: "news-open-csv"
description: "Run NSE post-close equity analysis and produce a ranked intraday BUY watchlist in CSV, saved to data with today date by default."
argument-hint: "Optional: output path or extra constraints (example: data/news_rankings_2026-07-21.csv)"
agent: "news-analyst"
---
Analyze the latest NSE post-close files in data/nse_postclose_downloads and produce the ranked BUY watchlist for market open.

Requirements:
- Focus only on NSE-listed equities.
- Ignore non-equity notices.
- Prefer latest files and ignore stale duplicates.
- Return CSV with exact header:
Symbol,Rank,Reason,SentimentLabel,SentimentScore,ConfidenceLabel,ConfidenceScore

File output rule:
- If the user provided an output path in the prompt arguments, write CSV there.
- Otherwise write CSV to data/news_rankings_<current-date>.csv using YYYY-MM-DD.
