import asyncio

from app.jobs import JobManager, sse_stream


async def test_create_job_runs_in_background_and_returns_immediately():
    manager = JobManager()
    started = asyncio.Event()

    async def runner(job):
        started.set()
        job.emit_progress("crawl", "サイトを読み込み中")
        job.emit_done({"score": 80})

    job = manager.create_job("taro@example.co.jp", runner)

    assert job.status == "running"
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0)  # runner の完了を待つ
    assert job.status == "done"
    assert manager.get(job.id) is job


async def test_subscriber_receives_progress_and_done_events():
    manager = JobManager()

    async def runner(job):
        job.emit_progress("crawl", "サイトを読み込み中")
        job.emit_progress("keywords", "対策キーワードを推定中")
        job.emit_done({"score": 80})

    job = manager.create_job("taro@example.co.jp", runner)

    events = []
    async for chunk in sse_stream(job):
        events.append(chunk)

    assert any('"stage": "crawl"' in e for e in events)
    assert any('"stage": "keywords"' in e for e in events)
    assert any('"type": "done"' in e for e in events)


async def test_late_subscriber_receives_past_events_replayed():
    manager = JobManager()

    async def runner(job):
        job.emit_progress("crawl", "サイトを読み込み中")
        job.emit_done({"score": 80})

    job = manager.create_job("taro@example.co.jp", runner)
    await asyncio.sleep(0)  # runner を完了させてから遅れて購読する

    events = []
    async for chunk in sse_stream(job):
        events.append(chunk)

    assert any('"stage": "crawl"' in e for e in events)
    assert any('"type": "done"' in e for e in events)


async def test_error_in_runner_is_captured_not_raised():
    manager = JobManager()

    async def runner(job):
        raise ValueError("crawl failed: timeout")

    job = manager.create_job("taro@example.co.jp", runner)

    events = []
    async for chunk in sse_stream(job):
        events.append(chunk)

    assert job.status == "error"
    assert any("crawl failed: timeout" in e for e in events)


async def test_warning_event_does_not_stop_stream():
    manager = JobManager()

    async def runner(job):
        job.emit_warning("psi", "PageSpeed Insights の取得に失敗しました")
        job.emit_done({"score": 70})

    job = manager.create_job("taro@example.co.jp", runner)

    events = []
    async for chunk in sse_stream(job):
        events.append(chunk)

    assert any('"type": "warning"' in e for e in events)
    assert any('"type": "done"' in e for e in events)
