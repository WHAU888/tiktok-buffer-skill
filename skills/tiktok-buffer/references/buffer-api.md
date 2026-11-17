# Buffer API notes (checked 2026-11-15)

Source: developers.buffer.com. Read this only when the script's behaviour is unclear or Buffer returns an unexpected error.

## Basics
- Endpoint: `POST https://api.buffer.com`, body `{"query": ..., "variables": ...}`.
- Auth: `Authorization: Bearer <personal API key>`. Keys come from publish.buffer.com/settings/api and act on the whole account.
- GraphQL usually returns HTTP 200 with an `errors` array (codes: UNAUTHORIZED, FORBIDDEN, NOT_FOUND, UNEXPECTED, RATE_LIMIT_EXCEEDED). Rate limiting returns HTTP 429 with `Retry-After`.

## createPost(input: CreatePostInput!)
- `channelId` (required), `text`, `schedulingType` (`automatic` | `notification`), `mode` (`addToQueue` | `customScheduled` | `shareNext` | `shareNow`), `dueAt` (ISO 8601 UTC, for customScheduled), `saveToDraft`, `assets` (each item has exactly one of `image`, `video`, `document`, each with a public `url`), `metadata.tiktok`.
- `metadata.tiktok`: `isAiGenerated` (video only), `title` (photo posts). **No privacy setting** is exposed — posts use the account's default.
- Video asset may carry `metadata.thumbnailOffset` (ms).
- Response union: `PostActionSuccess { post }` or `MutationError { message }`.

## editPost(input: EditPostInput!)
- Fields: `id` (required), `text`, `assets`, `metadata`, `mode`, `dueAt`, `saveToDraft`, `schedulingType`, plus tags/idea/draft ids.
- Docs say omitted `text`/`assets` are kept, but in practice (2026-11-17) leaving them out fails with "Post must have either text or media / TikTok posts require at least one image or video". Always resend the current text, assets and `metadata.tiktok`. `buffer.py schedule` does this.
- Draft → scheduled: `saveToDraft: false`, `mode: customScheduled`, `dueAt`. Confirmed live.
- Reading a post back: `assets { type source }` (type `image`/`video`) and `metadata { ... on TiktokPostMetadata { title isAiGenerated } }`.
- `PostStatus` values: draft, error, needs_approval, scheduled, sending, sent.
- Also exists: `deletePost`, `movePostInQueue` (not wrapped by the scripts).

## Media
- No upload endpoint. URL must be public, direct, HTTPS, stable until publish (no expiring signed links).
- A Buffer roadmap request says TikTok video is transferred by URL (PULL_FROM_URL). If TikTok posts with video fail with fetch/transfer errors, that's the likely cause.

## Post status
- `post(input:{id}) { status }` works (confirmed live: `sent`). `post_file.py cleanup` relies on it.

## Analytics
- `posts(first, after, input: { organizationId, filter: { status: [sent], channelIds } })` → edges/node, `pageInfo`.
- `post(input: { id })` → `metrics { type name value unit }`, `metricsUpdatedAt`.
- Metric types seen: views, reactions, comments, shares, reposts, impressions, reach, saves, engagementRate.
- Confirmed live for TikTok (2026-11-15): reactions (= likes), comments, engagementRate, views, shares, reach, totalTimeWatched (minutes), averageTimeWatched (seconds). Posts made in the TikTok app also appear here.
- Metrics need a personal API key (not an OAuth app client). Refresh roughly daily.
- `aggregatedPostMetrics(input: { organizationId, startDateTime, endDateTime, channelIds })` exists for totals over a range (≤365 days); not wrapped by the script.

## Limits (free plan)
150 requests / 15 min, 600 / 24 h. One API key.

## Unverified
- Whether TikTok requires video vs. allows photo-only via Buffer for every account.
