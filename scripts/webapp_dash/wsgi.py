"""Production WSGI entry point for the Dash application.

Use ``scripts.webapp_dash.wsgi:server`` with a WSGI process manager such as
Gunicorn. Keeping app creation here makes development and remote deployment
use the same Dash layout and callbacks.
"""

from scripts.webapp_dash.app import create_app

app = create_app()
server = app.server
