# Cloud Run + Cloudflare R2 Deployment

## Architecture

- FastAPI app: Google Cloud Run
- Database: Cloud SQL PostgreSQL
- Course files/videos/materials: Cloudflare R2
- Payments: Stripe
- File delivery: signed R2 URLs, not FastAPI streaming

## Important behavior

- Cloud Run filesystem is not used for course uploads.
- Admin uploads go directly from browser to R2 using presigned PUT URLs.
- Learner/admin downloads use presigned GET URLs.
- PostgreSQL stores only metadata: lesson ID, file name, R2 object key, content type, size.
- FastAPI never streams course videos/materials.

## Cloud Run command

The Dockerfile runs:

```bash
uvicorn fastapi_app:app --host 0.0.0.0 --port ${PORT:-8080}
```

## Build and deploy example

```bash
gcloud run deploy hub-academy \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --add-cloudsql-instances PROJECT:REGION:INSTANCE \
  --set-env-vars APP_ENV=production
```

Set the remaining variables from `.env.cloudrun.example` in Cloud Run.

## Cloud Run settings

- CPU allocation: request-based billing unless you add background workers.
- Minimum instances: `0` for lowest cost, `1` only if cold starts are unacceptable.
- Cloud SQL connection: add your PostgreSQL instance under Cloud Run connections.
- Container port: Cloud Run provides `PORT`; the Dockerfile reads it.
- Do not configure a writable upload directory. Course files belong in R2.

## R2 CORS

Configure the R2 bucket CORS to allow PUT uploads from your app domain.

Allowed methods:

```text
PUT
GET
HEAD
```

Allowed headers:

```text
Content-Type
```

Allowed origin:

```text
https://your-domain.com
```

## Database metadata

`lesson_materials` stores:

- `lesson_id`
- `material_type`
- `file_name`
- `storage_provider`
- `object_key`
- `content_type`
- `size_bytes`

The R2 object itself stores the bytes. FastAPI stores and serves no uploaded
course files.
