# stock-live-api

Local Flask API for `/api/quote` and `/api/radar`. If OpenD accepts a TCP connection on this machine, the process serves OpenD. Otherwise it serves Yahoo Finance. Each payload includes `data_source` (`FUTU_OPEND` or `YAHOO`) and `source_mode` (`OPEND` or `YAHOO`).

Vercel has no local OpenD, so the hosted app stays on Yahoo. Do not point the Futu SDK at a Funnel URL: OpenD is TCP, and Funnel is HTTPS.

## Tailscale Funnel

1. Start Futu OpenD on this same machine (default `127.0.0.1:11111`).
2. `pip install -r requirements.txt`
3. `python server.py` (listens on port 5000).
4. `tailscale funnel 5000`
5. In the Pages app, paste that HTTPS origin into `sniper_tunnel_url` (no trailing slash). The page probes `{origin}/api/radar`, then calls `{origin}/api/quote`.

Optional local override: `FUTU_HOST` and `FUTU_PORT`. An `http://` or `https://` value is ignored and the server keeps `127.0.0.1`.
