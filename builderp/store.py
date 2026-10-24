"""SQLite and private object storage; compatible with the existing SQL schema."""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from flask import current_app, g

class AppError(Exception):
    def __init__(self, status, message, code='request_failed'):
        super().__init__(message)
        self.status, self.code = status, code

def now(): return int(time.time() * 1000)
def new_id(): return str(uuid.uuid4())
def sha(value): return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()

def db():
    if 'db' not in g and current_app.config.get('TURSO_DATABASE_URL'):
        from .turso import Connection
        g.db=Connection(current_app.config['TURSO_DATABASE_URL'],current_app.config['TURSO_AUTH_TOKEN'])
    if 'db' not in g:
        path = Path(current_app.config['DATABASE'])
        path.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
        g.db.execute('PRAGMA journal_mode=WAL')
    return g.db

def init_db():
    db().executescript(Path(__file__).with_name('schema.sql').read_text())
    for table, name, definition in [('messages','doc_id','TEXT'),('project_docs','log_version','INTEGER NOT NULL DEFAULT 0'),('project_docs','doc_kind',"TEXT NOT NULL DEFAULT 'spec'")]:
        columns = {r['name'] for r in db().execute(f'PRAGMA table_info({table})')}
        if name not in columns:
            db().execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    db().execute('CREATE TABLE IF NOT EXISTS python_jobs (doc_id TEXT, kind TEXT, cursor INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0, lease INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT \'pending\', error TEXT, PRIMARY KEY(doc_id,kind))')
    db().execute('CREATE TABLE IF NOT EXISTS python_objects (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db().execute('CREATE TABLE IF NOT EXISTS python_uploads (id TEXT PRIMARY KEY,user_id TEXT NOT NULL,project_id TEXT NOT NULL,filename TEXT NOT NULL,kind TEXT NOT NULL,size INTEGER NOT NULL,expires INTEGER NOT NULL,doc_id TEXT)')

def all_rows(sql, *args): return [dict(r) for r in db().execute(sql, args).fetchall()]
def one(sql, *args):
    r = db().execute(sql, args).fetchone()
    return dict(r) if r else None

def run(sql, *args): return db().execute(sql,args).rowcount

def write(sql, *args):
    connection=db()
    if getattr(connection,'in_transaction',False) and hasattr(connection,'queue'):
        connection.queue(sql,args)
        return None  # Deferred writes are flushed before reads/commit.
    return connection.execute(sql,args).rowcount

@contextmanager
def transaction():
    db().execute('BEGIN IMMEDIATE')
    try:
        yield
        db().execute('COMMIT')
    except BaseException:
        if hasattr(db(),'rollback'): db().rollback()
        else: db().execute('ROLLBACK')
        raise

def owned_project(pid, uid):
    row = one('SELECT * FROM projects WHERE id=? AND user_id=?', pid, uid)
    if not row: raise AppError(404, 'Project not found.')
    return row

def owned_doc(did, uid):
    row = one('SELECT pd.*,d.status,d.pages,d.cursor,d.total,d.indexed,d.error FROM project_docs pd JOIN projects p ON p.id=pd.project_id JOIN documents d ON d.hash=pd.hash WHERE pd.id=? AND p.user_id=?',did,uid)
    if not row: raise AppError(404, 'Document not found.')
    return row

def owned_requirement(rid, uid):
    row = one('SELECT r.*,pd.hash,pd.project_id FROM requirements r JOIN project_docs pd ON pd.id=r.doc_id JOIN projects p ON p.id=pd.project_id WHERE r.id=? AND p.user_id=?',rid,uid)
    if not row: raise AppError(404,'Requirement not found.')
    return row

def event(pid, message):
    write('INSERT INTO events VALUES (?,?,?,?)',new_id(),pid,message,now())

def consume_limit(key, maximum, window=86400000):
    slot = now() // window
    row = one('INSERT INTO limits(key,count,expires) VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1 WHERE count<? RETURNING count',f'{key}:{slot}',(slot+1)*window,maximum)
    if not row: raise AppError(429,'Usage limit reached. Please retry after the limit resets.','rate_limit')

def object_path(key):
    root = Path(current_app.config['OBJECT_DIR']).resolve()
    path = (root/key).resolve()
    if not path.is_relative_to(root) or path == root: raise AppError(400,'Invalid storage key.')
    return path

def s3():
    if not current_app.config.get('R2_BUCKET'): return None
    if 's3' not in g:
        import boto3
        from botocore.config import Config
        g.s3 = boto3.client('s3',region_name='auto',endpoint_url=f"https://{current_app.config['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",aws_access_key_id=current_app.config['R2_ACCESS_KEY_ID'],aws_secret_access_key=current_app.config['R2_SECRET_ACCESS_KEY'],config=Config(signature_version='s3v4',connect_timeout=10,read_timeout=30,retries={'max_attempts':2},request_checksum_calculation='when_required',response_checksum_validation='when_required'))
    return g.s3

def get_bytes(key):
    client = s3()
    if client:
        try: return client.get_object(Bucket=current_app.config['R2_BUCKET'],Key=key)['Body'].read()
        except client.exceptions.NoSuchKey: return None
    path = object_path(key)
    return path.read_bytes() if path.exists() else None

def put_bytes(key, data):
    client = s3()
    if client:
        client.put_object(Bucket=current_app.config['R2_BUCKET'],Key=key,Body=data)
        return
    path = object_path(key)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = path.with_name(path.name+'.'+new_id()+'.tmp')
    try:
        temp.write_bytes(data)
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)

def get_json(key, default=None):
    # Per-project mutable state is stored transactionally in SQLite.
    row = one('SELECT value FROM python_objects WHERE key=?',key)
    if row: return json.loads(row['value'])
    # Python-owned state has always lived in SQL. Avoid remote object lookups
    # for absent keys inside short database transactions. Legacy caches still
    # fall back to the original R2 locations.
    if key.startswith('python/'): return default
    data = get_bytes(key)
    return json.loads(data) if data else default

def put_json(key, value):
    write('INSERT INTO python_objects VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',key,json.dumps(value,ensure_ascii=False))

@contextmanager
def job_lock(did,kind):
    run('INSERT OR IGNORE INTO python_jobs(doc_id,kind) VALUES (?,?)',did,kind)
    lease = now()+300000
    if not run('UPDATE python_jobs SET lease=?,error=NULL WHERE doc_id=? AND kind=? AND lease<?',lease,did,kind,now()):
        raise AppError(409,'This document is busy. Retry shortly.','busy')
    try: yield one('SELECT * FROM python_jobs WHERE doc_id=? AND kind=?',did,kind)
    except Exception as exc:
        try: run('UPDATE python_jobs SET error=? WHERE doc_id=? AND kind=? AND lease=?',str(exc) if isinstance(exc,AppError) else 'Processing paused. Retry to resume.',did,kind,lease)
        except Exception: pass
        raise
    finally:
        try: run('UPDATE python_jobs SET lease=0 WHERE doc_id=? AND kind=? AND lease=?',did,kind,lease)
        except Exception: pass  # Interrupted remote connections expire through the lease.
