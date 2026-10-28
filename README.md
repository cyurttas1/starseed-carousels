# Starseed Transmission carousels

Slides and the Instagram publisher for the daily Starseed carousel. `days/<date>/` holds six JPEG slides and
`post.json`; `.github/workflows/instagram.yml` publishes each day at 19:00 TR through the Instagram Graph API.
State of every day: `state/<date>.json`.

First comments (book / store link): `.github/workflows/comments.yml` runs `scripts/publish.py --comments` on every push that touches
`videos/`, `promos/` or `days/*/post.json`, plus hourly as a safety net. Facebook accepts a comment on a still-scheduled post, so the
comment is attached before the slot and re-checked after it; the same text is never posted twice under one post.
New video: add `videos/<date>.json`; new store promo: add it to `promos/promos.json`; push.

Carousels are Instagram-only (owner, 2026-10-28): no Facebook post and no Facebook first comment; `post.json` must not carry `fb_post`.
