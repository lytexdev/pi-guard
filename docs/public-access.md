# Dashboard from the internet

The safest way from outside stays the home VPN: nothing but one UDP port is open.
If you still want `https://guard.example.org`, follow all of this.

**Before you start:** set up two-factor codes with recovery codes or a passkey, and a strong
password. The security analysis under Logs warns if a public dashboard has neither.

## Settings

```ini
PI_GUARD_DASHBOARD_PUBLIC_URL=https://guard.example.org
PI_GUARD_DASHBOARD_TRUSTED_PROXIES=127.0.0.1
PI_GUARD_DASHBOARD_TLS=false
```

With a public URL, requests for other host names are refused (421), which stops scanners and
DNS rebinding; addresses of the home network keep working. Cookies are `Secure` and
`SameSite=Strict`. Only list your own proxy as trusted: its `X-Forwarded-For` header decides
which address is rate limited and banned.

Keep `PI_GUARD_DASHBOARD_TLS=true` if the proxy is on another machine, so that the way
from the proxy to the Pi is encrypted too.

## Caddy (certificates included)

```text
guard.example.org {
    reverse_proxy 127.0.0.1:8443 {
        flush_interval -1
    }
}
```

## nginx

```nginx
server {
    listen 443 ssl;
    server_name guard.example.org;
    # ssl_certificate ...
    client_max_body_size 60m;
    location / {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;       # live video and sound
        proxy_read_timeout 1h;
    }
}
```

Set the header to `$remote_addr` as shown: the proxy then replaces whatever a client sends.

## What protects you

Login rate limits, automatic bans, honeypots, CSRF and origin checks, a strict Content Security
Policy and `noindex` are always active. Add a firewall rule that only lets your proxy reach
port 8443.

A public login page will be attacked. pi-guard controls cameras and microphones in your home,
so weigh that against the convenience.
