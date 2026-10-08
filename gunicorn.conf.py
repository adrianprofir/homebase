# Loaded automatically by gunicorn from the working directory (/app in the image).
import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
accesslog = "-"
# The runtime control socket is not used, and the unprivileged container user has no
# writable home directory to put it in.
control_socket_disable = True
