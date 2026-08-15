"""The demo UI exposes opt-in browser-native voice narration."""

from pathlib import Path


UI = Path(__file__).resolve().parents[1] / "ui" / "index.html"


def test_ui_has_a_voice_toggle_and_speaks_narration_events():
    page = UI.read_text(encoding="utf-8")
    assert 'id="voice"' in page
    assert "speechSynthesis.speak" in page
    assert "speak(ev.text)" in page
