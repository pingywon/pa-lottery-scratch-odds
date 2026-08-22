#!/usr/bin/env bash
# Cut a release: build, verify, push to Docker Hub, git-tag, redeploy.
#
#   ./deploy/release.sh v1.7.0
#
# Must run from a checkout that has images/ populated (it is gitignored, so a
# fresh clone will not have it -- run scrape.py once first).
set -euo pipefail

VERSION="${1:?usage: release.sh vX.Y.Z}"
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must look like v1.7.0"; exit 1; }

IMAGE="${IMAGE:-pingywon/pa-lottery-scratch-odds}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

[ -d images ] && [ -n "$(ls -A images 2>/dev/null)" ] || {
    echo "images/ is empty -- run 'python3 scrape.py' first (images/ is gitignored)"; exit 1; }

echo "==> building $IMAGE:$VERSION"
docker build -t "$IMAGE:$VERSION" -t "$IMAGE:latest" .

echo "==> smoke-testing the image before it goes anywhere"
cid=$(docker run -d -p 127.0.0.1::80 "$IMAGE:$VERSION")
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
port=$(docker port "$cid" 80/tcp | head -1 | sed 's/.*://')
ok=""
for _ in $(seq 1 20); do
    if curl -fsS --max-time 3 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
done
[ -n "$ok" ] || { echo "smoke test FAILED"; docker logs --tail 40 "$cid"; exit 1; }
games=$(curl -fsS "http://127.0.0.1:$port/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["games"])')
[ "$games" -gt 0 ] || { echo "smoke test FAILED: /health reports $games games"; exit 1; }
body=$(curl -fsS --max-time 10 "http://127.0.0.1:$port/") || {
    echo "smoke test FAILED: could not fetch /"; docker logs --tail 40 "$cid"; exit 1; }
case "$body" in
    *"PA Lottery Scratch Odds"*) ;;
    *) echo "smoke test FAILED: index.html did not render"; exit 1 ;;
esac
echo "==> smoke test OK ($games games)"
docker rm -f "$cid" >/dev/null; trap - EXIT

echo "==> pushing to Docker Hub"
docker push "$IMAGE:$VERSION"
docker push "$IMAGE:latest"

if [ -z "$(git status --porcelain)" ]; then
    if git rev-parse "$VERSION" >/dev/null 2>&1; then
        echo "==> git tag $VERSION already exists, leaving it alone"
    else
        git tag -a "$VERSION" -m "$VERSION"
        git push origin "$VERSION" || echo "!! git tag push failed -- push it manually"
    fi
else
    echo "!! working tree dirty -- skipping git tag; commit and tag $VERSION manually"
fi

echo "==> redeploying"
"$REPO_DIR/deploy/run.sh" "$VERSION"
