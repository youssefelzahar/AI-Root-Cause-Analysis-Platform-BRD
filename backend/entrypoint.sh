#!/bin/sh
set -e

# Run migrations before serving. Fine for a single backend replica; a
# multi-replica deployment should use a one-shot migrate service instead.
if [ "${RUN_MIGRATIONS}" = "true" ]; then
  echo "Running database migrations..."
  alembic upgrade head
fi

exec "$@"
