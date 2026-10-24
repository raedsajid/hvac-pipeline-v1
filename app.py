"""Vercel's supported Flask entrypoint. No database migrations at cold start."""
from builderp import create_app
app = create_app()
