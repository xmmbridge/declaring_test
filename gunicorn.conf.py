import os
bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = 2
timeout = 120
# Share app code between workers via copy-on-write (biggest RAM saving).
# SQLite connections are created per-request so forking is safe.
preload_app = True
# Recycle workers after N requests to prevent gradual memory growth.
max_requests = 500
max_requests_jitter = 50
