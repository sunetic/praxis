# Quickstart

## Start with Docker

You need a machine that can run Docker and an API key for an OpenAI-compatible model service.

```bash
docker run -d \
  --name praxis \
  -p 8000:8000 \
  -v ~/.praxis/data:/app/data \
  sunzy2/praxis:latest
```

Open [http://localhost:8000](http://localhost:8000). On first startup, select a model provider and enter its API key, model name, and base URL. Praxis saves the configuration in its local data directory.

!!! warning "Set an encryption key before production use"
    For a shared or production deployment, set a strongly random `SECRET_KEY` or a dedicated `DATASOURCE_ENCRYPTION_KEY`, and persist `/app/data`. Existing datasource passwords cannot be decrypted after the encryption key changes.

## Connect your first database

1. Open **Datasources** and choose “New datasource.”
2. Select MySQL or PostgreSQL, then enter the host, port, user, and database name.
3. Test the connection first to confirm the target is reachable from the Praxis host.
4. Save it, open **Chat**, and select the datasource.

For the first connection, use a read-only account with minimal privileges. Grant write access only to a dedicated datasource after the need and risks are understood.

## Complete your first task

Start with a verifiable question, for example:

> Check this database's current connection pressure and long-running transactions. Collect evidence before explaining the risks, and do not make any changes.

Check whether the answer distinguishes observed facts, inference, and uncertainty. If the method will be reused, continue with [Agent](../features/agents.md) and [Skills](../features/skills.md).

## Run from source

Local development requires Python 3.11+, Node.js 18+, and `uv`.

```bash
make install
make migrate
make dev
```

Start the frontend in another terminal:

```bash
cd frontend
npm install
npm run dev
```

The backend defaults to `http://localhost:8000`, and the frontend development server defaults to `http://localhost:5173`.
