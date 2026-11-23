"""
hud_reader.py -- reads the HUD: digit fields, tyre compound icon, pause/pit screens and the
leaderboard overlay (position, gaps, tyres, pit wrenches).
Templates and reference images load on first use, so importing this module touches no files.
"""

import re
from functools import cache

import cv2
import numpy as np

from telemetry.glyph_reader import build_template_holes, load_templates, read_string
from telemetry.hud_layout import (
    CROP_REGIONS,
    LEADERBOARD_BADGE_ROWS,
    LEADERBOARD_CROPS,
    LEADERBOARD_ROWS,
    PIT_FUEL_CROP,
    TEMPLATES_DIR,
    TYRE_COMPOUND_CROP,
    TYRE_COMPOUNDS_DIR,
    UI_STATE_REGIONS,
    UI_STATES_DIR,
    crop,
)
from telemetry.lap_summary import parse_current_lap, parse_int_field, parse_total_laps


@cache
def glyph_templates():
    """(templates, template_holes); holes fix 8-vs-9, see glyph_reader.count_holes."""
    templates = load_templates(TEMPLATES_DIR)
    return templates, build_template_holes(templates)


@cache
def ui_state_configs():
    """{state: (region, reference image)} for ui_state_detector."""
    return {name: (region, cv2.imread(str(UI_STATES_DIR / f"{name}.png"))) for name, region in UI_STATE_REGIONS.items()}


def read_text(img):
    """(text, low_confidence) for a crop of HUD digits."""
    templates, holes = glyph_templates()
    return read_string(img, templates, template_holes=holes)


def read_all_fields(frame):
    """Crop+read every HUD field. Returns {name: (result, low_confidence)}."""
    return {name: read_text(crop(frame, region)) for name, region in CROP_REGIONS.items()}


def read_tyre_compound(frame):
    """(compound or None, ring colour distance) for the HUD tyre icon."""
    return tyre_from_crop(crop(frame, TYRE_COMPOUND_CROP), HUD_ICON_SCALE)


def read_pit_fuel(frame):
    """Fuel on the pit-stop screen as an int; None unless it reads cleanly."""
    text, low_confidence = read_text(crop(frame, PIT_FUEL_CROP))
    return None if low_confidence else parse_int_field(text)


# --- leaderboard ------------------------------------------------------------

# Player badge is solid saturated blue: 209-223 measured, vs 37-145 elsewhere.
# Fallback only; the player row is normally the one matching current_pos.
PLAYER_ROW_MIN_SATURATION = 180

# Wrench replaces the gap digits while that car pits. Slid across the gap
# crop: 0.96-0.97 on the wrench, at most 0.60 elsewhere.
WRENCH_PATH = TYRE_COMPOUNDS_DIR / "pit_wrench.png"
WRENCH_THRESHOLD = 0.8

# Leaderboard tyre icon = dark disc, white letter, compound-coloured ring;
# only the ring colour is classified.
RING_INNER, RING_OUTER = 19.5, 23.5  # ring band, px from icon centre
INNER_DARK = (14.5, 16.5)  # dark band inside the ring on every icon
RING_SEARCH = 4  # px the icon centre may sit off the crop centre
RING_MAX_DIST = 30  # BGR distance beyond which nothing matches
# HUD icon is the leaderboard icon drawn 1.15x larger. Its fill behind the ring
# follows the tyre gauge, so only the ring colour is stable at low tyre life.
HUD_ICON_SCALE = 1.15

# BGR ring colours measured from captures. A 56px crop saved as
# tyre_compounds/lb_<compound>.png overrides one.
DEFAULT_RING_COLOURS = {"soft": (37, 29, 226), "medium": (4, 200, 241), "hard": (241, 241, 241), "wet": (153, 96, 12)}

_GAP_RE = re.compile(r"(?<!\d)[+-]\d+\.\d{3}(?!\d)")


def lb_crop(frame, name):
    return crop(frame, LEADERBOARD_CROPS[name])


def mean_saturation(img):
    return float(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 1].mean())


def ring_colour(img, scale=1.0):
    """Median ring colour, or None if no icon. Picks the centre within
    +/-RING_SEARCH px whose ring is most uniform. scale sizes the radii to the icon."""
    ring_inner, ring_outer = RING_INNER * scale, RING_OUTER * scale
    dark_inner, dark_outer = INNER_DARK[0] * scale, INNER_DARK[1] * scale
    h, w = img.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    best = None
    for dy in range(-RING_SEARCH, RING_SEARCH + 1):
        for dx in range(-RING_SEARCH, RING_SEARCH + 1):
            cx, cy = (w - 1) / 2 + dx, (h - 1) / 2 + dy
            if min(cx, cy, w - 1 - cx, h - 1 - cy) < ring_outer:
                continue
            dist = np.hypot(xx - cx, yy - cy)
            px = img[(dist >= ring_inner) & (dist <= ring_outer)]
            spread = px.std(axis=0).sum()
            if best is None or spread < best[0]:
                best = (spread, cx, cy, px)
    if best is None:
        return None
    _, cx, cy, px = best
    dist = np.hypot(xx - cx, yy - cy)
    inner = img[(dist >= dark_inner) & (dist <= dark_outer)]
    if np.median(inner, axis=0).max() > 90:
        return None
    return np.median(px, axis=0)


@cache
def ring_refs():
    """({compound: BGR ring colour}, [override files that had no readable icon])."""
    refs = {c: np.array(v, float) for c, v in DEFAULT_RING_COLOURS.items()}
    skipped = []
    for name in DEFAULT_RING_COLOURS:
        path = TYRE_COMPOUNDS_DIR / f"lb_{name}.png"
        if not path.exists():
            continue
        img = cv2.imread(str(path))
        colour = ring_colour(img) if img is not None else None
        if colour is None:
            skipped.append(path.name)
        else:
            refs[name] = colour
    return refs, skipped


@cache
def wrench_gray():
    """Grayscale wrench template, or None if the file is missing or unreadable."""
    img = cv2.imread(str(WRENCH_PATH)) if WRENCH_PATH.exists() else None
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img is not None else None


def tyre_from_crop(img, scale=1.0):
    """(compound or None, distance to nearest ring colour); (None, None) if no icon."""
    colour = ring_colour(img, scale)
    if colour is None:
        return None, None
    compound, dist = min(
        ((c, float(np.linalg.norm(colour - ref))) for c, ref in ring_refs()[0].items()), key=lambda t: t[1]
    )
    return (compound if dist <= RING_MAX_DIST else None), dist


def wrench_score(img):
    template = wrench_gray()
    if template is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    th, tw = template.shape
    pad_y, pad_x = max(0, th - gray.shape[0]), max(0, tw - gray.shape[1])
    if pad_y or pad_x:
        gray = cv2.copyMakeBorder(gray, 0, pad_y, 0, pad_x, cv2.BORDER_REPLICATE)
    return float(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED).max())


def parse_gap(text):
    """'-1.318' -> -1.318 (car ahead), '+0.401' -> 0.401 (car behind). Stray glyphs around
    the number are ignored ('-+0.613' -> 0.613, '-2.620.' -> -2.620); None unless exactly one match."""
    matches = _GAP_RE.findall(text) if text else []
    return float(matches[0]) if len(matches) == 1 else None


def read_leaderboard(frame):
    """Position, gaps, tyres and pit status from the leaderboard; unclean values are None."""

    def read(name):
        return read_text(lb_crop(frame, name))

    current_text, current_low = read("current_pos")
    position = parse_current_lap(current_text) if not current_low else None
    total_cars = parse_total_laps(current_text) if not current_low else None

    pos_text = {"leader": "1"}
    for row in LEADERBOARD_BADGE_ROWS:
        pos_text[row] = read(f"{row}_pos")[0]
    row_pos = {row: int(t) if t.isdigit() else None for row, t in pos_text.items()}
    sat = {row: mean_saturation(lb_crop(frame, f"{row}_pos")) for row in LEADERBOARD_BADGE_ROWS}

    if position == 1:
        player_row = "leader"
    else:
        matches = [r for r in LEADERBOARD_BADGE_ROWS if position is not None and row_pos[r] == position]
        if len(matches) == 1:
            player_row = matches[0]
        else:
            best = max(sat, key=sat.get)
            player_row = best if sat[best] >= PLAYER_ROW_MIN_SATURATION else None

    rows = []
    for row in LEADERBOARD_ROWS:
        entry = {
            "row": row,
            "pos": row_pos[row],
            "pos_text": pos_text[row],
            "is_player": row == player_row,
            "saturation": sat.get(row),
            "gap_text": None,
            "gap_sec": None,
            "pitting": False,
            "wrench_score": None,
            "tyre": None,
            "tyre_dist": None,
        }
        if row != player_row:
            entry["wrench_score"] = wrench_score(lb_crop(frame, f"{row}_time"))
            if entry["wrench_score"] is not None and entry["wrench_score"] >= WRENCH_THRESHOLD:
                entry["pitting"] = True
            else:
                entry["gap_text"] = read(f"{row}_time")[0]
                entry["gap_sec"] = parse_gap(entry["gap_text"])
            entry["tyre"], entry["tyre_dist"] = tyre_from_crop(lb_crop(frame, f"{row}_tyre"))
        rows.append(entry)

    def gap_to(target_pos, sign):
        """|gap| to the car at target_pos if its gap reads cleanly with the expected sign."""
        for e in rows:
            if not e["is_player"] and e["pos"] == target_pos and e["gap_sec"] is not None and e["gap_sec"] * sign > 0:
                return abs(e["gap_sec"])
        return None

    known = position is not None and player_row is not None
    return {
        "position": position,
        "total_cars": total_cars,
        "current_pos_text": current_text,
        "player_row": player_row,
        "gap_ahead_sec": gap_to(position - 1, -1) if known and position > 1 else None,
        "gap_behind_sec": gap_to(position + 1, 1) if known else None,
        "gap_to_leader_sec": gap_to(1, -1) if known and position > 1 else None,
        "rows": rows,
    }


def format_leaderboard_brief(lb):
    """One-line leaderboard, e.g. 'P3/20 ahead 1.234 behind 0.401 | P1 -5.120  P2 -1.234  P3 YOU  P4 +0.401'."""
    if lb is None:
        return "no leaderboard yet"
    fmt = lambda v: f"{v:.3f}" if v is not None else "--"
    rows = []
    for r in lb["rows"]:
        tag = (
            "YOU"
            if r["is_player"]
            else (
                "PIT"
                if r["pitting"]
                else (f"{r['gap_sec']:+.3f}" if r["gap_sec"] is not None else f"?{r['gap_text']!r}")
            )
        )
        rows.append(f"P{r['pos'] or '?'} {tag}")
    line = (
        f"P{lb['position'] or '?'}/{lb['total_cars'] or '?'} ahead {fmt(lb['gap_ahead_sec'])} "
        f"behind {fmt(lb['gap_behind_sec'])} | " + "  ".join(rows)
    )
    sm = lb.get("smoothed")
    if sm is not None:
        gaps = "  ".join(f"P{p} {g:+.3f}" for p, g in sorted(sm["gaps"].items()))
        line += (
            f"\n    SM: ahead {fmt(sm['gap_ahead_sec'])} behind {fmt(sm['gap_behind_sec'])} | "
            f"{gaps or 'not enough reads yet'}"
        )
    pace = lb.get("pace")
    if pace:
        line += "\n    PACE (s/lap lost to you): " + "  ".join(f"P{p} {v:+.2f}" for p, v in sorted(pace.items()))
    return line
