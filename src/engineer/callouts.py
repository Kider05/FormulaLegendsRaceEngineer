"""
callouts.py -- decides what the race engineer says and when the fuel beep fires.
Pure logic: no audio or GUI here, so every rule can be tested.
"""

CALL_AT_FRACTION = 2 / 3  # callouts fire this far through the lap, by average lap time
FUEL_BEEP_LEAD = 7  # beep this much fuel before the target
COMPOUND_WORDS = {"soft": "softs", "medium": "mediums", "hard": "hards", "wet": "wets"}


def places_phrase(result):
    """positions_lost_if_pit() result -> 'Clear by 2.3 seconds.' / 'Lose one place.' / ''."""
    if result is None:
        return ""
    if result["label"] == "0":
        return f"Clear by {result['margin_sec']:.1f} seconds." if result["margin_sec"] is not None else "Clear."
    return {"1": "Lose one place.", "1+": "Lose one plus.", "2+": "Lose two plus."}.get(result["label"], "")


def lap_message(plan, places="", wet=None):
    """Phrases to say this lap for a race_view current plan (or wet window), spoken in order; () if nothing.
    The first phrase never depends on `places`, so it can be rendered before the call."""
    if plan is None:
        return ("Possible pit window this lap.",) if wet and wet["pit_in"] == 1 else ()
    best = plan["best"]
    if best["pit_in"] in (1, 2):
        compound = COMPOUND_WORDS.get(best["stints"][0]["compound"], best["stints"][0]["compound"])
        if best["pit_in"] == 1:
            return (f"Box, box. Box this lap, {compound}.",)
        return tuple(p for p in (f"Box next lap, {compound}.", places) if p)
    if plan["out_lap"]:
        if best["pit_in"] is None:
            return (f"{plan['laps_left']} laps, to the flag.",)
        return (f"{best['pit_in']} laps, box lap {plan['laps_done'] + best['pit_in']}.",)
    return ()


class CalloutTracker:
    """Fires lap_message() once per lap, when the lap timer passes CALL_AT_FRACTION of the average lap.
    The timer must first be seen below that point on the new lap, so a stale reading from the
    previous lap can't fire it early."""

    def __init__(self):
        self._lap = None
        self._armed = False
        self._fired = False

    def update(self, lap_in_progress, elapsed_sec, avg_lap_sec, plan, places="", wet=None):
        if lap_in_progress != self._lap:
            self._lap, self._armed, self._fired = lap_in_progress, False, False
        if elapsed_sec is None or not avg_lap_sec or self._fired:
            return ()
        if elapsed_sec < avg_lap_sec * CALL_AT_FRACTION:
            self._armed = True
            return ()
        if not self._armed:
            return ()
        self._fired = True
        return lap_message(plan, places, wet)


class FuelBeep:
    """One beep per stop once fuel reaches target - FUEL_BEEP_LEAD (immediately if already there)."""

    def __init__(self):
        self._fired = False

    def reset(self):
        self._fired = False

    def update(self, fuel, target):
        if self._fired or fuel is None or target is None:
            return False
        if fuel >= target - FUEL_BEEP_LEAD:
            self._fired = True
            return True
        return False
