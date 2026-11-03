from codepilot.core.events import RunEvent
from codepilot.tui.state import RunView
import pytest


def event(run: str, seq: int, step: int, text: str) -> RunEvent:
    return RunEvent(type="model.delta", run_id=run, seq=seq, step=step, timestamp="test", data={"text": text})


def test_interleaved_runs_steps_and_repeated_events_do_not_mix() -> None:
    one, two = RunView("first"), RunView("second")
    for item in [event("first", 1, 1, "a"), event("second", 1, 1, "b"), event("first", 2, 2, "c"), event("first", 2, 2, "duplicate")]:
        one.apply(item)
        two.apply(item)
    assert one.drafts == {1: "a", 2: "c"}
    assert two.drafts == {1: "b"}


@pytest.mark.parametrize(("kind", "data"), [
    ("model.delta", {"text": 1}),
    ("tool.started", {"name": "read_file"}),
    ("tool.completed", {"name": "read_file", "content": "x", "is_error": "false"}),
    ("run.finished", {"result": {"run_id": "different", "status": "success", "steps": 1, "stop_reason": "end_turn"}}),
])
def test_invalid_event_payload_is_rejected(kind: str, data: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RunEvent(type=kind, run_id="first", seq=1, step=1, timestamp="test", data=data)
