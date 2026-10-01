"""ASGI entrypoint for the isolated ACC M09 staging service."""
from acc.staging import build_staging_app

app = build_staging_app()
