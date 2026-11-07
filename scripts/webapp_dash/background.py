"""Diskcache workers that are safe to launch from a threaded web server."""

from typing import Any

from dash import DiskcacheManager


class SpawnDiskcacheManager(DiskcacheManager):
    """Keep Dash's result/progress protocol without inheriting server threads."""

    def job_running(self, job: Any) -> bool:
        import psutil

        try:
            return super().job_running(job)
        except psutil.NoSuchProcess:
            # The worker can exit after Dash's pid_exists check and before its
            # Process/status lookup. A completed job is not an HTTP 500.
            return False

    def terminate_job(self, job: Any) -> None:
        import psutil

        try:
            super().terminate_job(job)
        except psutil.NoSuchProcess:
            # Likewise, canceling an already exited worker is a successful no-op.
            pass

    def call_job_fn(self, key: str, job_fn: Any, args: Any, context: Any) -> int:
        from multiprocess import get_context

        # Choose this worker's context only, leaving an embedding application's
        # global multiprocessing settings alone. Fork can inherit locked mutexes
        # and open handles from other server threads.
        process = get_context('spawn').Process(
            target=job_fn,
            args=(key, self._make_progress_key(key), args, context),
        )
        process.start()
        return process.pid
