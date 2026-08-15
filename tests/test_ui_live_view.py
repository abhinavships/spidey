"""The public demo UI can show live Chrome frames from the local driver."""

from pathlib import Path


UI = Path(__file__).resolve().parents[1] / "ui" / "index.html"
SERVER = Path(__file__).resolve().parents[1] / "app" / "server.py"


def test_ui_renders_live_browser_frame_events():
    page = UI.read_text(encoding="utf-8")
    assert 'id="browser-frame"' in page
    assert "case 'frame'" in page
    assert "ev.data.src" in page


def test_server_streams_frame_events():
    server = SERVER.read_text(encoding="utf-8")
    assert "async def stream_frames" in server
    assert 'type="frame"' in server
    assert "WA_FRAME_INTERVAL_S" in server
