"""
Whale signal calibration - the concern that checks whether
composite_confidence's factor weights (services/confidence_scoring.py) and its
overall confidence-vs-observed-accuracy calibration still hold up against
real resolved signals, and tunes them if not. See confidence_calibration.py
(the rule-based bucket analysis + suggested-weights computation) and
calibration_history.py (point-in-time snapshots so calibration quality over
time is a trend, not just a single live report). See CHEATSHEET.md.
"""
