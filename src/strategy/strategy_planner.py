"""
strategy_planner.py -- optimal stint plans by dynamic programming over laps left.

best(n, banned) = cheapest way to cover n laps whose first stint isn't `banned`:
    min over compound c != banned, length k of  stint_cost(c, k) + pit_loss + best(n - k, None)
A stint's cost depends only on its compound and length, so the rest of the plan never
depends on how earlier laps were run (optimal substructure). stint_cost comes from a
TyreModel, so stint lengths and stop counts come out of the data, not a fixed threshold.

Game rule: the first stop must change compound (wets count). `banned` carries that rule
into the first stint after the next stop.

No lap is planned to end below MIN_END_PCT: 0% is drivable but far slower than the model can
learn, since nobody runs laps there.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

from strategy.race_types import Compound, LiveOption, Plan
from strategy.tyre_model import TyreModel

PUNCTURE_LIMIT_PCT = None  # set to ~25 once the game version with punctures is in use
MIN_END_PCT = 5.0
DRY_COMPOUNDS = ("soft", "medium", "hard")
MAX_STINT_LAPS = 100

# (seconds, ((compound, laps), ...)): a part-built plan inside the search
_Partial = tuple[float, tuple[tuple[Compound, int], ...]]


def _lap_allowed(start_pct: float, wear: float) -> bool:
    if PUNCTURE_LIMIT_PCT is not None and start_pct <= PUNCTURE_LIMIT_PCT:
        return False
    return start_pct - wear >= MIN_END_PCT


def stint_cost(model: TyreModel, compound: Compound, laps: int, start_pct: float = 100.0) -> float | None:
    """Seconds for `laps` laps on `compound` from start_pct; None if a lap breaks a tyre limit."""
    wear = model.wear_per_lap[compound]
    total = 0.0
    for i in range(laps):
        start = start_pct - wear * i
        if not _lap_allowed(start, wear):
            return None
        total += model.lap_time(compound, start)
    return total


def max_stint_laps(model: TyreModel, compound: Compound) -> int:
    """Longest stint on fresh `compound` within the tyre limits."""
    k = 0
    while k < MAX_STINT_LAPS and stint_cost(model, compound, k + 1) is not None:
        k += 1
    return k


class Planner:
    """Stint plans for one tyre model and pit loss.

    Every plan is costed from the same two things, so they are held here once instead of passed
    to each call. Stint costs and the best-rest-of-race table only depend on them, so they are
    cached on the instance and shared by every plan asked of it.
    """

    def __init__(self, model: TyreModel, pit_loss_sec: float | None):
        self.model = model
        self.pit_loss_sec = pit_loss_sec  # None = no pit-loss data yet
        self._pit = pit_loss_sec or 0.0  # unknown pit loss is planned as free
        self._compounds = [c for c in model.compounds if c in DRY_COMPOUNDS]
        self._cost: Callable[[Compound, int], float | None] = cache(lambda c, k: stint_cost(model, c, k))
        self._best: Callable[[int, Compound | None], _Partial | None] = cache(self._best_uncached)

    @classmethod
    def for_model(cls, model: TyreModel | None, pit_loss_sec: float | None) -> Planner | None:
        """A Planner, or None without a model."""
        return None if model is None else cls(model, pit_loss_sec)

    def plan(
        self, laps: int, first_banned: Compound | None = None, ban_second: bool = False, top_n: int = 3
    ) -> list[Plan]:
        """
        Best plans for `laps` laps on fresh tyres. Each plan is
        {"total_time_sec", "stops", "stints": [{"compound", "laps"}]}, fastest first. Plans with
        the same stints in a different order count once; "stops" counts stops between planned stints.

        first_banned: compound the first stint can't use (live: current compound before the rule is met).
        ban_second: pre-race rule -- the second stint can't repeat the first stint's compound.
        """
        if laps <= 0 or not self._compounds:
            return []
        candidates: list[_Partial] = []
        for c in self._compounds:
            if c == first_banned:
                continue
            for k in range(1, laps + 1):
                stint = self._cost(c, k)
                if stint is None:
                    break
                if k == laps:
                    candidates.append((stint, ((c, k),)))
                    continue
                rest = self._best(laps - k, c if ban_second else None)
                if rest is not None:
                    candidates.append((stint + self._pit + rest[0], ((c, k),) + rest[1]))

        candidates.sort(key=lambda t: (t[0], len(t[1])))
        plans: list[Plan] = []
        seen = set()
        for total, seq in candidates:
            key = tuple(sorted(seq))
            if key in seen:
                continue
            seen.add(key)
            plans.append(
                {"total_time_sec": total, "stops": len(seq) - 1, "stints": [{"compound": c, "laps": k} for c, k in seq]}
            )
            if len(plans) >= top_n:
                break
        return plans

    def _best_uncached(self, n: int, banned: Compound | None) -> _Partial | None:
        """(seconds, ((compound, laps), ...)) for the cheapest n laps whose first stint isn't banned."""
        if n == 0:
            return 0.0, ()
        result: _Partial | None = None
        for c in self._compounds:
            if c == banned:
                continue
            for k in range(1, n + 1):
                stint = self._cost(c, k)
                if stint is None:
                    break
                rest = self._best(n - k, None)
                if rest is None:
                    continue
                total = stint + (self._pit if k < n else 0.0) + rest[0]
                if result is None or total < result[0]:
                    result = (total, ((c, k),) + rest[1])
        return result

    def pit_options(
        self, laps_left: int, current_compound: Compound | None, rule_met: bool, top_n: int = 2
    ) -> list[tuple[str, int, list[Plan]]]:
        """
        Plans if pitting at the end of this lap or the next: [(label, laps after the stop, plans)].
        laps_left includes the lap in progress. Until the compound rule is met, the next stint
        can't reuse current_compound.
        """
        banned = None if rule_met else current_compound
        options: list[tuple[str, int, list[Plan]]] = []
        for label, laps_used in (("this lap", 1), ("next lap", 2)):
            after = laps_left - laps_used
            plans = self.plan(after, first_banned=banned, top_n=top_n) if after > 0 else []
            options.append((label, after, plans))
        return options

    def from_current(
        self, laps_left: int, compound: Compound | None, tyre_pct: float, rule_met: bool, top_n: int | None = 3
    ) -> list[LiveOption]:
        """
        Options from the lap in progress, laps_left including it: stay out pit_in laps on the
        current tyre (pit_in=1 pits at the end of this lap), then the best plan for the rest.
        [{"pit_in", "total_time_sec", "stints"}], fastest first, one per pit_in; pit_in None means
        run to the flag. Pitting this lap is always an option. [] if the current tyre isn't a modelled slick.
        """
        if compound not in DRY_COMPOUNDS or compound not in self.model.compounds or laps_left <= 0:
            return []
        banned = None if rule_met else compound
        options: list[LiveOption] = []
        for k in range(1, laps_left + 1):
            stay = stint_cost(self.model, compound, k, tyre_pct)
            if stay is None:
                if k > 1:
                    break
                stay = self.model.lap_time(compound, tyre_pct)
            if k == laps_left:
                options.append({"pit_in": None, "total_time_sec": stay, "stints": []})
                break
            rest = self.plan(laps_left - k, first_banned=banned, top_n=1)
            if rest:
                options.append(
                    {
                        "pit_in": k,
                        "total_time_sec": stay + self._pit + rest[0]["total_time_sec"],
                        "stints": rest[0]["stints"],
                    }
                )
        options.sort(key=lambda o: o["total_time_sec"])
        return options[:top_n] if top_n else options

    def lap_times(self, plan: Plan) -> list[float]:
        """Predicted time for each lap of a plan on fresh tyres, pit loss added to each stint's last lap."""
        times = []
        for i, stint in enumerate(plan["stints"]):
            wear = self.model.wear_per_lap[stint["compound"]]
            for lap in range(stint["laps"]):
                times.append(self.model.lap_time(stint["compound"], 100.0 - wear * lap))
            if i < len(plan["stints"]) - 1:
                times[-1] += self._pit
        return times


def format_plan(plan_: Plan | LiveOption) -> str:
    """'M6 -> H7 -> M6'"""
    return " -> ".join(f"{s['compound'][0].upper()}{s['laps']}" for s in plan_["stints"])
