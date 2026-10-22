# Picked up automatically by gunicorn (it runs from this folder on Render).
# A backup download is one long request, so: a long timeout, and several threads so the rest of the
# portal keeps answering other people while a backup is being downloaded.
worker_class = "gthread"
threads = 8
timeout = 900
