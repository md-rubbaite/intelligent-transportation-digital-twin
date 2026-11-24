# ADR 0004: The twin advises; people confirm closures

- Status: accepted
- Date: 2026-11-24

## Context
A twin that acts on its own guesses can make things worse. A false incident alarm that closes lanes in the model would distort meter rates and what-if results. Road operators are also accountable for what happens on the road, and their procedures require a human decision for incident response.

## Decision
- The only automatic output to the road is the ramp-meter rate. It is bounded, and the queue override releases the meter when a ramp queue reaches its limit.
- Incident alarms are advisory. A lane closure enters the model only when an operator confirms it through the API or the console. The confirmation is recorded in the alert log with the operator's role.
- When readings no longer match a confirmed closure, the twin asks the operator to reopen it. It does not reopen on its own.
- Metering can be switched off by an operator at any time.

## Consequences
- Wrong alarms cost an operator's glance, not a wrong model.
- Model accuracy during incidents depends on how quickly operators confirm closures. The demo shows the effect: the speed error falls once the closure is confirmed.
- Field controllers must keep their own safe fallback if the twin stops sending rates (outside this repository).
