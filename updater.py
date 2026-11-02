"""Signed GitHub releases; executable-only updates, never user data."""
import base64, ctypes, hashlib, json, os, re, secrets, shutil, subprocess, sys, threading, time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

APP_ID='merkagro-local-facturas-turno'
VERSION='1.1.0'
MAX_SIZE=100*1024*1024
HOSTS={'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}

def canonical(p):return json.dumps(p,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def version(s):
    if not isinstance(s,str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}',s):raise ValueError('Versión no válida.')
    return tuple(map(int,s.split('.')))
def repository(s):
    s=str(s).strip().removeprefix('https://github.com/').removesuffix('/').removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+',s):raise ValueError('Ingresa el repositorio como propietario/nombre o su enlace de GitHub.')
    return s

def verify(envelope,current,repo,public_key):
    try:
        p=envelope['payload']
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key,validate=True)).verify(base64.b64decode(envelope['signature'],validate=True),canonical(p))
        if p['app_id']!=APP_ID or p['platform']!='windows-x64':raise ValueError()
        newer=version(p['version'])>version(current)
        expected=f'https://github.com/{repository(repo)}/releases/download/v{p["version"]}/MerkagroLocal.exe'
        if p['url']!=expected or not re.fullmatch('[a-f0-9]{64}',p['sha256']):raise ValueError()
        if type(p['size']) is not int or not 0<p['size']<=MAX_SIZE:raise ValueError()
        return p if newer else None
    except Exception as e:raise ValueError('Firma, versión u origen de actualización no válido.') from e

def verify_file(path,p):
    path=Path(path)
    if path.stat().st_size!=p['size'] or hashlib.sha256(path.read_bytes()).hexdigest()!=p['sha256']:raise ValueError('Descarga incompleta o modificada.')

def safe_url(url):
    p=urlparse(url)
    if p.scheme!='https' or p.hostname not in HOSTS or p.username or p.password:raise ValueError('Origen de descarga no autorizado.')
class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        safe_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)
def open_url(url):
    safe_url(url)
    return urllib.request.build_opener(Redirect()).open(urllib.request.Request(url,headers={'User-Agent':'MerkagroLocal-Updater/1','Cache-Control':'no-cache'}),timeout=20)

class Manager:
    def __init__(self,data,assets):
        self.data=Path(data);self.assets=Path(assets);self.folder=self.data/'actualizaciones';self.folder.mkdir(exist_ok=True)
        self.channel=json.loads((self.assets/'update_channel.json').read_text())
        self.repo=self.channel.get('repository','');self.public_key=self.channel['public_key']
        self.lock=threading.RLock();self.stop=threading.Event();self.busy=False;self.envelope=None;self.binary=None
        try:self.repo=repository(json.loads((self.folder/'config.json').read_text())['repository'])
        except (OSError,ValueError,KeyError):pass
        self.info={'version':VERSION,'repository':self.repo,'status':'pendiente','message':'Configura el repositorio de actualizaciones.' if not self.repo else 'Se buscarán actualizaciones automáticamente.'}
    def status(self):
        with self.lock:return dict(self.info)
    def configure(self,repo):
        repo=repository(repo)
        with self.lock:
            if self.busy:raise ValueError('Espera a que termine la comprobación actual.')
            tmp=self.folder/'config.tmp';tmp.write_text(json.dumps({'repository':repo}));tmp.replace(self.folder/'config.json')
            self.repo=repo;self.envelope=None;self.binary=None;self.info.update(repository=repo,status='pendiente',message='Repositorio guardado. Comprobando…')
        self.check_async()
    def check_async(self):threading.Thread(target=self.check,daemon=True).start()
    def check(self):
        with self.lock:
            if self.busy or not self.repo or self.stop.is_set():return
            self.busy=True;repo=self.repo;self.info.update(status='comprobando',message='Buscando actualizaciones…')
        try:
            with open_url(f'https://github.com/{repo}/releases/latest/download/update.json') as r:
                raw=r.read(65537)
            if len(raw)>65536:raise ValueError('Aviso demasiado grande.')
            env=json.loads(raw);p=verify(env,VERSION,repo,self.public_key)
            if p is None:
                with self.lock:self.info.update(status='al_dia',message='El programa está actualizado.')
                return
            with self.lock:self.info.update(status='descargando',message='Descargando versión '+p['version']+'…')
            dest=self.folder/p['version'];dest.mkdir(exist_ok=True)
            binary=dest/'MerkagroLocal.exe';part=dest/'descarga.part'
            cached=False
            if binary.exists():
                try:verify_file(binary,p);cached=True
                except ValueError:pass
            if not cached:
                with open_url(p['url']) as r,part.open('wb') as f:
                    size=0
                    while True:
                        if self.stop.is_set():raise ValueError('Comprobación detenida.')
                        block=r.read(1024*1024)
                        if not block:break
                        size+=len(block)
                        if size>p['size']:raise ValueError('Tamaño de descarga no válido.')
                        f.write(block)
                verify_file(part,p);part.replace(binary)
            with self.lock:
                self.envelope=env;self.binary=binary;self.info.update(status='lista',available=p['version'],message='Versión '+p['version']+' lista. Se instalará al cerrar el programa.')
        except Exception:
            with self.lock:self.info.update(status='error',message='No se pudo comprobar o verificar la actualización. Puedes continuar trabajando sin internet.')
        finally:
            with self.lock:self.busy=False
    def start(self):
        if os.environ.get('MERKAGRO_DISABLE_UPDATES')=='1':return
        def loop():
            while not self.stop.is_set():
                self.check()
                if self.stop.wait(4*3600):break
        threading.Thread(target=loop,daemon=True).start()
    def ready(self):return getattr(sys,'frozen',False) and self.status()['status']=='lista'
    def launch(self):
        with self.lock:
            if not self.ready():return
            p=verify(self.envelope,VERSION,self.repo,self.public_key);verify_file(self.binary,p)
            folder=self.folder/('ayudante-'+secrets.token_hex(5));folder.mkdir()
            helper=folder/'MerkagroActualizador.exe';shutil.copy2(sys.executable,helper)
            ticket={'envelope':self.envelope,'binary':str(self.binary),'repository':self.repo,'target':str(Path(sys.executable).resolve()),'pid':os.getpid(),'current':VERSION,'old_hash':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),'data':str(self.data),'no_browser':'--no-browser' in sys.argv}
            req=folder/'solicitud.json';req.write_text(json.dumps(ticket))
            subprocess.Popen([str(helper),'--apply-update',str(req)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True)

def replace_binary(target,binary,p,old_hash):
    target=Path(target);binary=Path(binary)
    if target.name not in ('MERKAGRO Local.exe','MerkagroLocal.exe'):raise ValueError('Ejecutable de destino no reconocido.')
    verify_file(binary,p)
    if hashlib.sha256(target.read_bytes()).hexdigest()!=old_hash:raise ValueError('El programa de destino cambió; se conserva sin reemplazar.')
    stage=target.with_name(target.name+'.nuevo');previous=target.with_name(target.name+'.anterior')
    shutil.copy2(binary,stage);verify_file(stage,p)
    # Retry Windows executable locks, including the PyInstaller parent bootloader.
    for i in range(120):
        try:os.replace(target,previous);break
        except PermissionError:time.sleep(.5)
    else:raise ValueError('El programa sigue abierto en otra ventana. No se modificaron los datos.')
    try:os.replace(stage,target)
    except Exception:
        os.replace(previous,target)
        raise
    return previous

def apply_worker(ticket_path,assets):
    ticket=json.loads(Path(ticket_path).read_text());data=Path(ticket['data']);log=data/'actualizaciones'/'instalacion.log'
    try:
        channel=json.loads((Path(assets)/'update_channel.json').read_text())
        p=verify(ticket['envelope'],ticket['current'],ticket['repository'],channel['public_key'])
        if not p:raise ValueError('No hay una versión posterior.')
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_bool,ctypes.c_ulong];k.OpenProcess.restype=ctypes.c_void_p
        k.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];k.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=k.OpenProcess(0x100000,False,int(ticket['pid']))
        if handle:
            try:
                if k.WaitForSingleObject(handle,60000)!=0:raise ValueError('El programa no terminó de cerrar.')
            finally:k.CloseHandle(handle)
        previous=replace_binary(ticket['target'],ticket['binary'],p,ticket['old_hash'])
        args=[ticket['target']]
        if ticket.get('no_browser'):args.append('--no-browser')
        try:subprocess.Popen(args,close_fds=True)
        except Exception:
            os.replace(previous,ticket['target'])
            raise
        log.write_text('Actualización instalada: '+p['version'],encoding='utf-8')
    except Exception as e:
        log.parent.mkdir(parents=True,exist_ok=True);log.write_text('No se completó la actualización: '+str(e),encoding='utf-8')
