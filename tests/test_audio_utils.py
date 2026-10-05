import pytest
from utils.audio import percent_to_db, db_to_percent


def test_percent_to_db():
    assert percent_to_db(0) == -60.0
    assert percent_to_db(-10) == -60.0
    assert pytest.approx(percent_to_db(100), 0.01) == 0.0
    assert pytest.approx(percent_to_db(50), 0.05) == -6.02
    assert pytest.approx(percent_to_db(200), 0.05) == 6.02
    assert pytest.approx(percent_to_db(10), 0.1) == -20.0


def test_db_to_percent():
    assert db_to_percent(-60.0) == 0.0
    assert db_to_percent(-70.0) == 0.0
    assert pytest.approx(db_to_percent(0.0), 0.01) == 100.0
    assert pytest.approx(db_to_percent(-6.02), 0.5) == 50.0
    assert pytest.approx(db_to_percent(6.02), 0.5) == 200.0
