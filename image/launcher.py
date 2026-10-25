"""Build shared J71b launch recovery from verified components and root artifacts.

Root archives are producer inputs ONLY. Launch clients consume the small boot
manifest; no root archive or host install history is needed to select a system.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

from .integrity import sha
from .runtime import files as runtime_files


def compatibility(archive, receipt_path, build_path, components):
    receipt = json.loads(receipt_path.read_text())
    build = json.loads(build_path.read_text())
    if (receipt.get('schema') != 1 or receipt.get('kind') != 'rootfs' or
            receipt.get('profile') != 'j71b' or archive.is_symlink() or
            archive.stat().st_size != receipt['bytes'] or sha(archive) != receipt['sha256']):
        raise ValueError('Installed-root producer receipt differs')
    if build.get('release', build.get('version')) != receipt['release']:
        raise ValueError('Root/provenance release differs')
    provenance = build['inputs'].get('files', build['inputs'])
    for name in ('kernel','dtb'):
        if provenance[name]['sha256'] != components['files'][name]['sha256']:
            raise ValueError('Root/launcher hardware differs: '+name)
    process = None
    if receipt['compression'] == 'zstd':
        process = subprocess.Popen(['zstd','-dc',str(archive)],stdout=subprocess.PIPE)
        bundle = tarfile.open(fileobj=process.stdout,mode='r|')
    elif receipt['compression'] == 'gzip':
        bundle = tarfile.open(archive,mode='r|gz')
    else:
        raise ValueError('Unsupported root compression')
    hashes, links, text = {}, {}, {}
    wanted = {'etc/orchard/public.SHA256SUMS','etc/orchard/release.json',
              'usr/libexec/orchard-j71b/image_policy.py'}
    seen = set()
    try:
        with bundle:
            for member in bundle:
                name = member.name.removeprefix('./')
                if name in ('','.'):
                    continue
                if name.startswith('/') or '..' in PurePosixPath(name).parts or name in seen:
                    raise ValueError('Unsafe/duplicate root archive member')
                seen.add(name)
                if member.issym():
                    links[name] = member.linkname
                elif member.isfile():
                    checksum = hashlib.sha256()
                    content = bytearray()
                    source = bundle.extractfile(member)
                    for block in iter(lambda:source.read(1048576),b''):
                        checksum.update(block)
                        if name in wanted:
                            content.extend(block)
                            if len(content)>1024*1024:
                                raise ValueError('Oversized root policy metadata')
                    hashes[name] = checksum.hexdigest()
                    if name in wanted:
                        text[name] = bytes(content).decode()
        if process and process.wait():
            raise ValueError('Root decompression failed')
    finally:
        if process:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    policy = ast.parse(text['usr/libexec/orchard-j71b/image_policy.py'])
    init = [ast.literal_eval(n.value) for n in policy.body if isinstance(n,ast.Assign) and
            any(isinstance(t,ast.Name) and t.id=='INIT' for t in n.targets)]
    if len(init)!=1 or init[0] not in ('/sbin/init','/usr/lib/systemd/systemd'):
        raise ValueError('Image must declare one supported data-only INIT')
    pending = list(PurePosixPath(init[0]).parts[1:]); parts=[]; followed=0
    while pending:
        part=pending.pop(0)
        if part in ('','.'): continue
        if part=='..':
            if not parts: raise ValueError('Init link escapes root')
            parts.pop(); continue
        candidate='/'.join([*parts,part])
        if candidate in links:
            followed+=1
            if followed>16: raise ValueError('Init link cycle')
            link=PurePosixPath(links[candidate])
            if link.is_absolute(): parts=[]
            pending=list(link.parts[1:] if link.is_absolute() else link.parts)+pending
        else: parts.append(part)
    init_target='/'.join(parts)
    pinned = {}
    for line in text['etc/orchard/public.SHA256SUMS'].splitlines():
        digest, relative = line.split('  ',1)
        relative = relative.removeprefix('/')
        if (not re.fullmatch('[0-9a-f]{64}',digest) or relative in pinned or
                hashes.get(relative)!=digest):
            raise ValueError('Root public runtime integrity differs: '+relative)
        pinned[relative]=digest
    pinned.update({name:hashes[name] for name in wanted})
    pinned[init_target]=hashes[init_target]
    configuration=json.loads(text['etc/orchard/release.json'])
    if configuration['release']!=receipt['release'] or configuration['kernel_release']!=receipt['kernel_release']:
        raise ValueError('Root configuration differs from receipt')
    return {'release':receipt['release'],'root_sha256':receipt['sha256'],
            'kernel_release':receipt['kernel_release'],'init':init[0],
            'init_target':init_target,'files':pinned}


def verify_quiet_boot(receipt_path, inputs):
    """Never silently reuse a distro-branded loader in shared discovery."""
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('schema') != 1 or receipt.get('profile') != 'j71b' or
            receipt.get('presentation') != 'black'):
        raise ValueError('Shared launcher requires black-screen boot components')
    here = Path(__file__).resolve().parents[1] / 'boot/quiet'
    if receipt.get('patches') != {name: sha(here / name) for name in ('pongo.patch', 'm1n1.patch')}:
        raise ValueError('Quiet boot patch provenance differs')
    for role in ('loader', 'pongo'):
        row = inputs['files'][role]
        if receipt.get('files', {}).get(role) != {'bytes': row['bytes'], 'sha256': row['sha256']}:
            raise ValueError('Shared launcher requires verified quiet ' + role)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--quiet-boot-receipt',type=Path,required=True)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--base-receipt',type=Path,required=True)
    parser.add_argument('--installation',nargs=3,action='append',required=True,
                        metavar=('ARCHIVE','ROOT_RECEIPT','BUILD_RECEIPT'))
    parser.add_argument('--release',required=True)
    parser.add_argument('--public-base-url', help='immutable HTTPS release asset directory')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): parser.error('Choose a new output directory')
    if args.public_base_url:
        from urllib.parse import urlsplit
        url = urlsplit(args.public_base_url)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password or
                url.query or url.fragment or '/latest/' in url.path):
            parser.error('--public-base-url must be an immutable HTTPS asset directory')
    inputs=json.loads(args.inputs.read_text())
    verify_quiet_boot(args.quiet_boot_receipt, inputs)
    for row in inputs['files'].values():
        path=Path(row['path'])
        if path.is_symlink() or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']:
            raise ValueError('Component differs: '+str(path))
    modules=Path(inputs['modules']['path'])
    actual={p.relative_to(modules).as_posix():sha(p) for p in modules.rglob('*') if p.is_file()}
    if actual!=inputs['modules']['files'] or any(p.is_symlink() for p in modules.rglob('*')):
        raise ValueError('Module tree differs')
    policies=Path(__file__).resolve().parents[1]/'launcher'
    kernel_release, public=runtime_files(modules,policies)
    records=[compatibility(*(Path(p) for p in row),inputs) for row in args.installation]
    identities={(r['release'],r['root_sha256']) for r in records}
    if len(identities)!=len(records): raise ValueError('Duplicate launch compatibility records')
    # Same release string alone is not a kernel/module compatibility guarantee.
    for record in records:
        if record['kernel_release']!=kernel_release: raise ValueError('Kernel ABI differs')
        for name,(data,_) in public.items():
            if name.startswith('usr/lib/modules/') and record['files'].get(name)!=hashlib.sha256(data).hexdigest():
                raise ValueError('Installed module compatibility differs: '+name)
    args.output.mkdir(parents=True)
    catalogue=args.output/'launch.json'
    catalogue.write_text(json.dumps({'schema':1,'profile':'j71b','installations':records},indent=2)+'\n')
    files=inputs['files']
    command=[sys.executable,'-m','orchard_hardware.image.recovery']
    for key in ('loader','kernel','dtb','busybox','dropbear','dropbearkey'):
        command+=['--'+key,files[key]['path']]
    command+=['--base',str(args.base),'--base-receipt',str(args.base_receipt),
              '--modules-dir',str(modules),'--policy-dir',str(policies),
              '--launcher-catalogue',str(catalogue),'--launcher-release',args.release,
              '--output',str(args.output/'recovery.bin')]
    subprocess.run(command,check=True)
    shutil.copyfile(files['pongo']['path'],args.output/'pongo.bin')
    def artifact(name):
        path=args.output/name
        return {'file':name,'bytes':path.stat().st_size,'sha256':sha(path)}
    boot,pongo=artifact('recovery.bin'),artifact('pongo.bin')
    lines=['schema = 1','kind = "orchard.boot"','device = "j71b"',
           'boot_method = "checkm8-pongo"','min_orchard = "0.1.8"','protocol = 1',
           'name = "J71b installed-system launcher"','version = '+json.dumps(args.release)]
    for name,row in (('boot',boot),('pongo',pongo)):
        lines+=['','['+name+']',*[key+' = '+json.dumps(value) for key,value in row.items()]]
    (args.output/'boot.toml').write_text('\n'.join(lines)+'\n')
    if args.public_base_url:
        public_lines = [
            'url = ' + json.dumps(args.public_base_url.rstrip('/') + '/' + json.loads(line[7:]))
            if line.startswith('file = ') else line for line in lines]
        (args.output/'j71b-launcher.boot.toml').write_text('\n'.join(public_lines)+'\n')
    (args.output/'build-receipt.json').write_text(json.dumps({'schema':1,'release':args.release,
        'profile':'j71b','mode':'launcher','presentation':'black','inputs':inputs,'boot':boot,'pongo':pongo,
        'catalogue_sha256':sha(catalogue),'physical_qualification':'not-performed'},indent=2)+'\n')
    print('Shared launcher:',args.output/'boot.toml')


if __name__=='__main__':
    main()
