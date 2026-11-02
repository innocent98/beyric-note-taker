# X-Forwarded-For trust through Next (finding, 2026-11-02)

FastAPI keys the login limit on `request.client.host`. With Next in front, FastAPI must trust Next
as a proxy (`--forwarded-allow-ips` includes `next`), and Next passes `X-Forwarded-For` through
unchanged (captured: `proxy-passthrough-stub.txt`; Next neither appends to it nor validates it).

**Residual risk.** Anything that can open a TCP connection to `next:3000` can choose the address
FastAPI rate-limits on. nginx is the only intended client, and nginx overwrites the header, but a
container on the same host can reach `next` too:

- Measured on Docker Desktop (engine 29.1.3), from a container on the `default` network:
  `172.28.1.10:3000` (next, on `front`) CONNECTED although the two networks are not shared;
  `db` on the internal `appdb` network and `web`'s `front` address (unpublished port) were blocked.
  Docker accepts traffic to a *published* container port from other networks.
- A forged `X-Forwarded-For: 203.0.113.66` sent that way showed up in FastAPI's access log as
  client `203.0.113.66`. The connection reached `next` as the `front` gateway (172.28.1.1), the same
  address nginx arrives from, because Docker masquerades traffic between bridge networks. So `next`
  cannot tell a hostile container from nginx by peer address; a wrapper that only believes the
  header from the gateway was tried and removed because it cannot work.
- Who this matters for: a compromised Vexa container (Chrome on untrusted meeting pages). It can
  already reach FastAPI directly, where the header is ignored (peer not trusted); through `next` it
  could reset the per-address login limit and the app's own 5-failures-in-15-minutes counter.
  nginx's 6 requests a minute limit is also bypassed on that path. It is mitigated by the long owner
  password the README requires.

**Mitigations in this change.** `next` sits on its own network `front` with `web` only, so Vexa
cannot resolve `next` by name and must know its fixed address. This is obscurity, not a boundary.

**Real fixes (decisions for the lead / Adebayo, none needs nginx):**
1. `next` with `network_mode: host`, bound to 127.0.0.1:8020 and reaching FastAPI on 127.0.0.1:8021:
   no container can connect to the host's loopback, and FastAPI sees Next as the bridge gateway it
   already trusts. Not testable on Docker Desktop; needs a Linux run.
2. A shared secret: Next adds `X-Proxy-Secret`, FastAPI honours `X-Forwarded-For` only when it is
   present (Python change, stream B's area). A compromised Vexa cannot read the secret.
3. Accept the risk for the pilot (single owner, long password) and do 1 or 2 before open sign-up.
