"""Gunicorn defaults for one cache-owning ReacNet Scope process."""

import os

bind = os.environ.get("REACNET_SCOPE_BIND", "127.0.0.1:8060")
workers = 1
threads = int(os.environ.get("REACNET_SCOPE_THREADS", "4"))
worker_class = "gthread"
timeout = int(os.environ.get("REACNET_SCOPE_REQUEST_TIMEOUT", "300"))
graceful_timeout = 120
keepalive = 5
max_requests = int(os.environ.get("REACNET_SCOPE_MAX_REQUESTS", "2000"))
max_requests_jitter = 100
preload_app = False
accesslog = "-"
errorlog = "-"
capture_output = True
loglevel = os.environ.get("REACNET_SCOPE_LOG_LEVEL", "info")

