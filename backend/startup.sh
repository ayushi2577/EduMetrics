#!/bin/bash
# 1. Apply all migrations for real (no faking)
python manage.py migrate --noinput

# 2. Collect static files
python manage.py collectstatic --noinput

# 3. Start the server
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --timeout 120
