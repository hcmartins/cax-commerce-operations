#!/bin/sh
set -eu

python -m commerce_operations.deployment wait-for-db
exec "$@"
