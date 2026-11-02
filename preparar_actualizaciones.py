"""Developer-only signing tool. Private key never included in application builds."""
import argparse,base64,hashlib,json,os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from updater import APP_ID,canonical,repository,version

KEY=Path.home()/'.codex'/'merkagro-signing'/'release-private.pem'
def initialize(repo=''):
    KEY.parent.mkdir(parents=True,exist_ok=True)
    if not KEY.exists():
        key=Ed25519PrivateKey.generate()
        KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    key=serialization.load_pem_private_key(KEY.read_bytes(),password=None)
    public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
    channel=Path(__file__).parent/'assets'/'update_channel.json'
    if channel.exists() and not repo:repo=json.loads(channel.read_text()).get('repository','')
    channel.write_text(json.dumps({'repository':repository(repo) if repo else '', 'public_key':public},indent=2))
    print('Canal y clave pública preparados. La clave privada permanece fuera del proyecto.')

def sign(exe,ver,repo,out):
    version(ver);repo=repository(repo);exe=Path(exe);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    keydata=os.environ.get('MERKAGRO_SIGNING_KEY_PEM')
    key=serialization.load_pem_private_key(keydata.encode() if keydata else KEY.read_bytes(),password=None)
    payload={'app_id':APP_ID,'version':ver,'platform':'windows-x64','url':f'https://github.com/{repo}/releases/download/v{ver}/MerkagroLocal.exe','sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'size':exe.stat().st_size}
    env={'payload':payload,'signature':base64.b64encode(key.sign(canonical(payload))).decode()}
    (out/'update.json').write_text(json.dumps(env,indent=2));print('Manifiesto firmado generado.')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--init',action='store_true');p.add_argument('--repo',default='');p.add_argument('--exe');p.add_argument('--version');p.add_argument('--out',default='release')
    a=p.parse_args()
    if a.init:initialize(a.repo)
    else:sign(a.exe,a.version,a.repo,a.out)
