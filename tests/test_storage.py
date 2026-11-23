"""Saving races and reviewing saved laps."""

from gui import race_view
from helpers import dry_laps


def test_review_row_and_saved_review_round_trip(tmp_path, monkeypatch):
    from storage import race_db

    monkeypatch.setattr(race_db, "DB_PATH", tmp_path / "race.db")
    laps = dry_laps(3, "medium")
    laps[1].update(condition="wet", excluded=True, position=4)
    race_id = race_db.add_race_data("Silverstone", laps)
    saved = race_db.race_laps(race_id)
    assert [(lap["condition"], lap["excluded"]) for lap in saved] == [(None, False), ("wet", True), (None, False)]
    assert race_view.lap_review_row(saved[1])[2:] == ("Medium", "-10", "-5", "", "P4", "wet", "yes")
    race_db.review_laps([saved[0]["db_id"], saved[1]["db_id"]], condition="drying", excluded=False)
    race_db.review_laps([saved[1]["db_id"]], condition="")
    assert [(lap["condition"], lap["excluded"]) for lap in race_db.race_laps(race_id)] == [
        ("drying", False),
        (None, False),
        (None, False),
    ]
