# TaskFlow: A Hands-On Docker Project

You'll build **TaskFlow**, a small task-tracking API with 4 containers:

- `api` — Flask app (Python) that stores tasks
- `db` — PostgreSQL (persistent storage)
- `cache` — Redis (hit counter)
- `nginx` — reverse proxy / load balancer in front of `api`

By the end, you'll have used every concept on your list, on a real running system.

Files: `api/app.py`, `api/requirements.txt`, `api/Dockerfile`, `nginx/nginx.conf`, `docker-compose.yml`.

---

## Part 1 — Basic Docker Commands (no Compose yet)

Do this part manually first, without Compose, so the commands actually mean something to you later.

```bash
cd taskflow-docker

# Build an image from api/Dockerfile
docker build -t taskflow-api ./api

# See the image you just built
docker images

# Run Redis and Postgres as plain containers, on the default bridge network for now
docker run -d --name cache redis:7-alpine
docker run -d --name db \
  -e POSTGRES_DB=taskflow -e POSTGRES_USER=taskflow -e POSTGRES_PASSWORD=taskflow \
  postgres:16-alpine

# List running containers
docker ps

# List ALL containers, including stopped ones
docker ps -a

# Stream logs from a container (Ctrl+C to stop watching)
docker logs -f db

# Open a shell INSIDE a running container
docker exec -it db bash
# try: psql -U taskflow -d taskflow -c "\dt"   (then exit)

# Inspect low-level metadata (IP address, mounts, env vars) as JSON
docker inspect db

# See live resource usage (CPU/mem) like `top` for containers
docker stats --no-stream

# Stop and remove containers when done experimenting
docker stop cache db
docker rm cache db
```

**Checkpoint — you should be able to explain:**
- Difference between `docker ps` and `docker ps -a`
- What `-d` and `-it` flags do
- Why `docker exec` needs a *running* container (unlike `docker run`)

---

## Part 2 — Docker Networking

Right now `cache` and `db` above were run without `--network`, so they land on Docker's default `bridge` network, where containers **can't resolve each other by name** — only by IP, which is unreliable since it changes.

Fix this with a **user-defined bridge network**, which gives you automatic DNS.

```bash
# Create a custom network
docker network create taskflow-net

# List networks
docker network ls

# Inspect it -- see which containers are attached, subnet, gateway
docker network inspect taskflow-net

# Re-run db and cache attached to it
docker run -d --name db --network taskflow-net \
  -e POSTGRES_DB=taskflow -e POSTGRES_USER=taskflow -e POSTGRES_PASSWORD=taskflow \
  postgres:16-alpine
docker run -d --name cache --network taskflow-net redis:7-alpine

# Prove name resolution works: run a throwaway container on the same network
docker run --rm --network taskflow-net alpine sh -c "apk add --no-cache bind-tools >/dev/null && nslookup db"
```

You should see `db` resolve to an internal IP like `172.x.x.x`. **This is exactly why `app.py` connects to `host="db"` and `host="cache"` instead of an IP** — Docker's embedded DNS resolves container names on the same user-defined network.

**Checkpoint:**
- Why didn't name resolution work on the default `bridge` network?
- What happens if you run `docker network inspect bridge` vs `taskflow-net`?

Clean up before Part 3:
```bash
docker stop db cache && docker rm db cache && docker network rm taskflow-net
```

---

## Part 3 — Docker Compose

Doing Part 1+2 by hand for 4 containers is tedious — that's the problem Compose solves. Open `docker-compose.yml` and note:

- Each top-level key under `services:` is one container blueprint
- `networks: taskflow-net` (bottom of the file) replaces your manual `docker network create`
- `depends_on` controls **start order** (not readiness — that's why `app.py` has a retry loop for the DB)
- `environment:` replaces your manual `-e` flags

Bring the whole stack up:

```bash
docker compose up -d --build or docker-compose up -d --build

# See all services and their status
docker-compose ps

# Follow logs across every service at once
docker-compose logs -f

# Follow logs from just one service
docker-compose logs -f api

# Run a command inside a compose-managed container
docker-compose exec db psql -U taskflow -d taskflow -c "SELECT * FROM tasks;"
```

Visit **http://localhost:8080/tasks** — should return `[]`.

Create a task:
```bash
curl -X POST http://localhost:8080/ -o /dev/null   # warms up, ignore
curl -X POST http://localhost:8080/tasks -H "Content-Type: application/json" -d '{"title":"Learn Docker networking"}'
curl http://localhost:8080/tasks
```

Tear down:
```bash
docker-compose down          # stops + removes containers, keeps volumes
docker-compose down -v       # also deletes the named volume (fresh start)
```

**Checkpoint:**
- What's the difference between `docker compose down` and `docker compose down -v`?
- Why does `api` still connect successfully even though `db` takes a few seconds longer to become ready?

---

## Part 4 — Services and Scaling

Bring it back up, then scale the `api` service to 3 replicas:

```bash
docker-compose up -d --build
docker-compose up -d --scale api=3
docker-compose ps
```

You'll see `taskflow-docker-api-1`, `-2`, `-3`. Hit the API repeatedly through nginx and watch `served_by_container` change:

```bash
for i in 1 2 3 4 5 6; do curl -s http://localhost:8080/ | grep served_by_container; done
```

nginx is load-balancing across all 3 replicas, and `hit_count` (from Redis) keeps climbing across ALL of them — because Redis is shared state, while each container's own hostname differs. This is *why* `api` has no `ports:` mapping in the compose file: with 3 replicas you can't map all 3 to host port 5000 anyway, so nginx is the single entrypoint instead.

Scale back down:
```bash
docker-compose up -d --scale api=1
```

**Checkpoint:**
- Why can't you do `ports: ["5000:5000"]` on a service you plan to scale to 3 replicas?
- How does nginx know about all 3 replicas without you listing 3 IPs in `nginx.conf`? (Hint: revisit Part 2 — Docker's embedded DNS returns multiple IPs for one service name.)

---

## Part 5 — Ports and Storage Mounts

Two different mount types are already in your compose file — go find them and compare:

1. **Bind mount** (`api` service): `./api:/app`
   Maps your local `api/` folder directly into the container. Edit `app.py` on your machine, then:
   ```bash
   docker-compose restart api
   ```
   No rebuild needed — the file is live on disk inside the container. Try adding a new field to the `/health` response and restarting to see it.

2. **Named volume** (`db` service): `db-data:/var/lib/postgresql/data`
   Managed by Docker, not tied to a host folder. Proves persistence:
   ```bash
   docker-compose down          # containers gone
   docker-compose up -d         # containers recreated
   curl http://localhost:8080/tasks   # your task is still there
   ```
   Now really destroy it:
   ```bash
   docker-compose down -v       # -v removes named volumes too
   docker-compose up -d --build
   curl http://localhost:8080/tasks   # back to []
   ```

3. **Port mapping** — in `nginx` service: `"8080:80"` means `host_port:container_port`. Change it to `"9090:80"`, run `docker compose up -d`, and browse to port 9090 instead. Note this is separate from the internal `api:5000` connection nginx makes — that's container-to-container over `taskflow-net` and never touches the host at all.

Useful volume commands:
```bash
docker volume ls
docker volume inspect taskflow-docker_db-data
```

**Checkpoint:**
- Why is a bind mount good for `api` (your own code) but a named volume better for `db` (Postgres's internal data files)?
- If you `docker-compose down -v`, which of your data survives and which doesn't?

---

## Where to go next

- Add a second `nginx` config directive for basic health-check routing to `/health`
- Try `docker compose logs --tail=50 -f` with all 3 API replicas running and watch requests round-robin live
- Swap the hardcoded Postgres password for a `.env` file + `env_file:` in compose (a real step toward not committing secrets)
