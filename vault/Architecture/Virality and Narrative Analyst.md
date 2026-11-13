# Virality and Narrative Analyst

Upward link: [[Home]] · Architecture: [[Multi-Agent Research Team]]

Implemented as `ViralityNarrativeAnalyst` and `normalize_media_items` in [`research_team.py`](../../src/meme_flight_recorder/research_team.py), composing [`media_feeds.py`](../../src/meme_flight_recorder/providers/media_feeds.py), [`social_intelligence.py`](../../src/meme_flight_recorder/social_intelligence.py), [`narratives.py`](../../src/meme_flight_recorder/narratives.py), and [`sources.py`](../../src/meme_flight_recorder/sources.py).

## Adversarial Social Rules

1. **Literal-Mint Identity**: `mint` is populated **only** when a base58 Solana address appears verbatim in the post text. Cashtags (`$CATE`, `$HAROLD`) are stored separately in `symbols` because five distinct CATE mints appeared in a single night. See [[Fail-Closed Engineering Rules]].
2. **Relay Deduplication**: `SocialEvent.dedup_key` anchors on `forwarded_from or author`, preventing a single post relayed across dozens of Telegram channels from inflating author counts.
3. **Spam Retained, Not Deleted**: `spam_probability` scores promotional copy (`100x`, `guaranteed`, ticker-shotgunning, single-author flooding) and keeps events so `spam_share` remains measurable.
4. **Point-in-Time Features**: `compute_features(..., as_of=as_of)` strictly excludes events after `as_of`, preserving zero-lookahead integrity for replay.
5. **Advisory-Only Contract**: `ViralityReport.advisory_only` is permanently `True`. Per [[Promotion Policy and Evidence Gate]], media velocity (`M1`) must pass its pre-registered held-out study before gaining any gating weight.

## Free Public Feed Coverage
- **Reddit** (`/.rss` Atom feed rotating one subreddit per ~20-min pass because `.json` 403-blocks datacenter IPs).
- **Telegram Public Previews** (`t.me/s/<channel>`, operator-curated public channels only).
- **Google News RSS** (~100 headlines/query).

## Related Notes
- [[Study C - Discovery Coverage]]
- [[Adversarial On-Chain Investigator]]
- [[Promotion Policy and Evidence Gate]]
