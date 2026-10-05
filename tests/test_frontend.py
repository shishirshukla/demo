from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_dashboard_loads_and_runs_demo():
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "frontend/app.py", default_timeout=120
    ).run()
    assert not app.exception
    assert app.title[0].value == "Market regimes. Measured results."
    next(
        button for button in app.button if button.label == "Run backtest →"
    ).click().run(timeout=120)
    assert not app.exception
    assert len(app.metric) == 5
    assert len(app.session_state["results"]) == 3
