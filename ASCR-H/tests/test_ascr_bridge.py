import importlib

from src.ascr_bridge import ascr_import_context, call_ascr


def test_ascr_import_context_loads_ascr_src_and_restores_ascr_h_src():
    ascr_h_db = importlib.import_module("src.db")

    with ascr_import_context():
        ascr_recommender = importlib.import_module("src.recommender")
        ascr_deduper = importlib.import_module("src.event_deduper")
        assert "/ASCR/src/recommender.py" in ascr_recommender.__file__
        assert "/ASCR/src/event_deduper.py" in ascr_deduper.__file__

    restored_db = importlib.import_module("src.db")
    assert restored_db is ascr_h_db
    assert "/ASCR-H/src/db.py" in restored_db.__file__


def test_call_ascr_executes_without_leaking_src_package():
    result = call_ascr(
        "event_deduper",
        "canonical_news_title",
        "NVIDIA announces new AI platform, says report",
    )

    assert result == "nvidia announces new ai platform says report"
    assert "/ASCR-H/src/db.py" in importlib.import_module("src.db").__file__
