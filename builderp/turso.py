"""Small DB-API facade for Turso's documented Hrana v2 HTTP protocol.

No local replica, automatic write retry, or credentials on disk. Transactions
retain one server stream; a lost response poisons the connection rather than
replaying an operation that might already have committed.
"""
import base64
import json
import urllib.request
from urllib.parse import urlsplit


class TursoError(RuntimeError):
    pass


def encode(value):
    if value is None: return {'type':'null'}
    if isinstance(value, (bool,int)): return {'type':'integer','value':str(int(value))}
    if isinstance(value,float): return {'type':'float','value':value}
    if isinstance(value,str): return {'type':'text','value':value}
    if isinstance(value,bytes): return {'type':'blob','base64':base64.b64encode(value).decode()}
    raise TypeError('Unsupported SQL argument')


def decode(value):
    kind=value['type']
    if kind=='null': return None
    if kind=='integer': return int(value['value'])
    if kind=='float': return float(value['value'])
    if kind=='blob': return base64.b64decode(value['base64'])
    return value['value']


class Cursor:
    def __init__(self,result=None):
        result=result or {}; cols=[c['name'] for c in result.get('cols',[])]
        self.rows=[dict(zip(cols,map(decode,r))) for r in result.get('rows',[])]
        self.rowcount=result.get('affected_row_count',0)
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def __iter__(self): return iter(self.rows)


class Connection:
    def __init__(self,url,token):
        parsed=urlsplit(url.replace('libsql://','https://',1).replace('turso://','https://',1))
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('TURSO_DATABASE_URL must be an HTTPS or libsql database URL.')
        self.origin='https://'+parsed.netloc
        self.base=self.origin; self.token=token; self.baton=None
        self.in_transaction=False; self.failed=False; self.pending=[]

    def _send(self,requests):
        if self.failed: raise TursoError('Database connection interrupted. Reload to check saved progress.')
        req=urllib.request.Request(self.base.rstrip('/')+'/v2/pipeline',
            data=json.dumps({'baton':self.baton,'requests':requests}).encode(),
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=20) as response: result=json.load(response)
            self.baton=result.get('baton')
            if result.get('base_url'):
                parsed=urlsplit(result['base_url'])
                original=urlsplit(self.origin).hostname
                # Turso may route a stream to a regional subdomain. Never forward
                # a database token to an unrelated host supplied in a response.
                if parsed.scheme!='https' or parsed.username or not (parsed.hostname==original or parsed.hostname.endswith('.'+original) or (original.endswith('.turso.io') and parsed.hostname.endswith('.turso.io'))):
                    raise ValueError('Unexpected database stream host')
                self.base=result['base_url'].rstrip('/')
            results=result['results']
            if len(results)!=len(requests): raise ValueError('Incomplete database response')
        except Exception as exc:
            self.failed=True
            raise TursoError('Database request failed. Reload to check whether progress was saved; writes were not retried.') from exc
        for item in results:
            if item['type']=='error': raise TursoError('Database statement failed; check schema, credentials, or transaction timeout.')
        return [item['response'] for item in results]

    def flush(self):
        if not self.pending: return
        statements=self.pending; self.pending=[]
        steps=[]
        for i,stmt in enumerate(statements):
            step={'stmt':stmt}
            if i: step['condition']={'type':'ok','step':i-1}
            steps.append(step)
        response=self._send([{'type':'batch','batch':{'steps':steps}}])[0]['result']
        if any(response.get('step_errors',[])):
            raise TursoError('Database batch failed; transaction will be rolled back.')

    def queue(self,sql,args):
        self.pending.append({'sql':sql,'args':[encode(x) for x in args],'want_rows':False})

    def execute(self,sql,args=()):
        self.flush()
        command=sql.strip().split()[0].upper()
        begin=command=='BEGIN'; end=command in ('COMMIT','ROLLBACK')
        if begin and self.in_transaction: raise TursoError('Nested transaction')
        requests=[]
        if not self.baton:
            requests.append({'type':'execute','stmt':{'sql':'PRAGMA foreign_keys=ON'}})
        requests.append({'type':'execute','stmt':{'sql':sql,'args':[encode(x) for x in args],'want_rows':True}})
        close=end or (not self.in_transaction and not begin)
        if close: requests.append({'type':'close'})
        result=self._send(requests)[-2 if close else -1]['result']
        if begin:
            if not self.baton: raise TursoError('Database did not retain transaction stream')
            self.in_transaction=True
        if end: self.in_transaction=False
        return Cursor(result)

    def executescript(self,script):
        # Only trusted bundled schema SQL; no user-authored scripts.
        for statement in script.split(';'):
            if statement.strip(): self.execute(statement)

    def rollback(self):
        self.pending=[]
        if self.failed: return
        try: self.execute('ROLLBACK')
        except TursoError: self.close()

    def close(self):
        self.pending=[]
        if self.baton and not self.failed:
            try: self._send([{'type':'close'}])
            except TursoError: pass
        self.baton=None; self.in_transaction=False
