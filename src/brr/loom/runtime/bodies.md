| state | observed | next | commands |
|---|---|---|---|
| wanted, parked | closed | ended | end_settled, drain, drained, release |
| wanted, parked | failure | ended | exit, end_failed, drain, drained, release |
| parked | before until | parked | none |
| parked | gauge refreshing | parked | none |
| wanted | gauge refreshing | wanted | pending |
| wanted, parked | gauge empty | parked | park |
| wanted, parked | gauge ok | running | prepare, shown, spawn, started |
| running, leaving, stopping, unidentified | not alive | ended | collect, drain, exit, end, drain, drained, release |
| running | alive, open | running | none |
| running | alive, closed | leaving | leaving |
| leaving, unidentified | alive, reopened | running | stayed |
| leaving | alive, before deadline | leaving | none |
| leaving | alive, due, proven | stopping | stopped, signal |
| leaving | alive, due, unproven | unidentified | unidentified |
| stopping | alive, proven | stopping | signal |
| stopping | alive, not proven | stopping | none |
| unidentified | alive, closed | unidentified | none |
| exited | any | ended | end, drain, drained, release |
| ended | not released | ended | drain, drained, release |
