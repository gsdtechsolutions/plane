#!/bin/bash
set -e

python manage.py wait_for_db
# Wait for migrations
python manage.py wait_for_migrations
# Run the processes
# Current workers consume the queues used by connected delivery. An explicit
# override supports deployments that run dedicated workers for these queues.
exec celery -A plane worker -l info -Q "${CELERY_WORKER_QUEUES:-celery,release_intelligence,${GITHUB_DELIVERY_QUEUE:-github-delivery}}"
