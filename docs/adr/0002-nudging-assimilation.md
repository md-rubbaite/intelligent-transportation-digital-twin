# ADR 0002: Constant-gain nudging, not an ensemble Kalman filter

- Status: accepted
- Date: 2026-11-24

## Context
The twin must stay close to the road between detectors that are 1.5 km apart, with noise and faults in the data. Ensemble Kalman filters are the standard research choice for CTM state estimation. They also need an ensemble (tens of model copies), tuning of process and observation noise, and they are hard for operators to reason about.

## Decision
Nudge the model toward good readings with a gain of 0.6, and interpolate the correction linearly between detectors. Rejected readings contribute nothing. The demand level per entry is corrected slowly from toll-plaza counts.

## Consequences
- One model copy, a few lines of code, and behaviour that operators can follow ("the model is pulled 60 % of the way toward what the detector sees").
- On synthetic runs the median speed error is near zero and the 95th percentile under 5 km/h (architecture notes, section 6).
- No uncertainty estimate. If confidence bands are needed for decisions, an EnKF is the natural next step and can reuse the same model and quality checks.
- Interpolation across a lane drop mixes per-lane residuals; in practice this is a small effect.
