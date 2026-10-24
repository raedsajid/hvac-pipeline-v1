"""Load simple KEY=value configuration without evaluating shell expressions."""
import os
import re
from pathlib import Path

def load_env():
    path=Path(__file__).resolve().parent.parent/'.env'
    if not path.exists(): return
    for line in path.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#'): continue
        key,separator,value=line.partition('=')
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*',key.strip()): continue
        value=value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"): value=value[1:-1]
        os.environ.setdefault(key.strip(),value)
