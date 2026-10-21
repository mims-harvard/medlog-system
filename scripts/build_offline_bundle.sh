#!/usr/bin/env bash
#
# Build a self-contained offline bundle for hosts that cannot reach a container
# registry: the container images plus the source tree needed to run them.
#
# Produces one archive containing:
#   images.tar          - every image the selected services need
#   medlog-telemetry/   - the repo, including ClickStack's .env and ClickHouse configs
#   install.sh          - loads the images and starts the stack
#   INSTALL.md          - the same steps, written out
#
# Usage:
#   scripts/build_offline_bundle.sh
#   scripts/build_offline_bundle.sh --full
#   scripts/build_offline_bundle.sh --platform linux/arm64 --output /tmp/bundle.tar.gz

set -euo pipefail

PLATFORM="linux/amd64"
OUTPUT=""

# The ingest path: event-receiver -> medlog-otelcol -> ch-server. Enough for every test
# in the README. `app` (HyperDX) and `db` (Mongo) only serve the web UI and are by far
# the largest images, so they are opt-in via --full.
SERVICES="ch-server ch-ddl-init medlog-otelcol event-receiver"

usage() { sed -n '3,17p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --platform) PLATFORM="$2"; shift 2 ;;
        --output|-o) OUTPUT="$2"; shift 2 ;;
        --full) SERVICES="$SERVICES app db"; shift ;;
        --help|-h) usage 0 ;;
        *) echo "unknown option: $1" >&2; usage 2 ;;
    esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

COMPOSE="docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml"
SLUG="$(echo "$PLATFORM" | tr '/' '-')"

for schema in \
    spec/openapi.yaml \
    spec/semconv/medlog.yaml \
    spec/semconv/manifest.yaml \
    spec/semantic_schemas/gen-ai-system-instructions.json \
    spec/semantic_schemas/gen-ai-retrieval-documents.json \
    spec/semantic_schemas/gen-ai-output-messages.json \
    spec/semantic_schemas/gen-ai-input-messages.json \
    spec/semantic_schemas/LICENSE; do
    if [ ! -f "$schema" ]; then
        echo "error: required schema bundle file is missing: $REPO/$schema" >&2
        echo "       run: git submodule update --init --recursive spec" >&2
        exit 1
    fi
done

if [ ! -f ClickStack/.env ]; then
    echo "error: ClickStack/.env is missing - the submodule is not initialized." >&2
    echo "       run: git submodule update --init --recursive" >&2
    exit 1
fi

[ -n "$OUTPUT" ] || OUTPUT="$REPO/dist/medlog-offline-$SLUG.tar.gz"
mkdir -p "$(dirname "$OUTPUT")"

# MEDLOG_API_KEY only has to be non-empty here: the compose file marks it required, but
# nothing in this script starts a container. HYPERDX_API_KEY is optional and unset in
# ClickStack/.env; exporting it empty keeps Compose from warning on every call.
export MEDLOG_API_KEY="${MEDLOG_API_KEY:-bundle-placeholder}"
export HYPERDX_API_KEY="${HYPERDX_API_KEY:-}"

# Ask Compose what each service resolves to, so the image list cannot drift from the
# compose files. `config --images <service>` is not usable here - it also returns the
# images of that service's dependencies. event-receiver carries a `build:` section
# instead of an `image:`, and Compose names such an image <project>-<service>.
BUILT_IMAGE=""
PULL_IMAGES=""
while read -r kind image; do
    case "$kind" in
        build) BUILT_IMAGE="$image" ;;
        pull)
            case " $PULL_IMAGES " in *" $image "*) continue ;; esac
            PULL_IMAGES="$PULL_IMAGES $image"
            ;;
    esac
done <<EOF
$($COMPOSE config --format json 2>/dev/null | python3 -c '
import json, sys

config = json.load(sys.stdin)
project = config.get("name", "")
for service in sys.argv[1:]:
    spec = config["services"][service]
    image = spec.get("image") or "%s-%s" % (project, service)
    print("build" if spec.get("build") else "pull", image)
' $SERVICES)
EOF

if [ -z "$BUILT_IMAGE" ]; then
    echo "error: could not resolve the event-receiver image name from compose config" >&2
    exit 1
fi

echo "==> platform: $PLATFORM"
echo "==> services: $SERVICES"
echo "==> pulling:$PULL_IMAGES"

for image in $PULL_IMAGES; do
    docker pull --platform "$PLATFORM" "$image"
done

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
# Name the staging directory as it should appear inside the archive, so no tar
# --transform is needed (BSD tar on macOS does not have it).
BUNDLE_DIR="medlog-offline-$SLUG"
mkdir -p "$STAGE/$BUNDLE_DIR"

# Build from a minimal context. The repo root is ~600 MB (almost all of it the
# ClickStack submodule's git objects) and the Dockerfile needs only these two paths.
CONTEXT="$STAGE/context"
mkdir -p "$CONTEXT"
rsync -a --exclude '.git' spec "$CONTEXT/"
rsync -a --exclude '.venv' --exclude '__pycache__' --exclude 'data' --exclude '.env' \
    event-receiver "$CONTEXT/"

echo "==> building $BUILT_IMAGE"
docker build --platform "$PLATFORM" \
    -f "$CONTEXT/event-receiver/Dockerfile" \
    -t "$BUILT_IMAGE" \
    "$CONTEXT"

# `docker save --platform` needs a recent Docker; without it the daemon exports whatever
# variant the tag resolves to, which the verification step below catches.
SAVE_ARGS=""
if docker save --help 2>&1 | grep -q -- '--platform'; then
    SAVE_ARGS="--platform $PLATFORM"
fi

echo "==> saving images"
docker save $SAVE_ARGS $PULL_IMAGES "$BUILT_IMAGE" -o "$STAGE/$BUNDLE_DIR/images.tar"

# Verify from inside the archive rather than trusting --platform: when the local image
# store already holds another architecture for a tag, `docker pull --platform` can leave
# that one in place, and the bundle would only fail once it reached the target host.
echo "==> verifying architectures"
python3 - "$STAGE/$BUNDLE_DIR/images.tar" "$PLATFORM" <<'PY'
import json, sys, tarfile

tar_path, platform = sys.argv[1], sys.argv[2]
want_os, want_arch = platform.split("/", 1)
tar = tarfile.open(tar_path)
names = tar.getnames()


def blob(digest):
    return json.load(tar.extractfile("blobs/sha256/" + digest.split(":")[1]))


checked = 0
ok = True


def check(name, config):
    global checked, ok
    image_os, arch = config.get("os"), config.get("architecture")
    # buildx attaches provenance/SBOM manifests alongside the real image. They declare
    # no usable platform - either the literal "unknown" or nothing at all - and are
    # metadata rather than runnable images, so they are not checked.
    if image_os in (None, "unknown") or arch in (None, "unknown"):
        return
    print("    %-55s %s/%s" % (name, image_os, arch))
    checked += 1
    ok = ok and image_os == want_os and arch == want_arch


if "index.json" in names:  # OCI layout (containerd image store)
    for entry in json.load(tar.extractfile("index.json"))["manifests"]:
        annotations = entry.get("annotations") or {}
        name = annotations.get("io.containerd.image.name") or annotations.get(
            "org.opencontainers.image.ref.name", "?"
        )
        manifest = blob(entry["digest"])
        for child in manifest.get("manifests", [{"digest": entry["digest"]}]):
            child_manifest = manifest if "config" in manifest else blob(child["digest"])
            check(name, blob(child_manifest["config"]["digest"]))
else:  # legacy docker save layout
    for entry in json.load(tar.extractfile("manifest.json")):
        check(", ".join(entry.get("RepoTags") or ["?"]), json.load(tar.extractfile(entry["Config"])))

if not checked:
    sys.exit("    ERROR: no runnable images found in the archive")
if not ok:
    sys.exit("    ERROR: archive contains images that are not %s" % platform)
print("    %d images, all %s" % (checked, platform))
PY

# The source tree travels with the images: a GitHub ZIP download omits the ClickStack
# submodule, and without its .env and ClickHouse configs the stack cannot start.
echo "==> staging source"
rsync -a \
    --exclude '.git' --exclude '.volumes' --exclude 'dist' --exclude 'output' \
    --exclude 'paper' --exclude '__pycache__' --exclude '.venv' --exclude '.DS_Store' \
    --exclude 'event-receiver/data' \
    "$REPO/" "$STAGE/$BUNDLE_DIR/medlog-telemetry/"

cat > "$STAGE/$BUNDLE_DIR/install.sh" <<INSTALL
#!/usr/bin/env bash
# Load the bundled images and start the stack. Run from the extracted bundle directory.
set -euo pipefail

cd "\$(dirname "\$0")"

if [ -z "\${MEDLOG_API_KEY:-}" ]; then
    echo "Set MEDLOG_API_KEY first, e.g.:" >&2
    echo "  export MEDLOG_API_KEY=change-me" >&2
    exit 1
fi

echo "==> loading images"
docker load -i images.tar

cd medlog-telemetry
echo "==> starting: $SERVICES"
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml \\
    up -d --no-build $SERVICES

echo
echo "Check that the schema applied (should end with 'medlog DDLs applied'):"
echo "  docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml logs ch-ddl-init"
INSTALL
chmod +x "$STAGE/$BUNDLE_DIR/install.sh"

cat > "$STAGE/$BUNDLE_DIR/INSTALL.md" <<INSTALL
# MedLog offline bundle

Built for **$PLATFORM**. Contains the container images, the full source tree
(including ClickStack's \`.env\` and ClickHouse configs, which a GitHub ZIP download
omits), and this install script.

Services in this bundle: \`$SERVICES\`

## Install

\`\`\`bash
export MEDLOG_API_KEY=change-me
./install.sh
\`\`\`

Or manually:

\`\`\`bash
docker load -i images.tar
cd medlog-telemetry
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml up -d --no-build $SERVICES
\`\`\`

\`--no-build\` makes a missing image fail loudly instead of triggering a network build.

## Verify

\`\`\`bash
docker compose -f ClickStack/docker-compose.yml -f docker-compose.yml logs ch-ddl-init
\`\`\`

Must end with \`medlog DDLs applied\`. If \`ch-server\` shows \`Restarting\`, its
ClickHouse config files are missing - they ship in this bundle under
\`medlog-telemetry/ClickStack/docker/clickhouse/local/\`.

\`\`\`bash
python3 scripts/generate_medlog_events.py --runs 5
scripts/medlog_summary_stats.sh
\`\`\`
INSTALL

echo "==> compressing"
tar czf "$OUTPUT" -C "$STAGE" "$BUNDLE_DIR"

if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$OUTPUT" > "$OUTPUT.sha256"
else
    shasum -a 256 "$OUTPUT" > "$OUTPUT.sha256"
fi

echo
echo "bundle:  $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
echo "sha256:  $(cut -d' ' -f1 "$OUTPUT.sha256")"
echo
echo "On the target host:"
echo "  tar xzf $(basename "$OUTPUT")"
echo "  cd medlog-offline-$SLUG"
echo "  export MEDLOG_API_KEY=change-me"
echo "  ./install.sh"
