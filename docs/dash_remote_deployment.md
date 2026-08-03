# Dash Remote Deployment

Run the Dash service on the computer that has the fastest CPUs and local access
to the simulation data. The data directory selected in the UI is resolved by
the server process, so it must be mounted on that computer, not just on the
browser machine.

## Prepare the host

```bash
git clone <repository-url> reacnet-scope
cd reacnet-scope
uv sync --extra web
```

For a quick private-network trial, bind the existing entry point to the host
interface:

```bash
REACNET_SCOPE_ALLOWED_ROOTS="/home/$USER:/media/$USER:/data:/mnt:/scratch" \
  uv run reacnet-scope-web-dash --host 0.0.0.0 --port 8060
```

`REACNET_SCOPE_ALLOWED_ROOTS` controls which server directories are visible in
the Dash directory browser. It is a colon-separated list and must include the
actual mount point used by the simulation data. Restart the Dash or Gunicorn
process after changing it. Setting it replaces the built-in defaults rather
than appending to them. The legacy `run_web.sh` entry point serves a
different UI; use `run_dash.sh` or `reacnet-scope-web-dash` for this interface.

Use this only behind a VPN or a trusted LAN firewall. Do not expose the Dash
development server directly to the public internet.

## Production service

The repository provides `scripts.webapp_dash.wsgi:server` for a standard WSGI
process manager. On the remote host, run:

```bash
uv run --with gunicorn gunicorn \
  -c deploy/gunicorn.conf.py \
  scripts.webapp_dash.wsgi:server
```

Use one worker with multiple threads so all requests share the same in-memory
task state while persistent indexes live in `REACNET_SCOPE_CACHE_DIR`. Example
Gunicorn, systemd, environment and Nginx configurations are provided in
`deploy/`. Nginx terminates HTTPS and applies Basic Auth; systemd mounts data
paths read-only and grants writes only to the cache directory.

For secure personal access without opening a firewall, create an SSH tunnel on
the client computer:

```bash
ssh -N -L 8060:127.0.0.1:8060 <user>@<remote-host>
```

Then open `http://127.0.0.1:8060` locally.

## Performance notes

Set the cache directory, then prepare the dataset before starting Dash:

```bash
export REACNET_SCOPE_CACHE_DIR=/srv/reacnet-cache
```

Build or resume every required index and publish the dataset manifest:

```bash
uv run reacnet-scope-prepare /srv/reacnet-data/case
uv run reacnet-scope-prepare /srv/reacnet-data/case --status
```

Prepare the trajectory frame index when needed:

```bash
uv run reacnet-scope-prepare /srv/reacnet-data/case --trajectory-only
```

The preparation command supports event, trajectory, composition and legacy
Route targets through `--clear`, `--rebuild` and the corresponding `--*-only`
options. Ctrl+C preserves committed checkpoints. The same event, trajectory
and composition jobs can be launched from **Manage Data → Data preparation**;
Dash runs them in a separate background process and polls the shared index
checkpoints.

- A trajectory index is a SQLite database containing only timestep and byte
  offsets, never a copy of coordinates. Dash opens it with SQLite `mode=ro`
  and reads only selected frame ranges.

  ```bash
  uv run reacnet-scope-prepare /data/case --trajectory-only
  ```

- Trajectory indexes are SQLite databases under
  `$REACNET_SCOPE_CACHE_DIR/datasets/<dataset-id>/`. Partial builds use a `.building` file and
  resume from their last committed source offset. Missing, stale, or invalid
  indexes cause a fast query error. Query callbacks never build them
  implicitly; only an explicit management-page action or
  `reacnet-scope-prepare` command starts a build.
- Reaction-event search prefers a complete schema-1 `.timeline.h5` and falls
  back to `.reactionevent.csv` only when the native source is absent.
  Molecular Evidence (native or `.molecules.csv`) adds atom, bond, and
  physical-timestep evidence.
- `GET /api/health` reports service uptime, cache writability, and allowed data
  roots for monitoring.

- Repeated time-evolution requests reuse a file-versioned species catalog in
  the server process; changing only target species avoids rebuilding that
  catalog.
- Send several target species in one request. The selected SMILES are read in
  one pass and converted into multiple curves together.
- Keep the `.species`, `.reactionabcd`, `.timeline.h5`, `.reactionevent.csv`, `.molecules.csv`
  and trajectory files on local
  NVMe storage on the remote host whenever possible. A network filesystem can
  dominate total runtime even with more CPU cores.
- Use the time-evolution `下采样` setting to reduce browser payload size while
  retaining the full calculation on the server.
