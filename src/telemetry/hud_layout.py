"""
hud_layout.py -- where things are: game names, asset folders and every HUD crop.
Pure data, no I/O. Crops are (y1, y2, x1, x2) on the full game frame.
"""

from paths import PROJECT_DIR

TEMPLATES_DIR = PROJECT_DIR / "templates"
UI_STATES_DIR = PROJECT_DIR / "ui_states"
TYRE_COMPOUNDS_DIR = PROJECT_DIR / "tyre_compounds"

# Real process name (launcher is ASC.exe; game itself runs as Unreal's
# "-Win64-Shipping.exe").
GAME_EXE_NAME = "ASC-Win64-Shipping.exe"
GAME_WINDOW_TITLE = "ASC"

# HUD layout left to right: HP, Fuel, Tyre life. Laptime crop stays wide,
# max render width not confirmed yet.
CROP_REGIONS = {
    "lap": (112, 231, 3318, 3637),
    "laptime": (307, 420, 3007, 3629),
    "hp": (1770, 1820, 3358, 3454),
    "fuel": (1770, 1820, 3454, 3550),
    "tyre": (1770, 1820, 3548, 3644),
    # "LAST LAP" readout. Fixes pit-lap times when the live 'laptime'
    # counter freezes mid-stop. Track-limits violation blanks this out;
    # lap_summary.py handles that.
    "last_lap_time": (585, 635, 3360, 3635),
}

# Static reference-image checks, separate from CROP_REGIONS' digit read.
# Checked every cycle -- field-count alone misses the pit-stop screen.
UI_STATE_REGIONS = {
    "pause": (421, 485, 781, 1187),
    "pitstop": (255, 300, 1566, 1896),
}

TYRE_COMPOUND_CROP = (1894, 1967, 3559, 3632)

# Fuel number above the fuel bar on the pit-stop screen. Wide so a 3-digit value still fits.
PIT_FUEL_CROP = (470, 550, 3460, 3680)

# Leaderboard overlay: P1 row plus 3 rows around the player. Tyre crops are
# 56px squares centred on the icon.
LEADERBOARD_CROPS = {
    "current_pos": (125, 243, 210, 463),
    "leader_time": (327, 389, 516, 720),
    "leader_tyre": (332, 388, 740, 796),
    "row2_pos": (459, 523, 227, 294),
    "row2_time": (453, 523, 514, 723),
    "row2_tyre": (460, 516, 740, 796),
    "row3_pos": (547, 608, 228, 296),
    "row3_time": (547, 610, 514, 726),
    "row3_tyre": (547, 603, 741, 797),
    "row4_pos": (635, 692, 230, 291),
    "row4_time": (638, 699, 514, 723),
    "row4_tyre": (637, 693, 739, 795),
}
# Everything the telemetry loop reads; the capture grabs only these.
READ_REGIONS = (
    *CROP_REGIONS.values(),
    *UI_STATE_REGIONS.values(),
    TYRE_COMPOUND_CROP,
    PIT_FUEL_CROP,
    *LEADERBOARD_CROPS.values(),
)
LEADERBOARD_ROWS = ("leader", "row2", "row3", "row4")
LEADERBOARD_BADGE_ROWS = ("row2", "row3", "row4")


def crop(frame, region):
    y1, y2, x1, x2 = region
    return frame[y1:y2, x1:x2]
