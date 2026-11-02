import asyncio

import pytest

from codepilot.transport.framing import MAX_FRAME_BYTES, FrameTooLarge, decode_frame, encode_frame, read_frame


async def test_frame_byte_limit_includes_newline() -> None:
    reader = asyncio.StreamReader(limit=MAX_FRAME_BYTES)
    reader.feed_data(b"x" * (MAX_FRAME_BYTES - 1) + b"\n")
    assert len(await read_frame(reader)) == MAX_FRAME_BYTES
    reader.feed_data(b"x" * MAX_FRAME_BYTES + b"\n")
    with pytest.raises(FrameTooLarge):
        await read_frame(reader)


async def test_eof_does_not_complete_a_partial_frame() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"method":"core.ping"}')
    reader.feed_eof()
    assert await read_frame(reader) is None


def test_unicode_and_embedded_newline_remain_one_frame() -> None:
    payload = {"text": "第一行\n第二行"}
    frame = encode_frame(payload)
    assert frame.count(b"\n") == 1
    assert decode_frame(frame) == payload


@pytest.mark.parametrize("frame", [b'{"n":NaN}\n', b'{"n":Infinity}\n', b'\xff\n'])
def test_invalid_json_constants_and_utf8_are_rejected(frame: bytes) -> None:
    with pytest.raises(ValueError):
        decode_frame(frame)
