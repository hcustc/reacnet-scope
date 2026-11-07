"""Background work must not inherit locks from the threaded Dash server."""

import threading
import time

import psutil
import pytest

from scripts.webapp_dash.app import _background_callback_manager


_SERVER_LOCK = threading.Lock()


@pytest.mark.parametrize("operation,expected", [("job_running", False), ("terminate_job", None)])
def test_worker_exit_between_process_checks_is_normal_completion(monkeypatch, tmp_path, operation, expected):
    monkeypatch.setattr('scripts.webapp_dash.app.tempfile.gettempdir', lambda: str(tmp_path))
    manager = _background_callback_manager()
    monkeypatch.setattr(psutil, 'pid_exists', lambda pid: True)
    def already_exited(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, 'Process', already_exited)
    try:
        assert getattr(manager, operation)(999999) == expected
    finally:
        manager.handle.close()


def _locked_callback(progress, token):
    with _SERVER_LOCK:
        progress('completed')
        return token


def _waiting_callback(progress):
    progress('waiting')
    threading.Event().wait(60)
    return 'unexpected completion'


def test_background_callback_does_not_inherit_server_thread_lock(monkeypatch, tmp_path):
    monkeypatch.setattr('scripts.webapp_dash.app.tempfile.gettempdir', lambda: str(tmp_path))
    manager = _background_callback_manager()
    acquired = threading.Event()
    release = threading.Event()

    def hold_server_lock():
        with _SERVER_LOCK:
            acquired.set()
            release.wait(30)

    thread = threading.Thread(target=hold_server_lock)
    thread.start()
    assert acquired.wait(5)
    job = None
    key = 'thread-lock-regression'
    try:
        job_fn = manager.make_job_fn(_locked_callback, progress=True)
        job = manager.call_job_fn(key, job_fn, ['result'], {})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and key not in manager.handle:
            time.sleep(0.05)
        assert manager.handle.get(key) == 'result', 'worker inherited a locked server mutex'
        assert manager.get_progress(key) == ['completed']
    finally:
        if job:
            manager.terminate_job(job)
        release.set()
        thread.join(timeout=5)
        manager.handle.close()


def test_spawned_background_callback_can_be_cancelled(monkeypatch, tmp_path):
    monkeypatch.setattr('scripts.webapp_dash.app.tempfile.gettempdir', lambda: str(tmp_path))
    manager = _background_callback_manager()
    key = 'cancel-regression'
    job = None
    try:
        job_fn = manager.make_job_fn(_waiting_callback, progress=True)
        job = manager.call_job_fn(key, job_fn, [], {})
        deadline = time.monotonic() + 10
        progress = None
        while time.monotonic() < deadline and not progress:
            progress = manager.get_progress(key)
            if not progress:
                time.sleep(0.05)
        assert progress == ['waiting']
        assert manager.job_running(job)
        manager.terminate_job(job)
        assert not manager.job_running(job)
        assert key not in manager.handle
    finally:
        if job:
            manager.terminate_job(job)
        manager.handle.close()
