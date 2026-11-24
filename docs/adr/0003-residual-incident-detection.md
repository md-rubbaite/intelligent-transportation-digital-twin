# ADR 0003: Detect incidents from forecast residuals

- Status: accepted
- Date: 2026-11-24

## Context
Classic automatic incident detection (for example the California algorithms) compares occupancy at adjacent detectors. That flags every recurring bottleneck, such as a lane drop, every peak, unless each one is configured by hand. Operators then learn to ignore the alarms.

## Decision
Compare each detector with what the twin expected one interval earlier. An incident shows up as "denser than forecast upstream and lighter than forecast downstream", sustained for 3 of 4 intervals. Known bottlenecks are in the model, so they are part of the forecast and do not trigger alarms.

## Consequences
- No false alarms in incident-free synthetic peaks, including at the lane drop. Single-lane closures that create queues are found in 1.5 to 3 minutes.
- Closures that do not create queues (demand below the remaining capacity) are not found. Neither are some closures just upstream of an on-ramp, where ramp traffic refills the downstream detector. Video analytics or probe data are needed for these cases.
- Detection quality depends on the model being calibrated. On a badly calibrated model, the residuals carry model error as well as incidents.
- An alarm is a prompt for a human to check (CCTV, patrol). It never changes the model by itself (ADR 0004).
