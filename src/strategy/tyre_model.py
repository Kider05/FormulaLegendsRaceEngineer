"""
tyre_model.py -- learned lap-time and wear model. Pure logic: callers pass lap dicts in.

Lap time = base pace[compound] + sliding penalty(tyre % at mid-lap), where mid-lap % is the
start % minus half a lap's wear. The penalty is two hinges,
a*max(0, KNEE_HIGH - m) + b*max(0, KNEE_LOW - m): flat above KNEE_HIGH, slope a down to
KNEE_LOW, slope a+b below it. a and b are fitted from laps,
pulled toward PRIOR_PENALTY_SLOPES so few low-tyre laps can't produce a wild curve, then
clamped so the penalty never shrinks as the tyre wears (a >= 0, a + b >= 0).
Each race also gets a pace offset (difficulty, car, driver form), pulled toward 0 so a few
laps can't swing it; base pace is the typical race. The live model plans with the current
race's offset. The fit is a robust (Huber) regression, so laps with driver errors count for less.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import median

import numpy as np

from strategy.race_analysis import is_pit_in, pit_kind
from strategy.race_types import Compound, Lap

KNEE_HIGH = 45.0
KNEE_LOW = 30.0
PRIOR_PENALTY_SLOPES = (0.233, -0.183)  # mid-lap %: 0s at 45, ~1.2s at 40, 3.5s at 30, +0.05s per % below
PRIOR_WEIGHT = 500.0  # about 5 laps' worth of evidence at 35%
RACE_OFFSET_PRIOR_WEIGHT = 3.0  # a race's offset counts as 3 laps at 0 before its own laps
HUBER_K = 1.345
HUBER_ITERATIONS = 30
MIN_RESIDUAL_SCALE = 0.1  # seconds; lap timing is never cleaner than this
MIN_LAPS_PER_COMPOUND = 3
WET_BUFFER_LAPS = 2  # slick laps this close to a wet stint ran on a damp track


def _driven_compound(lap: Lap, prev_lap: Lap | None) -> Compound | None:
    """Compound a lap was driven on. Older rows store an in-lap under the tyre fitted at its end."""
    if prev_lap and is_pit_in(lap) and prev_lap.get("compound"):
        return prev_lap["compound"]
    return lap.get("compound")


def annotate_start_pct(race_laps: Sequence[Lap]) -> list[Lap]:
    """
    One race's laps (in order) -> same dicts with "compound" set to the compound driven,
    plus "start_pct" / "mid_pct" (tyre % at the start / middle of the lap) and "near_wet".
    Tyres reset to 100 before a pit out-lap or a lap on a different compound, and after a
    lap that gained tyre life. Slick laps within WET_BUFFER_LAPS of a wet lap are near_wet.
    """
    laps = list(race_laps)
    driven = [_driven_compound(lap, laps[i - 1] if i else None) for i, lap in enumerate(laps)]
    wet_laps = [n for lap, c in zip(laps, driven, strict=True) if c == "wet" and (n := lap.get("lap"))]
    out: list[Lap] = []
    pct, prev_compound = 100.0, None
    for lap, compound in zip(laps, driven, strict=True):
        if pit_kind(lap) == "out" or (prev_compound and compound and compound != prev_compound):
            pct = 100.0
        used = lap.get("tyre_used") or 0.0
        near_wet = compound != "wet" and any(abs((lap.get("lap") or 0) - w) <= WET_BUFFER_LAPS for w in wet_laps)
        out.append({**lap, "compound": compound, "start_pct": pct, "mid_pct": pct - used / 2, "near_wet": near_wet})
        pct -= used
        if (lap.get("tyre_gained") or 0) > 0:
            pct = 100.0
        prev_compound = compound
    return out


def current_tyre_pct(race_laps: Sequence[Lap]) -> float:
    """Tyre % at the start of the lap after race_laps (100 before lap 1 or right after a stop)."""
    laps = annotate_start_pct(race_laps)
    if not laps:
        return 100.0
    last = laps[-1]
    if (last.get("tyre_gained") or 0) > 0:
        return 100.0
    return max(0.0, last["start_pct"] - (last.get("tyre_used") or 0.0))


WET_CONDITIONS = {"raining", "wet", "drying"}


def is_model_lap(lap: Lap) -> bool:
    """Laps that reflect tyre pace: not lap 1, not pit in/out, not near a wet stint, complete, timed,
    not excluded in review, and not a slick lap marked raining/wet/drying."""
    slick_in_wet = lap.get("compound") != "wet" and lap.get("condition") in WET_CONDITIONS
    return bool(
        lap.get("lap") != 1
        and not lap.get("near_wet")
        and pit_kind(lap) is None
        and not lap.get("excluded")
        and not slick_in_wet
        and not lap.get("incomplete")
        and lap.get("laptime_sec") is not None
        and lap.get("compound")
        and lap.get("start_pct") is not None
        and lap["start_pct"] > 0
    )


def fit_races(races: Sequence[Sequence[Lap]], use_last_race_offset: bool = False) -> TyreModel | None:
    """
    fit() over several races' laps (race_db.track_laps() shape); None if nothing usable.
    use_last_race_offset: plan with the last race's pace (live: the race in progress).
    """
    laps: list[Lap] = [{**lap, "race": i} for i, race in enumerate(races) for lap in annotate_start_pct(race)]
    model = fit(laps)
    if model is not None and use_last_race_offset:
        model.pace_offset = model.race_offsets.get(len(races) - 1, 0.0)
    return model


def _hinges(pct: float) -> tuple[float, float]:
    return max(0.0, KNEE_HIGH - pct), max(0.0, KNEE_LOW - pct)


@dataclass
class TyreModel:
    """Lap time = base pace + race pace offset + sliding penalty at the lap's mid-lap tyre %."""

    base_pace: dict[Compound, float]  # fresh-tyre lap time
    penalty_slopes: tuple[float, float]  # seconds per % below KNEE_HIGH, extra per % below KNEE_LOW
    wear_per_lap: dict[Compound, float]  # tyre % used per lap
    lap_counts: dict[Compound, int]  # laps each compound was fitted from
    race_offsets: dict[int, float] = field(default_factory=dict)  # per-race pace vs the average race
    pace_offset: float = 0.0  # offset applied to lap_time (live: the race in progress)

    @property
    def compounds(self) -> list[Compound]:
        return [c for c in self.base_pace if c in self.wear_per_lap]

    def penalty(self, mid_pct: float) -> float:
        """Sliding cost of a lap whose tyre is at mid_pct halfway round."""
        h1, h2 = _hinges(mid_pct)
        return self.penalty_slopes[0] * h1 + self.penalty_slopes[1] * h2

    def lap_time(self, compound: Compound, start_pct: float) -> float:
        return self.base_pace[compound] + self.pace_offset + self.penalty(start_pct - self.wear_per_lap[compound] / 2)


def fit(laps: Sequence[Lap]) -> TyreModel | None:
    """
    TyreModel from laps already passed through annotate_start_pct(). Compounds with fewer than
    MIN_LAPS_PER_COMPOUND usable laps are left out. Laps with a "race" key get a per-race
    pace offset. None if nothing usable.
    """
    usable = _samples(laps)
    counts = Counter(s.compound for s in usable)
    compounds = sorted(c for c, n in counts.items() if n >= MIN_LAPS_PER_COMPOUND)
    usable = [s for s in usable if s.compound in compounds]
    if not usable:
        return None

    n_c = len(compounds)
    races = sorted({s.race for s in usable if s.race is not None})
    X = np.zeros((len(usable), n_c + 2 + len(races)))
    y = np.array([s.laptime_sec for s in usable])
    for i, s in enumerate(usable):
        X[i, compounds.index(s.compound)] = 1.0
        X[i, n_c], X[i, n_c + 1] = _hinges(s.mid_pct)
        if s.race is not None:
            X[i, n_c + 2 + races.index(s.race)] = 1.0

    priors = [(n_c, PRIOR_PENALTY_SLOPES[0], PRIOR_WEIGHT), (n_c + 1, PRIOR_PENALTY_SLOPES[1], PRIOR_WEIGHT)]
    priors += [(n_c + 2 + j, 0.0, RACE_OFFSET_PRIOR_WEIGHT) for j in range(len(races))]
    coef = _huber_fit(X, y, priors)

    wear = {}
    for c in compounds:
        w = [s.tyre_used for s in usable if s.compound == c and s.tyre_used is not None and s.tyre_used > 0]
        if w:
            wear[c] = median(w)
    return TyreModel(
        base_pace={c: float(coef[i]) for i, c in enumerate(compounds)},
        penalty_slopes=_monotonic(float(coef[n_c]), float(coef[n_c + 1])),
        wear_per_lap=wear,
        lap_counts={c: counts[c] for c in compounds},
        race_offsets={r: float(coef[n_c + 2 + j]) for j, r in enumerate(races)},
    )


@dataclass(frozen=True)
class _Sample:
    """A lap the model fits, with the fields it needs and none of them missing."""

    compound: Compound
    laptime_sec: float
    mid_pct: float
    tyre_used: float | None
    race: int | None


def _samples(laps: Sequence[Lap]) -> list[_Sample]:
    samples = []
    for lap in laps:
        compound, laptime = lap.get("compound"), lap.get("laptime_sec")
        if not is_model_lap(lap) or not compound or laptime is None:
            continue
        samples.append(
            _Sample(compound, laptime, lap.get("mid_pct", lap["start_pct"]), lap.get("tyre_used"), lap.get("race"))
        )
    return samples


def _monotonic(a: float, b: float) -> tuple[float, float]:
    a = max(a, 0.0)
    return a, max(b, -a)


def _huber_fit(X: np.ndarray, y: np.ndarray, priors: list[tuple[int, float, float]]) -> np.ndarray:
    """
    Iteratively reweighted least squares with Huber weights. Each (index, value, weight) in
    priors adds weight * (coef[index] - value)^2 to the loss.
    """
    p = X.shape[1]
    prior_rows = np.zeros((len(priors), p))
    prior_y = np.zeros(len(priors))
    for row, (j, value, weight) in enumerate(priors):
        prior_rows[row, j] = np.sqrt(weight)
        prior_y[row] = np.sqrt(weight) * value

    weights = np.ones(len(y))
    coef = np.zeros(p)
    for _ in range(HUBER_ITERATIONS):
        sw = np.sqrt(weights)
        A = np.vstack([X * sw[:, None], prior_rows])
        b = np.concatenate([y * sw, prior_y])
        coef = np.linalg.lstsq(A, b, rcond=None)[0]
        resid = y - X @ coef
        scale = max(np.median(np.abs(resid - np.median(resid))) / 0.6745, MIN_RESIDUAL_SCALE)
        cutoff = HUBER_K * scale
        new_weights = np.where(np.abs(resid) <= cutoff, 1.0, cutoff / np.maximum(np.abs(resid), 1e-12))
        if np.allclose(new_weights, weights, atol=1e-6):
            break
        weights = new_weights
    return coef
