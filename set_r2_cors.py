"""One-time helper: allow the browser to upload files directly to R2.

Direct-to-R2 uploads (presigned PUT) are how files larger than Cloud Run's
32 MB request limit get in without an HTTP 413. The browser can only do that
if the R2 bucket permits cross-origin PUT requests from the site's origin.

Run once (locally or anywhere the R2_* env vars are set):

    python set_r2_cors.py

Add extra origins as arguments, e.g. a custom domain:

    python set_r2_cors.py https://learn.example.com https://brainpractice-xxxx.run.app
"""
import sys

import fastapi_config as cfg
from fastapi_storage import r2_client, r2_enabled


def main():
    if not r2_enabled():
        print('R2 is not configured (R2_ACCOUNT_ID / R2_BUCKET / keys missing).')
        return 1

    origins = ['http://127.0.0.1:8000', 'http://localhost:8000']
    if cfg.PUBLIC_BASE_URL:
        origins.append(cfg.PUBLIC_BASE_URL)
    origins.extend(a.rstrip('/') for a in sys.argv[1:])
    origins = sorted({o for o in origins if o})

    rules = [{
        'AllowedOrigins': origins,
        'AllowedMethods': ['PUT', 'GET', 'HEAD'],
        'AllowedHeaders': ['*'],
        'ExposeHeaders': ['ETag'],
        'MaxAgeSeconds': 3600,
    }]

    client = r2_client()
    client.put_bucket_cors(Bucket=cfg.R2_BUCKET, CORSConfiguration={'CORSRules': rules})
    print(f'CORS set on bucket "{cfg.R2_BUCKET}" for origins:')
    for o in origins:
        print(f'  - {o}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
