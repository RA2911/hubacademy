import mimetypes
import uuid
from datetime import datetime

import fastapi_config as cfg


def r2_enabled():
    return all([cfg.R2_ACCOUNT_ID, cfg.R2_ACCESS_KEY_ID, cfg.R2_SECRET_ACCESS_KEY, cfg.R2_BUCKET])


def r2_client():
    if not r2_enabled():
        raise RuntimeError('Cloudflare R2 is not configured.')
    import boto3
    from botocore.client import Config
    return boto3.client(
        's3',
        endpoint_url=f'https://{cfg.R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
        aws_access_key_id=cfg.R2_ACCESS_KEY_ID,
        aws_secret_access_key=cfg.R2_SECRET_ACCESS_KEY,
        region_name='auto',
        config=Config(signature_version='s3v4'),
    )


def object_key(filename, lesson_id=None, material_type=None):
    safe = ''.join(ch if ch.isalnum() or ch in '.-_' else '-' for ch in filename).strip('-') or 'file'
    date = datetime.utcnow().strftime('%Y/%m/%d')
    prefix = f'lessons/{lesson_id}' if lesson_id else 'uploads'
    if material_type:
        safe_type = ''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in material_type).strip('-')
        if safe_type:
            prefix = f'{prefix}/{safe_type}'
    return f'{prefix}/{date}/{uuid.uuid4().hex}_{safe}'


def guess_content_type(filename, fallback='application/octet-stream'):
    return mimetypes.guess_type(filename)[0] or fallback


def presigned_upload_url(key, content_type):
    client = r2_client()
    return client.generate_presigned_url(
        'put_object',
        Params={'Bucket': cfg.R2_BUCKET, 'Key': key, 'ContentType': content_type},
        ExpiresIn=cfg.R2_PRESIGN_EXPIRES_SECONDS,
    )


def presigned_download_url(key, filename=None):
    client = r2_client()
    params = {'Bucket': cfg.R2_BUCKET, 'Key': key}
    if filename:
        params['ResponseContentDisposition'] = f'inline; filename="{filename}"'
    return client.generate_presigned_url('get_object', Params=params, ExpiresIn=cfg.R2_PRESIGN_EXPIRES_SECONDS)


def upload_fileobj(key, fileobj, content_type):
    client = r2_client()
    client.upload_fileobj(
        fileobj,
        cfg.R2_BUCKET,
        key,
        ExtraArgs={'ContentType': content_type or 'application/octet-stream'},
    )
