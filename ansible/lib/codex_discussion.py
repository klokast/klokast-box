"""Selected-discussion storage adapter for the tested Codex 0.163 SQLite format.

Authentication, logs, global memories, skills and repositories are never copied.
Both native runners use /home/agent. Unknown schemas and external attachment
paths are refused before source removal.
"""
import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

SCHEMA = Path(__file__).with_name('codex_storage_v0163.json')
LOGICAL_HOME = Path('/home/agent')
TABLES = {
    'state_5.sqlite': ('threads','thread_dynamic_tools','thread_spawn_edges','thread_attachments',
        'guardian_review_feedback','thread_read_receipts','thread_sections','projects','project_roots'),
    'thread_history_1.sqlite': ('thread_turns','thread_items','thread_history_projection_state','thread_realtime_items'),
    'goals_1.sqlite': ('thread_goals','thread_goal_continuation_deferrals'),
    'queue_1.sqlite': ('queued_items','queued_thread_revisions'),
}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent,mode='wb',prefix='.discussion-',delete=False) as stream:
        temporary=Path(stream.name);stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())
    try:
        temporary.replace(path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:temporary.unlink(missing_ok=True)


def encode(value):
    return {'sqlite_blob':base64.b64encode(value).decode()} if isinstance(value,bytes) else value


def decode(value):
    return base64.b64decode(value['sqlite_blob'],validate=True) if isinstance(value,dict) and set(value)=={'sqlite_blob'} else value


def identifier(value):
    if str(uuid.UUID(value))!=value:raise ValueError('discussion ID must be a canonical UUID')
    return value


class Store:
    def __init__(self,home):
        self.home=Path(home)
        self.formats=json.loads(SCHEMA.read_text())
        self.schemas={name:value['tables'] for name,value in self.formats.items()}

    def connect(self,name):
        path=self.home/name
        if path.is_symlink():raise RuntimeError('Codex database is a symbolic link')
        connection=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=10)
        connection.row_factory=sqlite3.Row
        return connection

    def validate(self,connection,name):
        actual={r[0]:r[1] for r in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        normalize=lambda value:{k:' '.join(v.split()) for k,v in value.items()}
        if normalize(actual)!=normalize(self.schemas[name]):
            raise RuntimeError('unsupported Codex storage schema: '+name)
        for kind in ('index','trigger','view'):
            actual=dict(connection.execute('SELECT name,sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL',(kind,)))
            key='indexes' if kind=='index' else kind+'s'
            if normalize(actual)!=normalize(self.formats[name][key]):
                raise RuntimeError('unsupported Codex storage '+kind+': '+name)
        if connection.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            raise RuntimeError('Codex database integrity failed: '+name)
        migrations=[{'version':r[0],'checksum':base64.b64encode(r[1]).decode()}
                    for r in connection.execute('SELECT version,checksum FROM _sqlx_migrations WHERE success=1 ORDER BY version')]
        if migrations!=self.formats[name]['migrations']:
            raise RuntimeError('unsupported Codex migration history: '+name)

    def create(self,name,migrations):
        c=sqlite3.connect(self.home/name);c.row_factory=sqlite3.Row
        for sql in self.schemas[name].values():c.execute(sql)
        for sql in self.formats[name]['indexes'].values():c.execute(sql)
        for sql in self.formats[name]['triggers'].values():c.execute(sql)
        for sql in self.formats[name]['views'].values():c.execute(sql)
        self.insert(c,'_sqlx_migrations',migrations)
        c.commit();self.validate(c,name)
        return c

    @contextlib.contextmanager
    def databases(self):
        connections={}
        try:
            for name in TABLES:
                if not (self.home/name).exists():
                    if name=='state_5.sqlite':raise RuntimeError('Codex state database is absent; initialize Codex first')
                    continue
                c=self.connect(name);connections[name]=c;self.validate(c,name)
                c.execute('BEGIN IMMEDIATE')
            yield connections
        finally:
            for c in connections.values():c.rollback();c.close()

    def ids(self,c,session):
        identifier(session)
        if not c.execute('SELECT 1 FROM threads WHERE id=?',(session,)).fetchone():
            raise RuntimeError('selected discussion is absent')
        ids={session};frontier=[session]
        while frontier:
            parent=frontier.pop()
            for row in c.execute('SELECT child_thread_id FROM thread_spawn_edges WHERE parent_thread_id=?',(parent,)):
                child=identifier(row[0])
                if child in ids:raise RuntimeError('discussion child-thread records contain a cycle')
                if not c.execute('SELECT 1 FROM threads WHERE id=?',(child,)).fetchone():
                    raise RuntimeError('discussion child-thread metadata is incomplete')
                ids.add(child);frontier.append(child)
                if len(ids)>500:raise RuntimeError('discussion has too many child threads for a bounded transfer')
        return sorted(ids)

    def path(self,relative):
        p=Path(relative)
        if p.is_absolute() or '..' in p.parts or not p.parts:
            raise RuntimeError('discussion file path escapes the runner home')
        target=self.home.parent/p
        if target.is_symlink() or not target.resolve().is_relative_to(self.home.parent.resolve()):
            raise RuntimeError('discussion attachment has an unsafe path')
        # No local credential/configuration file is an allowed attachment.
        if p.parts[0].startswith('.') and p.parts[0]!='.codex':
            raise RuntimeError('attachment refers to private runner settings')
        if p.parts[0]=='.codex' and (len(p.parts)<2 or p.parts[1] not in ('sessions','archived_sessions','attachments','tmp')):
            raise RuntimeError('attachment refers to Codex settings or credentials')
        return target

    def relative(self,path):
        try:return str(Path(path).relative_to(LOGICAL_HOME))
        except ValueError:raise RuntimeError('attachment is outside /home/agent; place it in runner-owned storage before handoff') from None

    def files(self,records):
        found=set()
        def attachment(value,*,payload=False):
            if isinstance(value,list):
                for v in value:attachment(v,payload=payload)
            elif isinstance(value,dict):
                typed=value.get('type') in ('localImage','local_image','input_image')
                for key,v in value.items():
                    if isinstance(v,str) and ((payload or typed) and key in ('path','file_path','image_path') or key=='image_url' and v.startswith('file://')):
                        found.add(self.relative(v.removeprefix('file://')))
                    elif isinstance(v,(dict,list)):attachment(v,payload=payload)
        for thread in records['state_5.sqlite']['threads']:
            relative=self.relative(thread['rollout_path'])
            if not relative.startswith(('.codex/sessions/','.codex/archived_sessions/')):
                raise RuntimeError('discussion rollout is outside the supported session trees')
            found.add(relative)
            content=self.path(relative).read_bytes()
            if not content.endswith(b'\n'):raise RuntimeError('discussion rollout is incomplete')
            for line in content.splitlines():attachment(json.loads(line))
        for row in records['state_5.sqlite']['thread_attachments']:
            # This native table permits arbitrary client-defined JSON. A schema
            # match alone cannot prove that an opaque attachment is complete.
            payload=json.loads(row['payload'])
            if (row['attachment_type'] not in ('local_image','localImage') or
                    not isinstance(payload,dict) or set(payload)!={'path'} or
                    not isinstance(payload['path'],str)):
                raise RuntimeError('unsupported persisted discussion attachment format; keep the source copy')
            attachment(payload,payload=True)
        for row in records.get('thread_history_1.sqlite',{}).get('thread_items',[]):
            attachment(json.loads(row['item_json']))
        for row in records.get('thread_history_1.sqlite',{}).get('thread_realtime_items',[]):
            attachment(json.loads(row['item_json']))
        for row in records.get('queue_1.sqlite',{}).get('queued_items',[]):
            attachment(json.loads(row['payload_json']))
        output={}
        total=0
        for relative in sorted(found):
            p=self.path(relative)
            if not p.is_file():raise RuntimeError('referenced attachment is missing: '+relative)
            before=p.stat();data=p.read_bytes();after=p.stat();total+=len(data)
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('attachment changed during export')
            if total>256*1024*1024:raise RuntimeError('selected discussion exceeds the 256 MiB transfer bound')
            output[relative]=base64.b64encode(data).decode()
        return output

    def snapshot(self,session):
        with self.databases() as dbs:
            ids=self.ids(dbs['state_5.sqlite'],session)
            marks=','.join('?' for _ in ids)
            records={};migrations={}
            for name,c in dbs.items():
                records[name]={}
                for table in TABLES[name]:
                    if table in ('projects','project_roots','thread_sections'):continue
                    key='id' if table=='threads' else 'parent_thread_id' if table=='thread_spawn_edges' else 'thread_id'
                    # Revisions are local projection counters, never portable IDs.
                    columns='thread_id' if table=='queued_thread_revisions' else '*'
                    rows=c.execute(f'SELECT {columns} FROM {table} WHERE {key} IN ({marks})',ids)
                    records[name][table]=sorted([{k:encode(row[k]) for k in row.keys()} for row in rows],key=canonical)
                migrations[name]=[{k:encode(r[k]) for k in r.keys()} for r in c.execute('SELECT * FROM _sqlx_migrations ORDER BY version')]
            c=dbs['state_5.sqlite'];threads=records['state_5.sqlite']['threads']
            for table,key,field in (('projects','id','project_id'),('project_roots','project_id','project_id'),('thread_sections','id','thread_section_id')):
                selected=sorted({r[field] for r in threads if r[field] is not None})
                rows=c.execute(f'SELECT * FROM {table} WHERE {key} IN ({",".join("?" for _ in selected)})',selected) if selected else []
                records['state_5.sqlite'][table]=sorted([{k:encode(row[k]) for k in row.keys()} for row in rows],key=canonical)
            for thread in threads:
                if thread['history_mode'] not in ('legacy','paginated'):raise RuntimeError('unknown discussion history mode')
                if thread['history_mode']=='paginated':
                    projection=records.get('thread_history_1.sqlite',{}).get('thread_history_projection_state',[])
                    if not any(r['thread_id']==thread['id'] for r in projection):raise RuntimeError('paginated discussion history is incomplete')
            files=self.files(records)
        return {'format':'klokast.codex-discussion.v1','adapter':'0.163.0','session':session,
                'threads':ids,'databases':records,'migrations':migrations,'files':files}

    def remove(self,bundle):
        """Hide only the exported discussion after its durable source copy exists."""
        ids=bundle['threads'];marks=','.join('?' for _ in ids)
        with self.databases() as dbs:
            for name,c in dbs.items():
                # Queue deletion triggers bump its local projection counter.
                tables=TABLES[name] if name=='queue_1.sqlite' else reversed(TABLES[name])
                for table in tables:
                    if table in ('projects','project_roots','thread_sections'):continue
                    key='id' if table=='threads' else 'parent_thread_id' if table=='thread_spawn_edges' else 'thread_id'
                    columns='thread_id' if table=='queued_thread_revisions' else '*'
                    current=[{k:encode(r[k]) for k in r.keys()} for r in c.execute(f'SELECT {columns} FROM {table} WHERE {key} IN ({marks})',ids)]
                    expected=bundle['databases'].get(name,{}).get(table,[])
                    if any(row not in expected for row in current):raise RuntimeError('source discussion changed after export; preserve both copies')
                    c.execute(f'DELETE FROM {table} WHERE {key} IN ({marks})',ids)
                c.commit()
        # Keep referenced assets for other discussions. Hide rollouts so Codex's
        # legacy scanner cannot rediscover the moved source copy.
        for row in bundle['databases']['state_5.sqlite']['threads']:
            relative=self.relative(row['rollout_path']);p=self.path(relative)
            if p.exists() and base64.b64encode(p.read_bytes()).decode()!=bundle['files'][relative]:
                raise RuntimeError('source rollout changed after export; preserve both copies')
            p.unlink(missing_ok=True)

    def install(self,bundle,receipt):
        validate_bundle(bundle)
        if bundle.get('format')!='klokast.codex-discussion.v1' or bundle.get('adapter')!='0.163.0':
            raise RuntimeError('unsupported discussion export')
        session=identifier(bundle['session']);ids=bundle['threads']
        if session not in ids or any(identifier(v)!=v for v in ids):raise RuntimeError('invalid discussion thread set')
        if set(bundle['databases'])-set(TABLES):raise RuntimeError('export contains an unknown database')
        if len(canonical(bundle))>350*1024*1024:raise RuntimeError('discussion export is too large')
        checksum=digest(bundle)
        prior=json.loads(receipt.read_text()) if receipt.exists() else None
        if prior and prior['sha256']!=checksum:raise RuntimeError('retry differs from the original discussion export')
        if prior and prior['phase'] in ('imported','starting','started'):
            return prior  # Never overwrite history written since an earlier import.
        if not prior:
            with self.databases() as dbs:
                marks=','.join('?' for _ in ids)
                if dbs['state_5.sqlite'].execute(f'SELECT 1 FROM threads WHERE id IN ({marks})',ids).fetchone():
                    raise RuntimeError('destination already has this discussion; preserve both copies and reconcile')
            prior={'phase':'importing','sha256':checksum,'session':session,'completed_databases':[]}
            atomic(receipt,prior)
        for relative,encoded in bundle['files'].items():
            p=self.path(relative);data=base64.b64decode(encoded,validate=True)
            if p.exists():
                if not p.is_file() or p.read_bytes()!=data:raise RuntimeError('destination attachment or rollout changed; preserve both copies')
            else:
                p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with tempfile.NamedTemporaryFile(dir=p.parent,prefix='.handoff-',delete=False) as out:
                    temporary=Path(out.name);out.write(data);out.flush();os.fsync(out.fileno())
                try:os.link(temporary,p)  # No replacement of a concurrent writer's file.
                finally:temporary.unlink(missing_ok=True)
        for name,tables in bundle['databases'].items():
            if set(tables)!=set(TABLES[name]):raise RuntimeError('discussion export is incomplete: '+name)
            missing=not (self.home/name).exists()
            if missing:
                c=self.create(name,bundle['migrations'][name]);c.close()
            c=self.connect(name)
            try:
                self.validate(c,name);c.execute('BEGIN IMMEDIATE')
                for table in TABLES[name]:
                    rows=tables[table]
                    # Exact INSERT OR IGNORE with a value check makes replay after
                    # a lost database commit safe without replacing newer rows.
                    for row in rows:
                        if table=='threads':row=dict(row,archived=0,archived_at=None)
                        self.insert(c,table,[row],check_existing=True)
                c.commit()
            finally:c.close()
            prior['completed_databases']=sorted(set(prior['completed_databases']+[name]));atomic(receipt,prior)
        actual=self.snapshot(session)
        expected=json.loads(json.dumps(bundle))
        for row in expected['databases']['state_5.sqlite']['threads']:row.update(archived=0,archived_at=None)
        if actual['databases']!=expected['databases'] or actual['files']!=expected['files']:
            raise RuntimeError('import verification differs; preserve both copies')
        prior.update(phase='imported',baseline=digest(actual));atomic(receipt,prior)
        return prior

    @staticmethod
    def insert(c,table,rows,check_existing=False):
        columns={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
        for row in rows:
            if not row or set(row)-columns:raise RuntimeError('export has unknown columns: '+table)
            keys=list(row);values=[decode(row[k]) for k in keys]
            c.execute(f'INSERT OR IGNORE INTO {table} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})',values)
            if check_existing and not c.execute(f'SELECT 1 FROM {table} WHERE '+ ' AND '.join(k+' IS ?' for k in keys),values).fetchone():
                raise RuntimeError('destination discussion or shared metadata changed: '+table)


@contextlib.contextmanager
def writers(home,ids):
    descriptors=[]
    try:
        directory=Path(home)/'thread-writer-locks';directory.mkdir(mode=0o700,exist_ok=True)
        coordination=os.open(directory/'.coordination.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(coordination,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Codex writer coordination is busy; retry the handoff') from None
            for session in sorted(ids):
                fd=os.open(directory/(identifier(session)+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
                descriptors.append(fd)
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise RuntimeError('selected discussion still has a live writer; wait for its turn and close that discussion') from None
        finally:os.close(coordination)
        yield
    finally:
        for fd in descriptors:os.close(fd)


def validate_bundle(bundle):
    """Prove completeness in a disposable store before touching working data."""
    if bundle.get('format')!='klokast.codex-discussion.v1' or bundle.get('adapter')!='0.163.0':
        raise RuntimeError('unsupported discussion export')
    if set(bundle['databases'])-set(TABLES) or 'state_5.sqlite' not in bundle['databases']:
        raise RuntimeError('discussion database set is unsupported')
    if len(canonical(bundle))>350*1024*1024:raise RuntimeError('discussion export exceeds its size bound')
    with tempfile.TemporaryDirectory(prefix='codex-discussion-check-') as temporary:
        home=Path(temporary)/'.codex';home.mkdir(mode=0o700)
        scratch=Store(home)
        for name,tables in bundle['databases'].items():
            if set(tables)!=set(TABLES[name]):raise RuntimeError('discussion export has missing or unknown tables')
            c=scratch.create(name,bundle['migrations'][name])
            try:
                for table,rows in tables.items():
                    columns={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
                    if table=='queued_thread_revisions':columns.remove('revision')
                    if any(set(row)!=columns for row in rows):raise RuntimeError('discussion export has missing or unknown fields')
                    scratch.insert(c,table,rows)
                if c.execute('PRAGMA foreign_key_check').fetchone():raise RuntimeError('discussion metadata references missing records')
                c.commit()
            finally:c.close()
        for relative,encoded in bundle['files'].items():
            p=scratch.path(relative);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            p.write_bytes(base64.b64decode(encoded,validate=True))
        actual=scratch.snapshot(bundle['session'])
        if any(actual[key]!=bundle[key] for key in ('threads','databases','files')):
            raise RuntimeError('discussion export is incomplete or contains unrelated records')
        turns={(r['thread_id'],r['turn_id']) for r in bundle['databases'].get('thread_history_1.sqlite',{}).get('thread_turns',[])}
        if any((r['thread_id'],r['turn_id']) not in turns for r in bundle['databases'].get('thread_history_1.sqlite',{}).get('thread_items',[])):
            raise RuntimeError('discussion items refer to missing turns')
