# Dev server

The app runs via `docker compose` (service name `web`, container `game-stats-web-1`),
mapping host port 8080 to the container's internal 8000.

The Dockerfile `COPY`s `app/` into the image at build time — it is **not** volume-mounted,
and the container runs **without** `--reload`. `docker compose restart web` alone restarts
the stale image and will NOT pick up code changes.

After editing any file under `app/`, rebuild and relaunch before considering the change
verified:

```bash
docker compose up -d --build web
```

Then re-check the affected page (e.g. `curl -s http://localhost:8080/ | grep ...` for the
specific markup that changed, not just the status code — a 200 can come back from stale
content) before reporting the task as done.
