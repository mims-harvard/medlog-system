# Troubleshooting

Failure modes hit while deploying this stack, symptom first. Most of them fail in ways
that do not name their cause.

## Compose warns about unset variables, then `no port specified: :<empty>`

`ClickStack/.env` is missing. It defines every `HYPERDX_*` and image-tag variable both
compose files interpolate; with it absent, `ports: ${HYPERDX_API_PORT}:...` collapses to
`:`. The file ships inside the ClickStack submodule, and **a GitHub ZIP download does not
include submodule contents**.

```bash
git submodule update --init --recursive
```

On a host without registry or GitHub access, use an offline bundle instead — it carries
the file (see *Offline install* in the README).

## Image pulls fail with `net/http: TLS handshake timeout`

No route to `registry-1.docker.io`. Distinguish "blocked" from "the daemon can't use the
network it has":

```bash
curl -sS -m 15 -o /dev/null -w 'HTTP %{http_code}\n' https://registry-1.docker.io/v2/
```

`HTTP 401` is the good result: egress works, so the problem is daemon-side — usually a
proxy the shell has and `dockerd` does not (it is a systemd service and inherits nothing).
Give it `/etc/systemd/system/docker.service.d/http-proxy.conf` and restart Docker.

A timeout means the destination is dropped. Note that a working `dnf`/`yum` does not prove
general egress — a domain allowlist can permit the distro repos and nothing else. In that
case build an offline bundle on a machine that does have access.

## The bundle's images are the wrong architecture

`docker pull --platform linux/amd64` does not guarantee an amd64 image: when the local
image store already holds another architecture for that tag, the existing one can stay in
place, and `docker image inspect` reports the host-platform view. The failure only
surfaces on the target host.

`scripts/build_offline_bundle.sh` verifies by reading the image configs back out of the
archive it just wrote. Do not replace that check with a reading of the flag.

## `ch-server` restarts forever, logs end in a Poco XML stack trace

The stack frames are not the error. The line above them is:

```
Poco::Exception. Code: 1000, e.code() = 13, Access to file denied: /etc/clickhouse-server/config.xml
```

`e.code() = 13` is `EACCES`. ClickHouse runs as **uid 101** inside the container, so it
cannot traverse a home directory created mode `700`, nor read config files created `640` —
regardless of the bind mount being correct. `--tail=50` hides this line; use `head`:

```bash
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml logs ch-server | head -30
```

Confirm which directory blocks it — every component of the path must be `o+x`:

```bash
namei -l ~/medlog-telemetry-main/ClickStack/docker/clickhouse/local/config.xml
```

Fix, as the owning user (no sudo needed):

```bash
chmod o+x ~
find ~/medlog-telemetry-main -path '*/.volumes' -prune -o -user "$(id -un)" -type d -exec chmod o+rx {} +
find ~/medlog-telemetry-main -path '*/.volumes' -prune -o -user "$(id -un)" -type f -exec chmod o+r {} +
```

Without the `-prune`, `find` prints `Permission denied` for every directory under
`.volumes/ch_data`. That is noise: those are ClickHouse's own data directories, owned by
uid 101 and mode 700, and were never candidates for the chmod. Their existence is in fact
a good sign — it means the entrypoint ran as root, created the data directory, handed it
to the `clickhouse` user, and only then failed reading the config as uid 101.

If `getenforce` returns `Enforcing`, SELinux is a second, independent cause: bind mounts
need `:z` labels or every mounted file returns EACCES.

Containers must be recreated, not restarted — the running container still has the old
mount:

```bash
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml up -d --force-recreate ch-server medlog-otelcol
```

## `Unknown table medlog.medlog_events`

`ch-ddl-init` is a one-shot container that waits ~120s for ClickHouse and then gives up.
If `ch-server` was crash-looping at the time, the schema was never applied and nothing
says so afterwards. Re-run it once ClickHouse is genuinely up:

```bash
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml up -d --force-recreate ch-ddl-init
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml logs ch-ddl-init | tail -5
```

It must end with `medlog DDLs applied`.

## Events return 201 but never reach ClickHouse

`event-receiver` acknowledges a POST and then forwards it over OTLP to `medlog-otelcol`.
If the collector is down — it restarts on failure when its ClickHouse exporter cannot
reach `ch-server` — those events are lost, and the receiver keeps returning 201 throughout.
Check that all three of `ch-server`, `medlog-otelcol` and `event-receiver` say `Up`, not
`Restarting`, before reading anything into ingest counts.

## `medlog-otelcol` restarts on a loop

Almost always downstream of `ch-server` being down; its ClickHouse exporter fails to start
and the collector exits. Fix ClickHouse first, then recreate the collector.

## `df` says the disk is full but `du` does not account for it

An unprivileged `du` silently skips every directory it cannot read, so the total can be
wildly short. Re-run it with `sudo`. If the gap remains, look for deleted-but-open files —
space is not returned until the holding process closes them, so `df` counts them and `du`
cannot see them at all:

```bash
sudo du -xh / --max-depth=2 2>/dev/null | sort -h | tail -20
sudo lsof -nP +L1 2>/dev/null | head -20
```

## Where `MEDLOG_API_KEY` and `MEDLOG_API_URL` come from

The receiver compares the `X-API-Key` header against `MEDLOG_API_KEY` **in its own
environment** ([app.py](event-receiver/app.py)). An empty value is not "no auth" — every
request is rejected with 401.

Under Compose there is no default. `docker-compose.yml` marks the variable required
(`${MEDLOG_API_KEY:?...}`) and takes it from the shell running the command, so *every*
compose invocation needs it exported, not just `up`. A new SSH session without it cannot
run `ps`, `logs`, or `exec`.

The value is baked into the container when it is **created**, so changing the key needs a
recreate; exporting a new value and restarting does nothing:

```bash
export MEDLOG_API_KEY=$(openssl rand -hex 16)
export MEDLOG_API_URL=http://localhost:5000
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml up -d --force-recreate event-receiver
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' -H "X-API-Key: $MEDLOG_API_KEY" "$MEDLOG_API_URL/docs"
```

`HTTP 200` confirms the pair. The key is a shared secret between the receiver and its
producers and is not registered anywhere, so any value works as long as both sides agree.
Persist both exports in `~/.bashrc`, or the next shell breaks every compose command.

**Why it can look like there is a built-in default.** Run outside Docker, `app.py` calls
`load_dotenv()`, which finds `event-receiver/.env` — the file `.env.example` tells you to
copy, carrying `MEDLOG_API_KEY=change-me`. That is a developer-machine convenience, not a
default the deployed stack has.

It can also leak into a container: the Dockerfile copies `event-receiver/` wholesale and
there is no `.dockerignore`, so an image built on a machine that has a `.env` bakes it in,
and `load_dotenv()` then supplies that key whenever Compose does not set the variable
(python-dotenv does not override an existing environment variable, so Compose still wins
when it provides one). `scripts/build_offline_bundle.sh` excludes `.env` from its build
context so a bundle can never ship someone's local key.

**Receiver URL.** `http://localhost:5000` from the host itself. From another container on
the compose network it is `http://event-receiver:5000` — `localhost` there is that
container, not the receiver. From off-host it needs the published port to be reachable,
which on a private VPC usually means an SSH tunnel.

## `medlog_tool.py` records events but sends none

Three silent failures in [scripts/medlog_tool.py](scripts/medlog_tool.py), all of which
look like a working integration:

- If `api_url` is not passed and `MEDLOG_API_URL` is unset, `_try_push_log` returns
  immediately. Events accumulate in `medlog.logs` in memory and are never posted, with no
  error.
- `_push_log` catches every exception and prints `Warning: Failed to push Medlog event…`.
  Nothing raises, so a caller that redirects stdout sees a clean run.
- `set_target(...)` must be called before the first `medlog(...)` call, or
  `target_identity` is `None` and the receiver rejects the event against the OpenAPI
  schema.

Confirm events actually landed by run id rather than by absence of errors:

```bash
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml exec -T ch-server \
  clickhouse-client --query "SELECT EventType, RunId FROM medlog.medlog_events ORDER BY Timestamp DESC LIMIT 10"
```

## Every event returns 401, and the client does not fail

`medlog_tool.py` prints one warning per event and **still exits 0**, so a wrapper that
checks only the exit status sees a clean run:

```
Warning: Failed to push Medlog event 4bf39fc6-... to /event/inference-start: HTTP Error 401: Unauthorized
```

The receiver compares `X-API-Key` against its own `MEDLOG_API_KEY`, and an empty expected
value rejects everything ([app.py:62](event-receiver/app.py:62)). Compare the two sides
directly — this is the check that settles it:

```bash
echo "shell:     [$MEDLOG_API_KEY]"
docker inspect clickstack-event-receiver-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -i medlog
```

Three distinct causes produce the same 401:

1. **The compose file never passes the variable.** If `grep -n MEDLOG_API_KEY
   docker-compose.yml` prints nothing, the tree predates the commit that added it and no
   amount of exporting or `.env` editing will help — there is nothing referencing the
   variable. Add it under `event-receiver:` → `environment:`, at six spaces:

   ```
         MEDLOG_API_KEY: ${MEDLOG_API_KEY}
   ```

2. **The container was created before the variable was set.** The environment is baked in
   at creation, so `--force-recreate` is required; `restart` keeps the old value.

3. **The key moved.** `export MEDLOG_API_KEY=$(openssl rand -hex 16)` produces a *new* key
   every time it runs. Running it again in another tab, or recreating the receiver from a
   different shell, guarantees a mismatch. Pin the value in `~/.bashrc` or
   `ClickStack/.env` — the latter is Compose's project-directory env file, because the
   project directory is the directory of the **first** `-f` argument.

`MEDLOG_API_URL` being empty inside the container is *not* a fault. It is client-side —
the address producers post to — and the receiver has no use for it.

## The client times out but the server logged `201 Created`

```
Warning: Failed to push Medlog event c6d7f5b7-... to /event/inference-start: timed out
```

while the receiver's own log shows:

```
Failed to export logs batch due to timeout, max retries or shutdown
INFO: 172.18.0.1:42838 - "POST /event/inference-start HTTP/1.1" 201 Created
```

The receiver blocks while its OTLP exporter retries a failing export, so the response
arrives after `medlog_tool.py`'s 5-second timeout. **A client-side timeout does not mean
the event was rejected** — check the receiver's log before concluding anything, and treat
a timeout as a signal to look downstream at the collector.

## `Failed to resolve 'medlog-otelcol' ([Errno -2] Name or service not known)`

This reads like a networking problem and usually is not. Docker's embedded DNS only
resolves names of *running* containers, so a crash-looping collector disappears from DNS
and its clients report a resolution failure. Check the collector's state before touching
any network config:

```bash
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml ps
docker inspect -f '{{.Name}} -> {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' \
  clickstack-event-receiver-1 clickstack-medlog-otelcol-1
```

If both containers list the same network (`clickstack_internal`) and the name still will
not resolve, the target is restarting — fix that, and resolution returns on its own.

Events accepted during such an outage are **lost**: the receiver returns 201, the exporter
retries, gives up, and drops the batch. Ingest counts after an outage cannot be trusted.

## The collector crash-loops on its persistent queue

```
Error: cannot start pipelines: failed to start "clickhouse" exporter:
open /var/lib/otelcol/file_storage/exporter_clickhouse__logs: permission denied
```

The exporter's `sending_queue` is backed by the `file_storage/medlog` extension
([collector/otelcol-config.yaml:12](collector/otelcol-config.yaml:12)), which must
**write** to its directory. That directory is a bind mount, and **Docker creates a missing
bind-mount source as `root:root` mode 755** — while `otelcol-contrib` runs as uid 10001.
Reading works, writing does not, and the collector exits before serving anything.

```bash
ls -ld ~/medlog-telemetry-main/.volumes/medlog-otelcol-queue
```

With sudo, hand it to the collector's user:

```bash
sudo chown -R 10001:10001 ~/medlog-telemetry-main/.volumes/medlog-otelcol-queue
```

Without sudo, the directory cannot be chowned *or* deleted — `.volumes` is itself
root-owned, so there is no write permission inside it. Repoint the mount at a directory
the user does own:

```bash
mkdir -p ~/otelcol-queue && chmod 777 ~/otelcol-queue
sed -i 's#\.\./\.volumes/medlog-otelcol-queue#/home/<user>/otelcol-queue#' docker-compose.yml
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml up -d --force-recreate medlog-otelcol
```

This generalises: any container running as a non-root user needs its bind-mount
directories to exist with the right ownership *before* first start, or Docker's
root-owned default silently blocks it. The same class of failure hits `ch-server`
(uid 101) through file permissions rather than directory ownership.

## Deploying an out-of-date copy of the repository

A tree extracted from a ZIP is pinned to whatever the default branch held that day, and
the failures it produces do not mention the version. On one EC2 host the copy was missing:

| Missing | Symptom |
|---|---|
| `ClickStack/.env` (submodule) | `no port specified: :<empty>` |
| `MEDLOG_API_KEY` in `docker-compose.yml` | every event 401s, receiver looks healthy |
| `EVENT_ID_DB_PATH`, `PORT` | none immediately; dedup DB lands on a container-local path |

Before debugging anything subtle on a host that was set up by copying files, diff its
`docker-compose.yml` against the current one. An offline bundle built by
`scripts/build_offline_bundle.sh` carries a coherent tree and avoids the whole category.

## macOS: `event-receiver` cannot bind port 5000

ControlCenter (AirPlay Receiver) listens on 5000 and takes it the moment Docker frees it.
Turn it off under **System Settings → General → AirDrop & Handoff → AirPlay Receiver**, or
publish a different host port.

## Benign

`WARN The "HYPERDX_API_KEY" variable is not set. Defaulting to a blank string.` is
expected — the variable is optional and absent from `ClickStack/.env`. Exporting it empty
silences the warning:

```bash
export HYPERDX_API_KEY=
```

# Deployment log: restricted EC2 host, 4-5 September 2026

The order in which these surfaced, since each one masked the next. Every failure below
reported something other than its cause.

1. **`no port specified: :<empty>`** — `ClickStack/.env` absent; the tree came from a ZIP,
   which omits submodule contents.
2. **`net/http: TLS handshake timeout` on every image** — no route to Docker Hub. `dnf`
   worked, which proved only that the distro repos were allowlisted. Resolved by building
   an offline bundle elsewhere and transferring it.
3. **Bundle images came out arm64** despite `--platform linux/amd64`, caught by verifying
   the image configs inside the archive rather than trusting the flag.
4. **`ch-server` restarting, 232 times** — `Access to file denied:
   /etc/clickhouse-server/config.xml`. The home directory was mode 700 and the repo
   directories 750; uid 101 could not traverse them. Fixed with `chmod o+x ~` plus
   `o+rX` across the tree.
5. **No tables** — `ch-ddl-init` had run and given up hours earlier while ClickHouse was
   looping. Re-running it applied all four DDLs.
6. **Disk at 100%** (199G/200G). Docker accounted for 1.4G; an unprivileged `du` found
   51G of 199G, the rest being in directories it could not read.
7. **Every event 401** — the host's `docker-compose.yml` predated the commit that passes
   `MEDLOG_API_KEY`, so the container's key was empty and the receiver rejected everything.
8. **Client timeouts against a receiver returning 201** — the receiver was blocking on a
   failing OTLP export.
9. **`Failed to resolve 'medlog-otelcol'`** — not a network fault; the collector was
   crash-looping and had dropped out of Docker's DNS.
10. **Collector crash-loop** — `permission denied` creating its persistent-queue file in a
    bind-mount directory Docker had created as `root:root`.

The recurring theme is worth stating plainly: **on Linux hosts, bind mounts and container
users are the dominant failure mode**, and each instance reports as something unrelated —
an XML parse trace, a DNS error, a client timeout.
