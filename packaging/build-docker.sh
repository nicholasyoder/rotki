#!/usr/bin/env bash

set -euo pipefail

NO_CACHE=""
CUSTOM_TAG=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --no-cache)
      NO_CACHE="--no-cache"
      shift
      ;;
    --tag)
      CUSTOM_TAG="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

REVISION=$(git rev-parse HEAD)
SHORT_HASH=$(git rev-parse --short HEAD)
DIRTY=$(if git diff --quiet HEAD 2>/dev/null; then echo '0'; else echo '1'; fi)
ROTKI_VERSION=$(grep 'current_version = ' .bumpversion.cfg | sed 's/current_version = //')
POSTFIX=$(if git describe --tags --exact-match "$REVISION" &>/dev/null; then echo ''; else echo '-dev'; fi)
ROTKI_VERSION=${ROTKI_VERSION}${POSTFIX}
TAG="rotki/rotki:${CUSTOM_TAG:-${DIRTY}${SHORT_HASH}}"

echo "Building ${TAG} (version: ${ROTKI_VERSION})"

docker buildx build \
  --pull \
  --load \
  $NO_CACHE \
  --build-arg REVISION="$REVISION" \
  --build-arg ROTKI_VERSION="$ROTKI_VERSION" \
  -t "$TAG" \
  .

echo ""
echo "Run with:"
echo "  mkdir -p ~/.rotki/data ~/.rotki/logs"
echo "  docker run -d --name rotki -v ~/.rotki/data:/data -v ~/.rotki/logs:/logs -p 8084:80 -e LOGLEVEL=debug ${TAG}"