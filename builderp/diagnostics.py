"""Owner-only, read-only deployment smoke test; no Gemini calls or user PDFs."""
import time
import fitz
from flask import current_app
from .store import AppError,one
from .table_jobs import detect_page,load_detector


def check(user):
    if user['role']!='admin': raise AppError(403,'Only the owner can run deployment checks.')
    one('SELECT 1 AS connected')
    load_detector()
    import cv2
    import numpy
    start=time.monotonic()
    with fitz.open() as doc:
        page=doc.new_page(width=600,height=800)
        for x in range(60,421,90):page.draw_line((x,100),(x,300),width=1)
        for y in range(100,301,40):page.draw_line((60,y),(420,y),width=1)
        regions,geometry=detect_page(doc.tobytes(),1)
    if len(regions)!=1: raise AppError(502,'The native detector did not find the expected synthetic test table.')
    return dict(ok=True,database='turso' if current_app.config.get('TURSO_DATABASE_URL') else 'sqlite',
                storage='r2-configured' if current_app.config.get('R2_BUCKET') else 'local',
                opencv=cv2.__version__,numpy=numpy.__version__,pymupdf=fitz.VersionBind,
                syntheticTables=len(regions),seconds=round(time.monotonic()-start,3),
                note='Synthetic native test only. Validate real PDFs, R2 transfer, and Gemini separately.')
