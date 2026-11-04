# API

For your own scripts and home automation. Create a token in the dashboard under
**Settings › API**, either read-only or with control rights. It is shown once.

```bash
curl -H "Authorization: Bearer pg_..." https://192.168.178.109:8443/api/v1/status
```

With the local certificate add `--cacert pi-guard-ca.crt` (from `/ca.crt`).

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/status` | Mode, cameras, open alerts |
| GET | `/api/v1/cameras` | All cameras with state |
| GET | `/api/v1/cameras/{id}/snapshot` | Current picture (JPEG) |
| GET | `/api/v1/events?limit=50` | Latest events |
| GET, POST | `/api/v1/mode` | Read, or set `{"mode": "HOME"}`, `AWAY` or `AUTO` |
| GET | `/api/v1/addons` | Add-ons and plugins |
| GET | `/api/v1/services` | Service watcher |
| GET | `/api/v1/smarthome` | Smart home devices |
| POST | `/api/v1/smarthome/{id}` | `{"on": true}`, `{"on": false}`, `{}` toggles |
| POST | `/api/v1/alarm` | `{"on": true}` or `{"on": false}` |
| GET | `/api/v1/openapi.json` | This list as OpenAPI |

Add-ons offer their own routes below `/api/plugins/<id>/`, which accept the same tokens.

Tokens only open the API, never the rest of the dashboard. Read-only tokens get 403 on anything
but GET. Wrong tokens count as failed logins and lead to bans. Only hashes are stored, every
control call is in the audit log, and tokens can be withdrawn any time.
