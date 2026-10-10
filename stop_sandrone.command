#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
exec "$ROOT_DIR/scripts/local_services.sh" stop
