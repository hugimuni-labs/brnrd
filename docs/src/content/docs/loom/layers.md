# Loom processes by layer

This page inventories every process the loom runs, with its layer, rise rule and sink rule.
The loom has no central self in its architecture; it organises work across one gradient of reach (in focus, at the edge, below) with two movements: rising (a rule puts one line at the edge) and sinking (a repeated act becomes a script, a note archives, a lesson retires, an attention runs out).

## Table

| Process | Layer | Rise rule | Sink rule |
|---|---|---|---|
| jack events (start, pre tool, post tool, stop) | at the edge | jack runs on every tool boundary; tooltrace.py appends start/end lines to port/tools.jsonl on pre/post tool hooks | tooltrace line pairs close on post hook; start line without end marks a stuck tool |
| boundary generation | in focus | loom.py tick_once builds boundary via render_boundary in port.py:307 | boundary replaced on next tick; lines age out via _ageless in jack.py:110 |
| body line | at the edge | bodies.py state_of determines body presence; port.py:316 passes body state to render_boundary | body exits or degrades removes the line |
| degraded line | at the edge | health.py:140 degraded_line returns non-empty when standing episodes exist (loom.degraded facts without loom.recovered) | episode clears after CLEAR_AFTER seconds (health.py:32) or on loom.recovered fact |
| asks line | at the edge | pull.py:51 band_line returns open asks for a thread | ask closed or settled removes it from band |
| opened line | at the edge | loom.py:376-399 children lines added for each opened thread with moving status | thread settles or becomes idle removes the line |
| waiting line | at the edge | waits.py observes wait conditions; loom.py:421 observe_waits adds waiting facts | wait condition met or times out removes the line |
| unease line | at the edge | idle.py:348 present_unease returns line when unease exists | unease clears when fuel readings normalize |
| fuel line | at the edge | idle.py:354 fuel_line generates from usage readings | fuel readings refresh (usage.py) clears stale state |
| face line | at the edge | faces.py:146 proposal_line returns non-empty when face proposed | face taken or cleared via verbs_faces.py:25 |
| cited line | at the edge | reincarnate.py:242 cited_line returns closures since gen began that README cites | citation condition met or thread moves on |
| gone line | at the edge | jack.py:175 gone lines for keys no longer in said set | jack.py:174 slots logic prevents duplicate gone lines |
| moved line | at the edge | update.py:510 moved_line returns non-empty when facts show moved strands | moved facts retire after acknowledgment |
| notes/scars injection | below | seed/core/immune.d/* scripts run on loom start and at immune intervals | immune step completes; scars retire when guarded condition passes |
| waits and parks | at the edge | waits.py:8 observe_waits checks conditions; jack.py:461 _note_tool handles parks | wait condition met (waits.py:207) or park cleared |
| morning pass | below | loom.py tick_once runs at every tick; health steps pace themselves | each step completes or degrades |
| wake recipe and its cuts | in focus | seed/core/loom/wake:471 main runs recipe; cuts applied when budget exceeded | wake builds successfully or fails; cuts recorded in wake.sizes fact |
| index generation | below | seed/core/loom/index:437 main runs on loom start and change | index generation completes; new index replaces old |
| ledger facts | below | ledger.py append writes facts; read_facts reads them back | facts retire when superseded or after TTL |
| health steps | in focus | loom.py:3056-3087 health.guard calls for each step (usage, gauge, connector-ownership, inputs, tick_once, publisher, parts, waits, mirror, library, sample, disk, cpu, memory, reap, board, spa, audit, update) | health.guard completes or writes loom.degraded; step backs off on failure |
| update offers | below | update.py:232 poll checks for updates; step applies them | update applied or refused; update.py:301 newest_release retires old offers |
| mirror | below | mirror.py:119 Mirror class; loom.py:3075 health.guard("mirror", mirror.step) | mirror.step completes or degrades |
| board publisher | at the edge | board.py publisher step; loom.py:3071 health.guard("publisher", publisher.step) | publisher.step completes; board messages delivered |
| attention | below | attention step in main loop; no explicit line, managed via presence | attention condition met or times out |
| molt and its preview | at the edge | loom.py:3029 record_start records start facts; moltcost.py:188 wake_line shows cost | molt completes; cost facts recorded in ledger |

## Processes with no rise rule

Processes that fail silently (no mechanism puts them at the edge when they should appear):

- **attention**: no dedicated rise rule; relies on presence updates but has no explicit boundary line generation
- **molt preview**: wake_line in moltcost.py:188 generates line but has no automatic trigger to put it at the edge

## Processes with no sink rule

Processes that pile up (no mechanism removes them when they should retire):

- **notes/scars injection**: immune scripts run but their output has no automatic cleanup; scars persist until manually retired
- **ledger facts**: facts accumulate; no automatic pruning based on age or relevance
- **index generation**: generated index files are not automatically cleaned up when stale
- **health steps**: degraded episodes can accumulate if recovery facts are not written
