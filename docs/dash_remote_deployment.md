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
uv run reacnet-scope-web-dash --host 0.0.0.0 --port 8060
```

Use this only behind a VPN or a trusted LAN firewall. Do not expose the Dash
development server directly to the public internet.

## Production service

The repository provides `scripts.webapp_dash.wsgi:server` for a standard WSGI
process manager. On the remote host, run:

```bash
uv run --with gunicorn gunicorn \
  --bind 127.0.0.1:8060 \
  --workers 2 \
  --threads 4 \
  --timeout 180 \
  scripts.webapp_dash.wsgi:server
```

Put Caddy or Nginx in front of port `8060` for TLS, login control and request
limits. Start with two workers only after confirming that the remote host has
enough memory for the data caches. A single worker with four threads keeps the
in-memory indices warmest; two or more workers are useful when several users
run long parsing jobs at the same time.

For secure personal access without opening a firewall, create an SSH tunnel on
the client computer:

```bash
ssh -N -L 8060:127.0.0.1:8060 <user>@<remote-host>
```

Then open `http://127.0.0.1:8060` locally.

## Performance notes

- A trajectory frame index is only frame number plus byte offsets, never a
  copy of the trajectory coordinates. It is persisted by default in
  `.reacnet_scope_cache` beside the `.lammpstrj` file. Set
  `REACNET_SCOPE_CACHE_DIR` to place it on a different mounted cache volume.
  Build it explicitly before interactive use when the trajectory is very large:

  ```bash
  uv run reacnet-scope-build-trajectory-index /data/run.lammpstrj
  ```

- Repeated time-evolution requests reuse a file-versioned species catalog in
  the server process; changing only target species avoids rebuilding that
  catalog.
- Send several target species in one request. The selected SMILES are read in
  one pass and converted into multiple curves together.
- Keep the `.species`, `.reactionabcd`, route and trajectory files on local
  NVMe storage on the remote host whenever possible. A network filesystem can
  dominate total runtime even with more CPU cores.
- Use the time-evolution `下采样` setting to reduce browser payload size while
  retaining the full calculation on the server.
