"""
Tests for the cap on live SSE streams per student

On 2026-11-03 one student's browser kept opening new streams every few
seconds without the old ones ever closing, until nginx ran out of
connections. The server now ends a student's oldest stream once they have
more than MAX_STREAMS_PER_STUDENT open.
"""

import asyncio

import pytest

from app.routes import sse


async def _open_stream(course: str, tsn: str):
    gen = sse.event_generator(course, filter_counts=True, student_tsn=tsn)
    assert await gen.__anext__() == ": connected\n\n"
    return gen


async def _ends_soon(gen) -> bool:
    """True if the stream finishes (rather than waiting for events) within 3 s"""
    try:
        await asyncio.wait_for(gen.__anext__(), timeout=3.0)
    except StopAsyncIteration:
        return True
    except asyncio.TimeoutError:
        return False
    return False


@pytest.mark.asyncio
async def test_opening_one_stream_too_many_ends_the_oldest(redis_client) -> None:
    cap = sse.MAX_STREAMS_PER_STUDENT
    streams = [await _open_stream("test-course", "111111111") for _ in range(cap)]
    try:
        assert len(sse._student_streams[("test-course", "111111111")]) == cap

        newest = await _open_stream("test-course", "111111111")
        streams.append(newest)

        assert await _ends_soon(streams[0])
        assert len(sse._student_streams[("test-course", "111111111")]) == cap
    finally:
        for gen in streams:
            await gen.aclose()

    assert ("test-course", "111111111") not in sse._student_streams


def _open_count(course: str, tsn: str) -> int:
    """Number of the student's streams still registered and not told to end"""
    return sum(not stop.is_set() for stop in sse._student_streams.get((course, tsn), []))


@pytest.mark.asyncio
async def test_cap_is_per_student_and_per_course(redis_client) -> None:
    cap = sse.MAX_STREAMS_PER_STUDENT
    streams = [await _open_stream("test-course", "111111111") for _ in range(cap)]
    others = [
        await _open_stream("test-course", "222222222"),
        await _open_stream("other-course", "111111111"),
    ]
    try:
        # Neither another student's stream nor the same student's stream in
        # another course counts toward this student's cap
        assert _open_count("test-course", "111111111") == cap
        assert _open_count("test-course", "222222222") == 1
        assert _open_count("other-course", "111111111") == 1
    finally:
        for gen in streams + others:
            await gen.aclose()

    assert sse._student_streams == {}


@pytest.mark.asyncio
async def test_admin_streams_are_not_capped(redis_client) -> None:
    gens = []
    try:
        for _ in range(sse.MAX_STREAMS_PER_STUDENT + 2):
            gen = sse.event_generator("test-course", filter_counts=False)
            assert await gen.__anext__() == ": connected\n\n"
            gens.append(gen)
        assert sse._student_streams == {}
    finally:
        for gen in gens:
            await gen.aclose()
