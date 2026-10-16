"""Exercise retained infrastructure migration only on disposable Xen test data."""
import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import uuid

import vm_infrastructure_storage as storage

CHECKS = dict.fromkeys(('copy_verified','numeric_ownership','sqlite','symlinks','hardlinks',
                       'resume','no_reseed','wrong_volume_refused'),True)


def test(run, operation, role):
    import retained_data
    retained_data.environment()
    if role not in ('ops','air') or int(Path('/sys/class/block/xvdd/size').read_text())*512 != 256*1024**2:
        raise RuntimeError('infrastructure fixture requires its disposable 256 MiB disk')
    if any(row['source'].startswith('/dev/xvdd') for row in retained_data.mount_records()):
        raise RuntimeError('infrastructure fixture disk is already mounted')
    directory=Path('/mnt/control-storage-fixture');directory.mkdir(mode=0o700)
    source=directory/'old';source.mkdir()
    data=directory/'data'
    accounts={'neo':[1000,1000],**({'smith':[2001,2002],'minion':[2003,2004]} if role=='ops' else {'agent':[2001,2002]})}
    relative='home/smith/private/klokast/apps' if role=='ops' else 'home/agent'
    state=source/relative;state.mkdir(parents=True)
    payload=state/'state.db'
    with closing(sqlite3.connect(payload)) as connection, connection:
        connection.execute('CREATE TABLE state (value TEXT)')
        connection.execute('INSERT INTO state VALUES (?)',('before replacement',))
    payload.chmod(0o600);os.chown(payload,2001,2002)
    (state/'state-link').symlink_to('state.db');os.link(payload,state/'state-hardlink')
    filesystem=str(uuid.uuid4())
    value={'box':'boxa','role':role,'accounts':accounts,'filesystem_uuid':filesystem,
           'operation':operation,'machine_id':'a'*64,'migrate':True}
    previous_data,previous_device=storage.DATA,storage.DEVICE
    storage.DATA,storage.DEVICE=data,'/dev/xvdd'
    mounted=False
    try:
        # These are test disks selected and sized by the existing Xen harness.
        run(['dd','if=/dev/zero','of=/dev/xvdd','bs=1M','count=256','conv=fsync'])
        storage.mount(value,initialize=True);mounted=True
        if role=='ops':
            storage.atomic(data/'platform-ops-data.json',{'box':'boxa','filesystem_uuid':filesystem})
            (data/'download-sources').mkdir()
        original=storage.copy_state
        def interrupted(*args):
            original(*args)
            raise RuntimeError('synthetic interrupted copy')
        storage.copy_state=interrupted
        try:
            try:storage.seed(source,value)
            except RuntimeError as error:
                if str(error)!='synthetic interrupted copy':raise
            else:raise RuntimeError('migration did not exercise interruption')
        finally:storage.copy_state=original
        try:storage.seed(source,dict(value,operation='f'*24 if operation!='f'*24 else 'e'*24))
        except RuntimeError:pass
        else:raise RuntimeError('migration accepted another operation')
        storage.seed(source,value)
        copied=data/'state'/relative
        target=copied/'state.db'
        if (hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(payload.read_bytes()).digest() or
                (target.stat().st_uid,target.stat().st_gid,target.stat().st_mode&0o777)!=(2001,2002,0o600) or
                os.readlink(copied/'state-link')!='state.db' or not target.samefile(copied/'state-hardlink')):
            raise RuntimeError('infrastructure state content or metadata changed')
        with closing(sqlite3.connect(target)) as connection, connection:
            if connection.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise RuntimeError('copied database is corrupt')
            connection.execute('UPDATE state SET value=?',('after first boot',))
        storage.seed(source,dict(value,migrate=False))
        with closing(sqlite3.connect(target)) as connection, connection:
            if connection.execute('SELECT value FROM state').fetchone()!=('after first boot',):
                raise RuntimeError('replacement restored stale root data')
        run(['umount',str(data)]);mounted=False
        try:storage.mount(dict(value,filesystem_uuid=str(uuid.uuid4())))
        except RuntimeError:pass
        else:
            mounted=True;raise RuntimeError('replacement accepted a different filesystem')
        return dict(CHECKS)
    finally:
        if mounted:run(['umount',str(data)])
        storage.DATA,storage.DEVICE=previous_data,previous_device
        shutil.rmtree(directory)
