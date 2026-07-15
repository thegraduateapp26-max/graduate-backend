#!/bin/sh
set -e

if [ "$RUN_MODE" = "cron" ]; then
  exec python analytics_report.py
else
  exec gunicorn --bind :8080 --workers 1 --threads 8 app:app
fi
