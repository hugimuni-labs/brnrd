# The Reddit read — what people say about the Claude limits change

Read 2026-09-14, 10:30–10:50Z, read-only, with the maintainer's session on
old.reddit.com. Sources: the four searches (`claude limits reduced` ·
`claude max usage limit` · `claude code usage limits september 2026` ·
`anthropic rate limits`), each sorted top/past month, in r/ClaudeAI,
r/ClaudeCode, r/Anthropic and r/LocalLLaMA (16 searches, 799 unique hits),
plus r/ClaudeAI hot. 72 threads were opened with their top comments.
Upvote counts are as read at that time. Nothing was posted, voted or changed.

Screenshots: [`media/reddit/`](../../media/reddit/). They were captured
logged out on www.reddit.com, because old.reddit now shows a login wall to
logged-out readers and a logged-in capture shows the account's handle.

## The short version

Anthropic ran a +50% boost to Claude Code **weekly** limits from May 13 to
September 13, 2026. It extended the end date twice, then said that from
September 14 weekly limits would be 25% above the pre-promotion level. Users
work that out as 150 → 125, a cut of about one sixth. Since about
September 11, many Max 20× users say their meters are draining much faster
than a one-sixth cut explains. That second claim is a bug-or-nerf dispute
with no Anthropic reply in any thread read.

## 1. The claim, as users state it

### The dated sequence (as users posted it)

| date (as named) | what users say happened | source |
| --- | --- | --- |
| 2026-05-13 | Weekly Claude Code limits +50% begins | support article, quoted below |
| 2026-07-19 | @ClaudeDevs: weekly limits stay 50% higher "now through August 19, for all Pro, Max, Team, and seat-based Enterprise users" | quoted tweet visible in [screenshot 2](../../media/reddit/2-anthropic-extends-50-limit-increase.png) |
| 2026-08-18 | @ClaudeDevs extends through Aug 31: "We hope to make this a permanent change to our plans, but strong demand for our models means that capacity may be tight over the coming weeks." | *Anthropic extends 50% limit increase to Aug 31*, u/MagicZhang, r/ClaudeAI, 2026-08-18, 2,060↑ — https://old.reddit.com/r/ClaudeAI/comments/1vrzmx9/anthropic_extends_50_limit_increase_to_aug_31/ |
| 2026-08-27 | Help article then read "From May 13, 2026 through August 31, 2026 … 50% higher" and "After August 31 … return to their standard levels" | *Per Anthropic's own help page, Claude Code weekly limits drop by a third after Monday…*, u/Unable_Strategy5135, r/ClaudeCode, 2026-08-27, 118↑ — https://old.reddit.com/r/ClaudeCode/comments/1w01yew/per_anthropics_own_help_page_claude_code_weekly/ |
| 2026-08-29 | @ClaudeDevs, first tweet deleted and then reposted: "Starting September 14, we're permanently raising standard weekly limits in Claude Code by 25%… Until then, the current 50% increase will be in place." | *so a 1/6th usage cut starting 14th sept*, u/Altruistic-Gift-565, r/ClaudeCode, 2026-08-29, 618↑ — https://old.reddit.com/r/ClaudeCode/comments/1w1qeb7/so_a_16th_usage_cut_starting_14th_sept/ (tweet text quoted by u/plopsaland, 5↑: https://old.reddit.com/r/ClaudeCode/comments/1w1qeb7/so_a_16th_usage_cut_starting_14th_sept/p6na85t/) |
| 2026-09-04 ~20:08Z | Surprise reset of usage meters for Max plans. Pro users say they did not get one. | *Did we just get a reset?*, u/End2EndEncryption, r/ClaudeCode, 528↑ — https://old.reddit.com/r/ClaudeCode/comments/1w7fckf/did_we_just_get_a_reset/ · *We got a limits reset.*, u/cryogen2dev, r/ClaudeAI, 294↑ — https://old.reddit.com/r/ClaudeAI/comments/1w7ffob/we_got_a_limits_reset/ |
| 2026-09-13 | Promotion ends. The meme post "13/05/26 – 13/09/26 Claude extra 50% limit" is the month's top thread. | *Back to normal limit*, u/Low_Roll163, r/ClaudeAI, 2026-09-13, 2,313↑ — https://old.reddit.com/r/ClaudeAI/comments/1wf65up/back_to_normal_limit/ |
| 2026-09-14 | New level live: +25% above pre-promotion | *The limits have been reduced even further now. It's September 14, and it really happened..*, u/AironParsMan, r/ClaudeCode, 2026-09-14, 242↑ — https://old.reddit.com/r/ClaudeCode/comments/1wfwl6k/the_limits_have_been_reduced_even_further_now_its/ |

**The size of the cut, in users' arithmetic:**

> "Yeah to do the math you can just divide 125 / 150, which is 83.33 … which is about a 16.66 … reduction. Or 1/6."
> — u/NoAdsDude, 2026-08-29, 5↑, https://old.reddit.com/r/ClaudeCode/comments/1w1qeb7/so_a_16th_usage_cut_starting_14th_sept/p6n3alv/

> "No, you must be misreading this. They are clearly increasing weekly limits by 25%... ... immediately after decreasing them by one third. Lol"
> — u/cndvcndv, 2026-08-30, 25↑, https://old.reddit.com/r/ClaudeCode/comments/1w1qeb7/so_a_16th_usage_cut_starting_14th_sept/p6pd4ku/

Most titles now say "17%", for example *Since we're losing 17% of our weekly usage today, here's a tip from Lydia on how to save usage!*, u/Current_Ad7104, r/Anthropic, 2026-09-13, 64↑, https://old.reddit.com/r/Anthropic/comments/1wfh3e4/since_were_losing_17_of_our_weekly_usage_today/

### Which plans

The official scope covers Pro, Max, Team and seat-based Enterprise. It applies
to Claude Code only, and only to the weekly limits. The 5-hour limits were not
part of the promotion (see §2). Almost all of the anger comes from **Max 20×**
users. A second grievance runs through the whole month, separate from the
promotion: **the "20×" multiplier applies to the 5-hour window, not the
weekly limit.**

> *Claude Max "20x" only applies to the 5-hour window. Weekly usage on the $200 plan is 2x the $100 plan*
> — u/kupri_94, r/ClaudeCode, 2026-08-31, 1,755↑, https://old.reddit.com/r/ClaudeCode/comments/1w38v98/claude_max_20x_only_applies_to_the_5hour_window/ (links @SataEricUX, 2026-08-30, 1.2M views, [screenshot 3](../../media/reddit/3-max-20x-only-applies-to-5h-window.png))

> "From what I gathered the Max X20 sub doesn't even give you 2x weekly usage but more like 1.7x … So I just got myself two Max X5 subscriptions instead."
> — u/Factor013, 2026-08-31, 191↑, https://old.reddit.com/r/ClaudeCode/comments/1w38v98/claude_max_20x_only_applies_to_the_5hour_window/p6ybfwz/

In r/ClaudeAI, the modbot's AI summary of *Weekly Limit on Pro vs Max* puts
the consensus at "Max 5x plan gives you roughly **3.5 times the weekly usage
of Pro**" (u/Chemical-Visual-7992, 2026-09-01, 169↑,
https://old.reddit.com/r/ClaudeAI/comments/1w465ko/weekly_limit_on_pro_vs_max/).

### The numbers users quote

| plan | number | quote / source |
| --- | --- | --- |
| Max ×20 / ×5 | weekly pool **1,412M vs 628M** "token-value" units (ratio 2.25). Fable is metered at **6.5× Opus on ×20, 4.25× on ×5**. The ×20 Fable cap is only **1.47×** the ×5 cap. Includes the +50% boost ("divide … by 1.5 for post-boost"). In API list dollars, ×20 buys **$7,059/week with no Fable, $4,615 at max Fable**. | *Lifting the Curtain: The Max x5 and Max x20 Usage Limits that Anthropic Refuses to Share*, u/Ohtince, r/ClaudeAI, 2026-08-24, 518↑ — https://old.reddit.com/r/ClaudeAI/comments/1vx0k69/lifting_the_curtain_the_max_x5_and_max_x20_usage/ (n=1 account, fitted from local `~/.claude/projects` transcripts against the meter) |
| Max ×20 | one full 5-hour window = **16.67% of the week ("exactly 1/6") → 20%** since Sep 1: "we went from six windows a week to five" | u/BlockTailor, 2026-09-02, 78↑, in *Since when is one 5 hour window 20% of weekly usage in the 20x max plan?* — https://old.reddit.com/r/ClaudeCode/comments/1w56rc6/since_when_is_one_5_hour_window_20_of_weekly/ |
| Max ×20 | weekly limit gone in **2.5 days**. "a $200/month Max 20 subscription effectively gives me around 12 days of actual usage for the entire month" | *I burned through my Max 20 weekly limit in 2.5 days. What exactly am I paying $200/month for?*, u/redditslutt666, r/Anthropic, 2026-09-09, 407↑ — https://old.reddit.com/r/Anthropic/comments/1wc0lua/i_burned_through_my_max_20_weekly_limit_in_25/ |
| Max ×20 | "an hour into it I've used the whole session 40% of fable and 20% of weekly" | *Did usage change this week? 20x max plan?*, u/No_Consequence4312, r/ClaudeCode, 2026-09-11, 244↑ — https://old.reddit.com/r/ClaudeCode/comments/1wdklro/did_usage_change_this_week_20x_max_plan/ |
| Max ×20 | **183,987 tokens** used the whole 5-hour limit "in less than 19 minutes" | *183,987 tokens used up my entire 5 hour limit. What the fuck, Anthropic.*, u/Guinness, r/ClaudeCode, 2026-09-12, 81↑ — https://old.reddit.com/r/ClaudeCode/comments/1we7x02/183987_tokens_used_up_my_entire_5_hour_limit_what/ (top reply: ultracode sub-agent tokens aren't in the main window's count) |
| Max ×20 | weekly meter jumped from **~7% to ~50%** in a day with minimal use | u/Guilty-Dish-395, r/ClaudeCode, 2026-09-12, 42↑ — https://old.reddit.com/r/ClaudeCode/comments/1weo0w1/max_20x_weekly_usage_suddenly_jumped_from_7_to_50/ |
| Max ×20 | measured at API cost, the allowance dropped **~25% since the reset**, not the 20% expected (26% used after 19.5% of the previous week's API-cost spend). Previous week's API-equivalent spend: Opus 5 $1,793.99, Fable 5.1 $600.13, Sonnet 5 $312.04. | *Lower usage limits kicking in early and large than expected*, u/ForgotMyUserName15, r/ClaudeCode, 2026-09-13, 58↑ — https://old.reddit.com/r/ClaudeCode/comments/1wfb1x8/lower_usage_limits_kicking_in_early_and_large/ |
| Max ×20 | hit the limit, next reset Sep 17. "I added Usage credits for about **$100** but that got washed away like in **30 mins**" | *WTH is going on with Claude Usage Limits*, u/pugazh_is_my_name, r/ClaudeCode, 2026-09-13, 446↑ — https://old.reddit.com/r/ClaudeCode/comments/1wf9uuf/wth_is_going_on_with_claude_usage_limits/ |
| Max ×20 (Fable 5.1) | 5-hour limit at **100% after 58 minutes**, 44% of the Fable weekly limit | *Lol @ 5 hour limit Fable 5.1*, u/WoozieMaddox, r/ClaudeCode, 2026-09-04, 167↑ — https://old.reddit.com/r/ClaudeCode/comments/1w7l0sf/lol_5_hour_limit_fable_51/ |
| Max ×20 | first session of the week "Maxed out in **2.5 hours** and **20%** of the weekly usage" | *Just cancelled my Claude Code Bullshit 20x Plan*, u/BadKoba, r/ClaudeCode, 2026-08-31, 481↑ — https://old.reddit.com/r/ClaudeCode/comments/1w3lw7f/just_cancelled_my_claude_code_bullshit_20x_plan/ |
| Pro | "New Haiku record today: 5h limit reached in **under an hour**" (edit: "On PRO plan") | u/Anxious_Current2593, 2026-09-13, 26↑ — https://old.reddit.com/r/ClaudeCode/comments/1wf9uuf/wth_is_going_on_with_claude_usage_limits/ |
| Pro | previously "**1–2 hours** of continuous, intensive work" on Opus, now much less | *Claude Pro Token Usage Has Increased Dramatically After the Latest Update. Anyone Else?*, u/Critical_Home3535, r/ClaudeAI, 2026-09-07, 250↑ — https://old.reddit.com/r/ClaudeAI/comments/1w9i1iv/claude_pro_token_usage_has_increased_dramatically/ |
| Max ×5 → ×20 | a 2025 TechCrunch figure is quoted back as the only published numbers: "$100-per-month Max plan can expect **140 to 280 hours** of Sonnet 4 and **15 to 35 hours** of Opus 4…" | u/ShelZuuz, 2026-09-01, 32↑ — https://old.reddit.com/r/Anthropic/comments/1w44ei9/is_it_really_true_that_20x_plan_gives_you_roughly/ |
| API | nearly nothing. The searches surface subscription users; API rate-limit (RPM/TPM) complaints are a handful of posts under 30↑. | e.g. *sooo.... usage limits burn extremely fast now, and it costs $40 for two prompts via api*, r/Anthropic, 18↑ |

**The disputed half.** On September 13 and 14, threads split into two camps.
One says the drain since about Sep 11 is too large to be the announced
change ("this is crazy, i thought it is only gonna take 17%",
u/pugazh_is_my_name, above). The other says it is Fable use, bloated
contexts and sub-agents: *I am SO OVER the its draining too quickly posts*,
u/termmonkey, r/ClaudeAI, 2026-09-14, 33↑,
https://old.reddit.com/r/ClaudeAI/comments/1wfv0a5/i_am_so_over_the_its_draining_too_quickly_posts/.
The clearest statement of what nobody can see:

> "'100% in two days' by itself doesn't tell us whether the allowance changed, the accounting changed, a particular workload is burning differently, or there's actually a bug"
> — u/Disastrous-Radio-732, 2026-09-13, 10↑, https://old.reddit.com/r/ClaudeCode/comments/1wfiu04/something_is_seriously_wrong_with_the_limits/

## 2. The official side

**No Anthropic staff reply appears in any of the 72 threads read.** I checked
distinguished posts, staff flair and moderator comments. The only
moderator-marked comments are the r/ClaudeAI modbot's AI summaries and
r/ClaudeCode AutoModerator. What does appear is linked or quoted:

- **Help Center article, linked in 8 of the threads read:**
  https://support.claude.com/en/articles/15910845-claude-code-may-august-2026-weekly-limits-promotion.
  Current wording, as quoted by u/Saylar on 2026-09-14 (10↑,
  https://old.reddit.com/r/ClaudeAI/comments/1wfwuyi/claude_code_mayaugust_2026_weekly_limits_promotion/p9pluq9/)
  and confirmed by fetching the article the same day:
  > "This promotion was a limited-time boost on top of your plan. It ran from May 13 through September 13, 2026. We were planning to return weekly limits to their original levels, but we know many of you found the extra usage helpful. So while the full promotional boost couldn't last, we're making part of it permanent: starting September 14, 2026, **weekly limits in Claude Code are 25% higher than they were before the promotion** for Pro, Max, Team, and seat-based Enterprise plans. We have a lot more in the works around usage, visibility, and control, so stay tuned."

  The fetched page also says the boost applied "to Claude Code only,
  everywhere you use it: the CLI, IDE extensions, desktop, and the web" and
  that it did not affect "5-hour usage limits". On 2026-08-27 it gave
  August 31 as the end date (see §1), so the article has been edited as the
  dates moved.
- **@ClaudeDevs on X:** the 07-19 and 08-18 tweets are visible as images in
  1vrzmx9. The 08-29 tweet (https://x.com/ClaudeDevs/status/2093742321473065266)
  is quoted in 1w1qeb7. Its first version
  (https://x.com/ClaudeDevs/status/2093730620539289689) "got deleted"
  (u/Raffinesse, 97↑).
- **Max plan article**, linked when people argue about 20×:
  https://support.claude.com/en/articles/11049741-what-is-the-max-plan
- **Not official, but posted as news:** a WSJ link, *Anthropic sued over
  limits on its $200-a-month AI plans*, and a CourtListener complaint (N.D.
  Cal.). Both are in *Has Anthropic come out with a statement regarding the
  false advertising of usage limits?*, u/WhoKnowsAtThisPointe, r/ClaudeCode,
  2026-09-09, 87↑,
  https://old.reddit.com/r/ClaudeCode/comments/1wbw3ar/has_anthropic_come_out_with_a_statement_regarding/.
  **I did not verify the suit.**

## 3. The mood and the size

**Size.** Between 2026-08-15 and 09-14, **218 threads** had limit vocabulary
in the title (limit / usage / quota / weekly / 5-hour / reset): r/Anthropic
79, r/ClaudeCode 77, r/ClaudeAI 58, r/LocalLLaMA 4. **Treat 218 as a floor.**
11 of the 16 searches hit Reddit's 100-result cap, and Reddit search is
fuzzy both ways. By week:

| week | threads |
| --- | --- |
| Aug 15–21 (first end date, extension) | 49 |
| Aug 22–28 | 28 |
| Aug 29–Sep 4 (the "25%" tweet, 20× row, Sep 4 reset) | 63 |
| Sep 5–14 (end of promotion; 10 days) | 78 |

**r/ClaudeAI hot, right now:** 3 of the top 10 are about limits: #5 *Back to
normal limit*, #8 *I am SO OVER the its draining too quickly posts*, #9
*Claude Code May–August 2026 weekly limits promotion*. Two slots are pinned;
the rest are showcases and projects.

**Top three by upvotes** (limits-topic threads, last 30 days):

1. **Back to normal limit**: u/Low_Roll163, r/ClaudeAI, 2026-09-13, 2,313↑, 146 comments. https://old.reddit.com/r/ClaudeAI/comments/1wf65up/back_to_normal_limit/ · [screenshot](../../media/reddit/1-back-to-normal-limit.png)
2. **Anthropic extends 50% limit increase to Aug 31**: u/MagicZhang, r/ClaudeAI, 2026-08-18, 2,060↑, 327 comments. https://old.reddit.com/r/ClaudeAI/comments/1vrzmx9/anthropic_extends_50_limit_increase_to_aug_31/ · [screenshot](../../media/reddit/2-anthropic-extends-50-limit-increase.png)
3. **Claude Max "20x" only applies to the 5-hour window. Weekly usage on the $200 plan is 2x the $100 plan**: u/kupri_94, r/ClaudeCode, 2026-08-31, 1,755↑, 223 comments. https://old.reddit.com/r/ClaudeCode/comments/1w38v98/claude_max_20x_only_applies_to_the_5hour_window/ · [screenshot](../../media/reddit/3-max-20x-only-applies-to-5h-window.png)

**Mood.** Angry, and aimed at how the changes are packaged more than at the
limits themselves. The top comment on #1 is sarcasm about the next promotion
("Here's Opus 5.1, with 50% more usage until we feel threatened by OpenAI
again", u/CouldaShoulda_Did, 399↑). On #2, the modbot's summary says the
thread reads the extension as "a desperate panic move to stop users from
canceling". The complaints come with competitors attached: Codex or GPT-6
Astra is named in 58 of the 63 limit threads read.

### Recurring complaints

Counts are rough keyword matches over the 72 threads read, not a census.

| complaint | e.g. |
| --- | --- |
| **No published numbers**, only a percentage meter; "20×" is misleading | *Lifting the Curtain* (518↑); 1w38v98 (1,755↑); u/JonnyJonnerson123: "Why isn't this just written plainly in the docs … '20x the session limit, 2x the weekly limit'" (42↑) |
| **Promotions as a casino**: temporary boosts, then a cut framed as a raise | u/holyknight00: "tired of this gamified bonus and discount strategy … feels like using a fucking casino app instead of a proper programming tool" (102↑, 1w01yew) |
| **Drain without cause**: the meter jumps with no matching work; "shadow nerf" or A/B test theories | 1weo0w1, 1wdklro, 1wfiu04; modbot summary of 1vp5cqt: "Anthropic is quietly reducing limits or A/B testing" |
| **Fable is costly**: the 50% Fable sub-cap and heavy metering of Fable 5.1 | *Anthropic needs to remove the 50% usage limit on Fable 5.1*, u/cephas1784, 162↑ |
| **The 5-hour wall interrupts work** even with weekly quota left | *The 5-hour limit on the $200 Max plan is genuinely frustrating*, u/FoxTheory, r/Anthropic, 2026-09-05, 74↑ — https://old.reddit.com/r/Anthropic/comments/1w89rfw/the_5hour_limit_on_the_200_max_plan_is_genuinely/ |
| **Cancel / downgrade / switch** | roughly 66 cancel/downgrade mentions across 37 threads |

### Recurring workarounds — ⚑ = people describing what brnrd does

- ⚑ **Waiting out the wall, and planning the week around it.**
  "The rest of the time I'm just waiting for the weekly reset" (u/redditslutt666, 1wc0lua, 407↑).
  "I am always just waiting for limits to be reset" (u/1Poochh, 50↑, https://old.reddit.com/r/ClaudeAI/comments/1w465ko/weekly_limit_on_pro_vs_max/p757b5i/).
  Holding back quota: "I'm not going to burn through the tiny amount of usage I have left because I know I'll need it later" (1wc0lua).
- ⚑ **Scheduling around the 5-hour window.** *Have a cronjob every morning 3
  hours before you usually start work pinging claude code to have a quicker
  session reset.*, u/False-Positive21, r/ClaudeAI, 2026-08-15, 96↑,
  https://old.reddit.com/r/ClaudeAI/comments/1vp9q4f/have_a_cronjob_every_morning_3_hours_before_you/.
  **Relevant to us:** the OP later edits in the ToS clause against accessing
  the Services "through automated or non-human means, whether through a bot,
  script, or otherwise". The modbot summary calls the trick "a great way to
  potentially get your account banned". One reply says it can now be
  scheduled from the claude.ai website itself (u/OlorinDK, 14↑).
- ⚑ **Cheaper models for chores, the strong model as orchestrator.** This is
  the most common advice (about 92 mentions in 32 threads).
  *How I use sub-agents without burning through Fable 5.1*, u/Smbridges91, r/ClaudeCode, 2026-09-09, 382↑ — "**Fable is my orchestrator, not my worker.**" Haiku as scout, Sonnet as builder, Opus as refuter. https://old.reddit.com/r/ClaudeCode/comments/1wbc03f/how_i_use_subagents_without_burning_through_fable/
  *Tired of people complaining about usage. Here's a guide to conserve usage*, u/Emotional-Bus-7065, r/ClaudeCode, 2026-09-08, 350↑ — "Fable should NEVER touch any part of the code… delegate that to models with higher usage limits." https://old.reddit.com/r/ClaudeCode/comments/1wa9aya/tired_of_people_complaining_about_usage_heres_a/
  In the same sub-agents thread, u/sisif_ (69↑) describes a loop close to
  brnrd's: "Fable … writes the spec and files the ticket … the loop mints a
  branch, a worktree and a seat. An ephemeral Opus hand boots in that
  worktree, reads the spec, implements, runs the suite in its tree, commits
  to its branch, marks the ticket DONE with a short report."
- ⚑ **Handoffs instead of re-reading context.** *How I got my Mac to read my
  Claude Code chats at night and extend my token usage by 1/3rd*,
  u/Unable_Strategy5135, r/ClaudeAI, 2026-08-27, 161↑: a local model writes
  the handoff overnight "instead of the expensive model re-reading
  everything at full price".
  https://old.reddit.com/r/ClaudeAI/comments/1w06a7b/how_i_got_my_mac_to_read_my_claude_code_chats_at/.
  Also u/serj88: "auto compact off · statusline that shows your context occupation and cache age · managed handoff between session on a warm cache, no compaction" (13↑, https://old.reddit.com/r/ClaudeCode/comments/1w9wf7z/claude_just_compacted_my_session_and_took_me_from/p8dy4tk/).
- ⚑ **Staying on the subscription, not paying per token.** "Never buy usage
  credits, that one goes 20x faster" (u/Wooden_Drag9473, 63↑, 1wf9uuf). The
  *Lifting the Curtain* post values a ×20 week at $4,615–$7,059 at API list
  prices.
- ⚑ **Wanting measurement.** Several users build their own: a meter-fitting
  harness (*Lifting the Curtain*), ccusage comparisons, and *I built a pixel
  pet that eats your Claude Code tokens (and warns you before the 5h wall)*
  (r/ClaudeCode, 2026-08-25, 104↑). "I really wish we had better usage
  telemetry" (u/Disastrous-Radio-732, above).
- **Splitting plans or providers.** Two Max ×5 accounts instead of one ×20
  (u/Factor013, 191↑), or $100 Anthropic plus $100 OpenAI: "Way more usage,
  resets all the time, and the different models fill in each other's gaps
  well" (u/sermer48, 10↑, 1w89rfw). Several accounts per person is also
  common (*Using multiple Pro accounts to bypass Claude Code rate limits— is
  it against the ToS?*, r/Anthropic, 96↑).
- **Leaving.** Codex / GPT-6 Astra is the named alternative in most
  complaint threads. r/LocalLLaMA is barely in this conversation (4 limit
  threads).

## 4. What they'd read from us

Existing threads where a chart of our **measured cost per merged PR on the
same subscription** would be on-topic rather than spam. This only holds if
the chart shows measured before/after figures across September 14 and names
the plan.

1. *Lifting the Curtain: The Max x5 and Max x20 Usage Limits that Anthropic Refuses to Share* (r/ClaudeAI)
2. *Lower usage limits kicking in early and large than expected* (r/ClaudeCode)
3. *Something is seriously wrong with the limits* (r/ClaudeCode)

## Method notes

- JSON listings (`search.json`, `comments/<id>.json`) were read through the
  same authenticated browser context as the HTML pages. Up to 200 top
  comments were read per thread, 4 levels deep.
- "Last 30 days" means created on or after 2026-08-15T00:00Z.
- Scores change by the hour. Treat every ↑ as a reading taken at 10:40Z on
  2026-09-14.
- Thread and comment authors are quoted by their public Reddit handles. The
  reading account appears nowhere in this page or the screenshots.
