# What a token costs on a subscription vs the API

**Status: done, with named gaps (§Open).** Prices read 2026-09-20. Measured tokens come from `post-the-seventy-hour-run-2026-09-15.md` (a Max 5× seat, 70 h). The sibling analysis `the-cost-of-a-week.md` (#1971) exists only on `origin/brr/the-cost-of-a-week` (commit bced6648), not on `main`; I used it for context only.

## Table 1 — API list prices (USD per 1M tokens; read 2026-09-20)

Source: https://platform.claude.com/docs/en/about-claude/pricing (cache multipliers: 5-min write 1.25×, 1-h write 2×, read 0.1× — 0.025× on Fable 5.1 / Mythos 5.1). Same numbers on https://claude.com/pricing.

| Model | input | cache write 5m / 1h | cache read | output |
| --- | --- | --- | --- | --- |
| Fable 5.1 | 10 | 12.50 / 20 | **0.25** | 50 |
| Fable 5 | 10 | 12.50 / 20 | 1.00 | 50 |
| Opus 5 (4.5–4.8 same) | 5 | 6.25 / 10 | 0.50 | 25 |
| Sonnet 5 | 2 | 2.50 / 4 | 0.20 | 10 |
| Haiku 4.5 | 1 | 1.25 / 2 | 0.10 | 5 |

OpenAI, https://developers.openai.com/api/docs/pricing (read 2026-09-20; short-context rate; cached input = 10 % of input per the same page):

| Model | input | cached input | output |
| --- | --- | --- | --- |
| GPT-6 Astra | 10 | 1.00 | 50 |
| GPT-5.6 Sol | 4 | 0.40 | 20 |
| GPT-5.6 Terra | 2 | 0.20 | 12 |
| GPT-5.6 Luna | 0.20 | 0.02 | 1.20 |
| GPT-5.5 | 5 | 0.50 | 30 |

Cached values are 10 % × input, computed from the page's stated rule (the page summary did not print a cached column). Third-party trackers list Sol at $5/$30 — a conflict with the official page; official used (see Open).

## Table 2 — the seventy-hour run at API rates

Tokens (seat transcript): output 2.37 M · cache-read 969 M · cache-write 5.7 M · fresh input 0.055 M. Seat model: Fable (which point release is not recorded → both priced). Cache writes priced at the 5-min rate (1-h rate in brackets; the harness TTL is not recorded).

| Model | output | cache read | cache write | fresh in | **Total** |
| --- | --- | --- | --- | --- | --- |
| Fable 5 | 2.37×50 = 118.50 | 969×1.00 = 969.00 | 5.7×12.5 = 71.25 (114.00) | 0.055×10 = 0.55 | **$1,159** ($1,202) |
| Fable 5.1 | 118.50 | 969×0.25 = 242.25 | 71.25 (114.00) | 0.55 | **$433** ($475) |
| Opus 5 (upper bound for "Opus") | 2.37×25 = 59.25 | 969×0.5 = 484.50 | 5.7×6.25 = 35.63 | 0.28 | **$580** |
| Sonnet 5 (lower bound) | 23.70 | 193.80 | 14.25 | 0.11 | **$232** |

Beside it: plan $100/month flat (Max 5×; claude.com/pricing "From $100/month … 5x or 20x more usage than Pro"). Share of the weekly window consumed: 2 % → 42 % = **40 points** in 70 h.
Ratio: the run's API-equivalent value is 2.3×–11.6× the *monthly* plan price, for 40 % of one week.
Caveat: strand runs (Opus/Sonnet/Codex) drew from the same weekly window; their tokens are not in the seat's counts (ledger logs input only, ≥1.63 M), so the 40 points is not all seat.

## Table 3 — implied tokens per week

Measured (Max 5×): 977.1 M seat tokens ÷ 0.40 = **≈2,443 M tokens per 100 % weekly window** (cache-read 2,423 M · output 5.9 M · cache-write 14.3 M · fresh 0.14 M). API-equivalent of that week: Fable 5 ≈ $2,898 · Fable 5.1 ≈ $1,081 · Opus 5 ≈ $1,449 · Sonnet 5 ≈ $580 (Table 2 ÷ 0.40). Because strands shared the 40 points, this is a **lower bound** on capacity per week for this mix.

| Tier | price | implied tokens/week | basis |
| --- | --- | --- | --- |
| Max 5× | $100/mo | ≈2,443 M (this mix) | measured |
| Max 20× | not sourced (see Open) | ≈9,770 M | *documented multiplier* 20× Pro ÷ 5× Pro = 4×, applied linearly — assumption |
| Pro | $20/mo (17 annual) | ≈489 M | *documented multiplier* (Max 5× = 5× Pro), linear — assumption |
| Codex Plus $20 / Pro from $100 | | not measured | vendor documents only messages per 5 h (e.g. Sol Plus 10–100, Pro 5× 50–500, Pro 20× 200–2,000; https://learn.chatgpt.com/docs/pricing) — no token figure |

## The sentence

"On a $100 Max 5× plan, one 70-hour agent run drew 977 M tokens for 40 % of a week — about 2.4 B tokens a week — which at API list price is $232–$1,159 for those 70 hours (Fable 5.1 $433, Opus 5 $580, Fable 5 $1,159) against $100 a month."

Hidden by it: 99 % of the tokens (969 M of 977 M) are cache reads, which the API bills at 10 % of input (2.5 % on Fable 5.1) — so the dollar figure swings 2.7× on one cache-read price line; and a subscription limit is a window that refills, not a bill, so the "equivalent" is a yardstick, not a saving; the 40 points also include strand work not in the token count.

## Open

- Max 20× monthly price ($200 is commonly quoted) — claude.com/pricing fetch showed only "From $100/month". Unsourced; not used.
- Which Fable point release the seat ran (5 vs 5.1); 2.7× price swing. Cache-write TTL (5m vs 1h) unrecorded.
- Anthropic publishes no tokens-per-plan or hours figures (support article 11145838 states none); 5×/20× are relative to Pro only. Linear scaling of the weekly window with the multiplier is my assumption.
- Sol API price conflict: official page $4/$20, aggregators $5/$30. Official used.
- Codex: no token allowance published; credit rates (Sol 100/10/500 credits per 1M) but credit→USD not read. Our Codex measurement (55 % weekly used, 13 strands) has no token count in the sources given.
- API rates are short-context, first-party global; the 1.1× US-residency and batch discounts not applied.
