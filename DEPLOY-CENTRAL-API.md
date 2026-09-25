# Central CRM API Deployment

The central API is a Flask application served by Gunicorn on port 8000.

## Required environment variables

- `CRM_API_SECRET`: a long random secret used by the bootstrap endpoint.
- `CRM_CLOUD_DB`: SQLite database path. The deployment must keep this path on persistent storage.

Do not put `CRM_API_SECRET` inside the Android APK or commit it to Git.

## Persistent storage requirement

The API uses SQLite. A production deployment must mount persistent storage at `/app/data` so `/app/data/cloud.db` survives container restarts and redeploys.

Do not deploy this configuration on an ephemeral filesystem.

## Docker Compose

`docker-compose.yml` defines a named volume (`crm-data`) mounted at `/app/data`.

Example startup:

```bash
export CRM_API_SECRET='replace-with-a-long-random-secret'
docker compose up -d --build
```

The API should then answer:

```
GET /api/health
```

The Android app must be configured with the public HTTPS base URL through `CRM_CLOUD_API_URL` or its app-private `cloud_api_url.txt` configuration.

## Production checklist

1. Deploy the Docker image on a host with persistent disk.
2. Set `CRM_API_SECRET` as a server-side secret.
3. Keep `/app/data` persistent.
4. Put the API behind HTTPS.
5. Confirm `GET /api/health`.
6. Bootstrap the first Admin account.
7. Configure the APK with the HTTPS API base URL.
8. Only then perform multi-phone smoke testing.

The GitHub APK build does not itself deploy the central API and does not provide persistent database storage.
