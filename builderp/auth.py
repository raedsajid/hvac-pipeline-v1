from .store import write
import hashlib
import hmac
import re
import secrets
from flask import request, current_app
from .store import AppError, one, run, now, new_id, sha, consume_limit, transaction

COOKIE='builderp_session'

def derive(password,salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),100000,32).hex()

def current_user():
    token=request.cookies.get(COOKIE,'')
    return one('SELECT u.id,u.username,u.role FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?',sha(token),now()) if token else None

def require_user():
    user=current_user()
    if not user: raise AppError(401,'Please sign in.')
    return user

def authenticate(username,password,signup=False):
    username=str(username or '').strip().lower()
    if not re.fullmatch(r'[a-z0-9_]{3,32}',username) or not isinstance(password,str) or not 12<=len(password)<=128:
        raise AppError(400,'Use a 3–32 character username (letters, numbers, underscore) and a password of 12–128 characters.')
    consume_limit('auth:'+sha(request.remote_addr or 'unknown'),20,900000)
    consume_limit('login:'+username,10,900000)
    with transaction():
        user=one('SELECT * FROM users WHERE username=?',username)
        if signup:
            if user: raise AppError(409,'That username is already taken.')
            salt=secrets.token_hex(16)
            uid=new_id()
            write("INSERT INTO users VALUES (?,?,?,CASE WHEN EXISTS(SELECT 1 FROM users) THEN 'member' ELSE 'admin' END,?)",uid,username,salt+':'+derive(password,salt),now())
            user=one('SELECT * FROM users WHERE id=?',uid)
        else:
            salt,expected=user['password'].split(':') if user else ('0'*32,'0'*64)
            actual=derive(password,salt)
            if not user or not hmac.compare_digest(actual,expected): raise AppError(401,'Username or password is incorrect.')
        token=secrets.token_hex(32)
        write('INSERT INTO sessions VALUES (?,?,?)',sha(token),user['id'],now()+604800000)
    return {k:user[k] for k in ('id','username','role')},token

def cookie(response,token):
    response.set_cookie(COOKIE,token,max_age=604800 if token else 0,httponly=True,secure=current_app.config['COOKIE_SECURE'],samesite='Lax',path='/')
    return response

def logout():
    write('DELETE FROM sessions WHERE token=?',sha(request.cookies.get(COOKIE,'')))

def change_password(user,body):
    row=one('SELECT password FROM users WHERE id=?',user['id'])
    salt,expected=row['password'].split(':')
    if not hmac.compare_digest(derive(str(body.get('current','')),salt),expected): raise AppError(400,'Current password is incorrect.')
    password=body.get('password','')
    if not isinstance(password,str) or not 12<=len(password)<=128: raise AppError(400,'Use a password of 12–128 characters.')
    salt=secrets.token_hex(16)
    with transaction():
        write('UPDATE users SET password=? WHERE id=?',salt+':'+derive(password,salt),user['id'])
        write('DELETE FROM sessions WHERE user_id=?',user['id'])
    return {'ok':True}
