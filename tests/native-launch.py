"""Audit/run the actual launch initramfs and real root integrity files off-device.

Synthetic installation receipts/firmware fixtures test integrity, NOT hardware
readiness. No physical block devices are passed into the container.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shlex
import stat
import struct
import subprocess
import tarfile
import tempfile


def newc(data):
    entries={};offset=0
    while True:
        header=data[offset:offset+110]
        if len(header)!=110 or header[:6]!=b'070701':raise ValueError('Invalid cpio')
        fields=[int(header[i:i+8],16) for i in range(6,110,8)]
        mode,size,namesize=fields[1],fields[6],fields[11]
        start=offset+110
        name=data[start:start+namesize-1].decode()
        if namesize<1 or data[start+namesize-1]!=0:raise ValueError('Invalid name')
        start=(start+namesize+3)&~3
        payload=data[start:start+size]
        if len(payload)!=size:raise ValueError('Truncated cpio')
        offset=(start+size+3)&~3
        if name=='TRAILER!!!':return entries
        if name.startswith('/') or '..' in PurePosixPath(name).parts or name in entries:
            raise ValueError('Unsafe cpio entry')
        entries[name]=(payload,mode)


def elf_pages(data):
    if data[:6]!=b'\x7fELF\x02\x01':return 0
    assert struct.unpack_from('<H',data,18)[0]==183
    offset=struct.unpack_from('<Q',data,32)[0]
    stride,count=struct.unpack_from('<HH',data,54)
    loads=0
    for index in range(count):
        kind,_,file_offset,virtual,_,_,_,alignment=struct.unpack_from('<IIQQQQQQ',data,offset+index*stride)
        if kind==1:
            assert alignment>=16384 and file_offset%16384==virtual%16384
            loads+=1
    return loads


def add(target,name,data,mode=0o644):
    item=tarfile.TarInfo(name);item.mode=mode;item.size=len(data)
    target.addfile(item,io.BytesIO(data))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate',type=Path)
    parser.add_argument('--root',nargs=2,action='append',required=True,metavar=('ARCHIVE','RECEIPT'))
    parser.add_argument('--mixin',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    receipt=json.loads((args.candidate/'recovery.bin.json').read_text())
    image=(args.candidate/'recovery.bin').read_bytes()
    assert hashlib.sha256(image).hexdigest()==receipt['image_sha256']
    parts={}
    for row in receipt['components']:
        data=image[row['offset']:row['offset']+row['bytes']]
        assert hashlib.sha256(data).hexdigest()==row['sha256']
        parts[row['name']]=data
    entries=newc(gzip.decompress(parts['initramfs']))
    loads=sum(elf_pages(data) for data,_ in entries.values())
    config=json.loads(entries['etc/orchard/release.json'][0])
    catalogue=json.loads(entries['etc/orchard/launch.json'][0])
    assert config['mode']=='launcher' and 'root' not in config
    assert entries['etc/orchard/launch.json'][0]==(args.candidate/'launch.json').read_bytes()
    with tarfile.open(args.mixin,'r:gz') as mix:
        metadata=json.load(mix.extractfile('mixin.json'))
        firmware={name:mix.extractfile(name).read() for name in metadata['files']}
    metadata['files']=dict(sorted(metadata['files'].items()))
    with tempfile.TemporaryDirectory(prefix='orchard-native-launch-') as temporary:
        archive=Path(temporary)/'recovery.tar'
        fixtures=[]
        with tarfile.open(archive,'w') as target:
            for name,(data,mode) in entries.items():
                item=tarfile.TarInfo(name);item.mode=stat.S_IMODE(mode)
                if stat.S_ISDIR(mode):item.type=tarfile.DIRTYPE
                elif stat.S_ISLNK(mode):item.type=tarfile.SYMTYPE;item.linkname=data.decode()
                elif stat.S_ISREG(mode):item.size=len(data)
                else:raise ValueError('Unexpected initramfs entry')
                target.addfile(item,io.BytesIO(data) if item.isfile() else None)
            add(target,'run/orchard/mixin.json',json.dumps(metadata,sort_keys=True).encode(),0o600)
            for number,(root_path,root_receipt) in enumerate(args.root):
                r=json.loads(Path(root_receipt).read_text())
                record=next(x for x in catalogue['installations'] if x['root_sha256']==r['sha256'])
                name='root'+str(number);prefix='fixtures/'+name+'/'
                needed=set(record['files']);public=None;copied=set()
                process=None
                if r['compression']=='zstd':
                    process=subprocess.Popen(['zstd','-dc',root_path],stdout=subprocess.PIPE)
                    source=tarfile.open(fileobj=process.stdout,mode='r|')
                else:source=tarfile.open(root_path,mode='r|gz')
                try:
                    with source:
                        for member in source:
                            path=member.name.removeprefix('./')
                            if member.issym():
                                item=tarfile.TarInfo(prefix+path);item.type=tarfile.SYMTYPE
                                item.linkname=member.linkname;target.addfile(item)
                            elif path in needed:
                                data=source.extractfile(member).read()
                                assert hashlib.sha256(data).hexdigest()==record['files'][path]
                                add(target,prefix+path,data,member.mode);copied.add(path)
                                if path=='etc/orchard/public.SHA256SUMS':public=data.decode()
                    if process:assert process.wait()==0
                finally:
                    if process:
                        process.stdout.close()
                        if process.poll() is None:process.kill();process.wait()
                assert copied==needed and public is not None
                for relative,data in firmware.items():add(target,prefix+'usr/'+relative,data,0o600)
                ledger=public+''.join(row['sha256']+'  /usr/'+relative+'\n' for relative,row in metadata['files'].items())
                add(target,prefix+'usr/share/ipadlinux/hardware.SHA256SUMS',ledger.encode())
                installed={'schema':1,'release':record['release'],'root_sha256':record['root_sha256'],
                           'partition':'fixture','filesystem_uuid':'fixture','directory':name}
                add(target,prefix+'.orchard-install.json',json.dumps(installed).encode())
                fixtures.append({'directory':name,'init':record['init']})
            code='''
import json,sys,subprocess,types
from pathlib import Path
sys.path.insert(0,'/usr/libexec/orchard-j71b')
import launch
status=json.loads(subprocess.check_output(['/usr/bin/orchard-device','status']))['result']
assert status['ready'] is False and status['mode']=='launcher'
for operation in ('format-target','install','targets','handoff','destination'):
    r=subprocess.run(['/usr/bin/orchard-device',operation],capture_output=True,text=True)
    assert r.returncode!=0 and 'Shared launcher refuses' in r.stdout
assert not Path('/run/orchard/format.started').exists()
launch.os.uname=lambda:types.SimpleNamespace(release=KERNEL)
for fixture in FIXTURES:
    root=Path('/fixtures')/fixture['directory']
    receipt,init=launch.validate(root,{'id':'fixture','filesystem_uuid':'fixture'})
    assert init==fixture['init']
print(json.dumps({'actual_initramfs_python_api':True,'ready':False,'actual_root_integrity_fixtures':len(FIXTURES),
                  'kernel_identity_mocked_for_fixture_test_only':True,'format_install_apis_disabled':True,
                  'physical_device_operations_performed':False}))
'''.replace('KERNEL',repr(config['kernel_release'])).replace('FIXTURES',repr(fixtures))
            add(target,'probe.py',code.encode())
        tag='localhost/orchard-launch-native:temporary'
        try:
            subprocess.run(['podman','import',str(archive),tag],check=True,stdout=subprocess.DEVNULL)
            command='/bin/busybox --install -s; exec /usr/bin/python3 /probe.py'
            result=subprocess.check_output(['podman','run','--rm','--network','none',tag,
                                            '/bin/busybox','sh','-c',command],text=True)
            report={**json.loads(result),'initramfs_members':len(entries),'compatible_elf_segments':loads,
                    'recovery_bytes':len(image),'sha256':receipt['image_sha256']}
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report,indent=2))
        finally:
            subprocess.run(['podman','image','rm',tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


if __name__=='__main__':main()
