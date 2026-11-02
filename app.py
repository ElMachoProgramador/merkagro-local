"""MERKAGRO Local. Runtime: Python standard library only."""
import csv
import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import webbrowser
import zipfile
import updater
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ASSETS=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'assets'
DATA=Path(os.environ.get('MERKAGRO_DATA_DIR',Path(os.environ.get('LOCALAPPDATA',Path.home()))/'MerkagroLocal'))
DENOMS=(100000,50000,20000,10000,5000,2000,1000,500,200,100)
TOKEN=secrets.token_urlsafe(32)
LOCK=threading.RLock()
UPDATES=None
CLOSING=False

class ClosedConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def now():
    return datetime.now(timezone(timedelta(hours=-5)))

def connect():
    c=sqlite3.connect(DATA/'merkagro.sqlite3',timeout=30,factory=ClosedConnection)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def backup(destination=None):
    destination=Path(destination or DATA/'copias'/f'Merkagro-{now():%Y%m%d-%H%M%S-%f}.sqlite3')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with LOCK, connect() as src, sqlite3.connect(destination,factory=ClosedConnection) as dst:
        src.backup(dst)
    return destination

def initialize():
    DATA.mkdir(parents=True,exist_ok=True)
    with LOCK,connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY,name TEXT NOT NULL,sheet TEXT NOT NULL UNIQUE,in_summary INTEGER NOT NULL,original_balance INTEGER NOT NULL,original_cell TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY,supplier_id INTEGER NOT NULL REFERENCES suppliers(id),date TEXT,original_date TEXT NOT NULL DEFAULT '',invoice TEXT NOT NULL DEFAULT '',amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>=0),payment INTEGER NOT NULL CHECK(typeof(payment)='integer' AND payment>=0),notes TEXT NOT NULL DEFAULT '',source_row INTEGER,source_sheet TEXT,version INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS cash_draft(id INTEGER PRIMARY KEY CHECK(id=1),counts TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cash_reports(id INTEGER PRIMARY KEY,created TEXT NOT NULL,label TEXT NOT NULL,counts TEXT NOT NULL,total INTEGER NOT NULL,base INTEGER NOT NULL,revenue INTEGER NOT NULL,origin TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,created TEXT NOT NULL,action TEXT NOT NULL,payload TEXT NOT NULL);
        ''')
        if not c.execute("SELECT 1 FROM meta WHERE key='seed'").fetchone():
            seed=json.loads((ASSETS/'seed.json').read_text(encoding='utf-8'))
            for s in seed['suppliers']:
                c.execute('INSERT INTO suppliers(id,name,sheet,in_summary,original_balance,original_cell) VALUES(:id,:name,:sheet,:in_summary,:original_balance,:original_cell)',s)
            for e in seed['entries']:
                c.execute('INSERT INTO entries(supplier_id,date,original_date,invoice,amount,payment,notes,source_row,source_sheet) VALUES(:supplier_id,:date,:original_date,:invoice,:amount,:payment,:notes,:source_row,:source_sheet)',e)
            for x in seed['cash']:
                counts=x['counts']; totals=cash_totals(counts)
                c.execute('INSERT INTO cash_reports(created,label,counts,total,base,revenue,origin) VALUES(?,?,?,?,?,?,?)',(now().isoformat(),x['name'],json.dumps(counts),totals['total'],totals['base'],totals['revenue'],'Excel · fecha del conteo no indicada'))
            c.execute('INSERT INTO cash_draft(id,counts,updated) VALUES(1,?,?)',(json.dumps(seed['cash'][0]['counts']),now().isoformat()))
            c.execute("INSERT INTO meta VALUES('seed',?)",(json.dumps({'sources':seed['sources'],'summary_balance':seed['summary_balance'],'imported':now().isoformat()},ensure_ascii=False),))
    originals=DATA/'originales'
    originals.mkdir(exist_ok=True)
    for p in ASSETS.glob('*.xlsx'):
        if not (originals/p.name).exists():shutil.copy2(p,originals/p.name)
    day=DATA/'copias'/f'Automatico-{now():%Y-%m-%d}.sqlite3'
    if not day.exists():backup(day)

def whole(x,name):
    if type(x) is not int or x<0 or x>999999999999999:
        raise ValueError(f'{name}: ingresa un número entero, sin decimales y mayor o igual a cero.')
    return x

def cash_totals(counts):
    if not isinstance(counts,dict) or set(counts)!={str(d) for d in DENOMS}:
        raise ValueError('Las denominaciones son fijas.')
    for k,v in counts.items():
        whole(v,'Cantidad')
        if v>10000000:raise ValueError('Cantidad demasiado grande.')
    bills=sum(d*counts[str(d)] for d in DENOMS[:6])
    coins=sum(d*counts[str(d)] for d in DENOMS[6:])
    revenue=100000*counts['100000']+50000*counts['50000']
    return {'bills':bills,'coins':coins,'total':bills+coins,'base':bills+coins-revenue,'revenue':revenue}

def state():
    with connect() as c:
        suppliers=[dict(r) for r in c.execute('''SELECT s.*,COALESCE(SUM(e.amount),0) AS charges,COALESCE(SUM(e.payment),0) AS payments,COALESCE(SUM(e.amount-e.payment),0) AS balance,COUNT(e.id) AS count FROM suppliers s LEFT JOIN entries e ON s.id=e.supplier_id GROUP BY s.id ORDER BY s.name''')]
        draft=dict(c.execute('SELECT * FROM cash_draft WHERE id=1').fetchone())
        draft['counts']=json.loads(draft['counts'])
        reports=[dict(r) for r in c.execute('SELECT id,created,label,total,base,revenue,origin FROM cash_reports ORDER BY id DESC')]
        info=json.loads(c.execute("SELECT value FROM meta WHERE key='seed'").fetchone()[0])
        return {'suppliers':suppliers,'cash':draft,'reports':reports,'info':info,'today':now().date().isoformat(),'data_path':str(DATA),'totals':cash_totals(draft['counts']),'updates':UPDATES.status() if UPDATES else {'version':updater.VERSION,'status':'pendiente','repository':'','message':'Actualizaciones disponibles desde el ejecutable.'}}

def audit(c,action,payload):
    c.execute('INSERT INTO audit(created,action,payload) VALUES(?,?,?)',(now().isoformat(),action,json.dumps(payload,ensure_ascii=False)))

def save_entry(p):
    allowed={'id','supplier_id','date','invoice','amount','payment','notes','version'}
    if set(p)-allowed:raise ValueError('Campo no permitido.')
    try:dt=datetime.strptime(p['date'],'%Y-%m-%d').date().isoformat()
    except (KeyError,ValueError,TypeError):raise ValueError('Ingresa una fecha válida.')
    amount=whole(p.get('amount'),'Valor de factura');payment=whole(p.get('payment'),'Abono')
    if not amount and not payment:raise ValueError('Ingresa el valor de la factura o el abono.')
    inv=str(p.get('invoice','')).strip();notes=str(p.get('notes','')).strip()
    if len(inv)>120 or len(notes)>3000:raise ValueError('El texto es demasiado largo.')
    if not inv:raise ValueError('Ingresa el número o referencia de factura.')
    with LOCK,connect() as c:
        sid=p.get('supplier_id')
        if CLOSING:raise ValueError('El programa se está cerrando. No se guardó este movimiento.')
        if type(sid) is not int or not c.execute('SELECT 1 FROM suppliers WHERE id=?',(sid,)).fetchone():raise ValueError('Selecciona un proveedor.')
        if p.get('id'):
            before=c.execute('SELECT * FROM entries WHERE id=?',(p['id'],)).fetchone()
            if not before:raise ValueError('Registro no encontrado.')
            if before['version']!=p.get('version'):raise ValueError('Este registro cambió en otra ventana. Actualiza antes de editarlo.')
            c.execute('UPDATE entries SET supplier_id=?,date=?,invoice=?,amount=?,payment=?,notes=?,version=version+1 WHERE id=?',(sid,dt,inv,amount,payment,notes,p['id']))
            audit(c,'editar movimiento',{'before':dict(before),'after':p})
        else:
            c.execute('INSERT INTO entries(supplier_id,date,invoice,amount,payment,notes) VALUES(?,?,?,?,?,?)',(sid,dt,inv,amount,payment,notes))
            audit(c,'nuevo movimiento',p)
    return {'ok':True}

def save_cash(p):
    if set(p)-{'counts','version','snapshot'}:raise ValueError('Solo se pueden modificar las cantidades.')
    totals=cash_totals(p.get('counts'))
    with LOCK,connect() as c:
        old=c.execute('SELECT * FROM cash_draft WHERE id=1').fetchone()
        if CLOSING:raise ValueError('El programa se está cerrando. No se guardó este conteo.')
        if p.get('version')!=old['version']:raise ValueError('El conteo cambió en otra ventana. Actualiza para consultar la versión guardada.')
        c.execute('UPDATE cash_draft SET counts=?,version=version+1,updated=? WHERE id=1',(json.dumps(p['counts']),now().isoformat()))
        audit(c,'guardar cantidades',{'before':json.loads(old['counts']),'after':p['counts']})
        if p.get('snapshot'):
            c.execute('INSERT INTO cash_reports(created,label,counts,total,base,revenue,origin) VALUES(?,?,?,?,?,?,?)',(now().isoformat(),f'Reporte {now():%d/%m/%Y %H:%M}',json.dumps(p['counts']),totals['total'],totals['base'],totals['revenue'],'Guardado local'))
        return {'ok':True,'version':old['version']+1,'totals':totals}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def send(self,data,status=200,mime='application/json; charset=utf-8',filename=None):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode('utf-8')
        elif isinstance(data,str):data=data.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        if filename:self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return self.headers.get('X-Merkagro-Token')==TOKEN

    def do_GET(self):
        try:
            # Fixed loopback Host prevents DNS rebinding and remote host access.
            if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':return self.send({'error':'Acceso local requerido.'},403)
            if self.path=='/':
                return self.send((ASSETS/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN),mime='text/html; charset=utf-8')
            if self.path=='/favicon.ico':return self.send(b'',204,'image/x-icon')
            if self.path=='/api/state':return self.send(state())
            if self.path.startswith('/api/entries/'):
                sid=int(self.path.rsplit('/',1)[1])
                with connect() as c:
                    return self.send([dict(r) for r in c.execute('SELECT * FROM entries WHERE supplier_id=? ORDER BY id',(sid,))])
            if self.path.startswith('/api/report/'):
                rid=int(self.path.rsplit('/',1)[1])
                with connect() as c:
                    r=c.execute('SELECT * FROM cash_reports WHERE id=?',(rid,)).fetchone()
                    if not r:return self.send({'error':'Reporte no encontrado.'},404)
                    row=dict(r);row['counts']=json.loads(row['counts']);return self.send(row)
            return self.send({'error':'No encontrado.'},404)
        except Exception:
            self.send({'error':'No se pudo leer la información. Los datos guardados se conservan.'},500)

    def do_POST(self):
        global CLOSING
        if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}' or not self.authorized():return self.send({'error':'Sesión no válida. Abre nuevamente la aplicación.'},403)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n>200000:raise ValueError('Solicitud demasiado grande.')
            p=json.loads(self.rfile.read(n) or b'{}')
            if not isinstance(p,dict):raise ValueError('Datos no válidos.')
            if CLOSING:return self.send({'error':'El programa se está cerrando para actualizar. No se guardó este cambio.'},503)
            if self.path=='/api/updates':
                if not UPDATES:raise ValueError('Abre el programa desde el ejecutable.')
                if 'repository' in p:UPDATES.configure(p['repository'])
                else:UPDATES.check_async()
                return self.send(UPDATES.status())
            if self.path=='/api/entry':return self.send(save_entry(p))
            if self.path=='/api/cash':return self.send(save_cash(p))
            if self.path=='/api/exit':
                with LOCK:
                    CLOSING=True
                    backup()
                try:
                    if UPDATES:UPDATES.launch()
                except Exception:
                    CLOSING=False
                    raise ValueError('No se pudo preparar la actualización. Tus datos están guardados. Revisa los permisos de la carpeta del programa e inténtalo de nuevo.')
                self.send({'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if self.path=='/api/supplier':
                name=str(p.get('name','')).strip()
                if not name or len(name)>140:raise ValueError('Ingresa un nombre de proveedor válido.')
                with LOCK,connect() as c:
                    if c.execute('SELECT 1 FROM suppliers WHERE lower(trim(name))=lower(?)',(name,)).fetchone():raise ValueError('Ya existe un proveedor con ese nombre.')
                    sheet='Nuevo-'+secrets.token_hex(8)
                    cursor=c.execute('INSERT INTO suppliers(name,sheet,in_summary,original_balance,original_cell) VALUES(?,?,1,0,\'\')',(name,sheet))
                    audit(c,'nuevo proveedor',{'name':name});return self.send({'id':cursor.lastrowid})
            if self.path=='/api/backup':
                path=backup()
                data=io.BytesIO()
                with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as z:
                    z.write(path,'merkagro.sqlite3')
                    for f in (DATA/'originales').glob('*.xlsx'):z.write(f,'originales/'+f.name)
                    z.writestr('LEEME.txt','Copia de MERKAGRO Local. Contiene la base de datos completa y los Excel originales. Para recuperar: cierra la aplicación, conserva copia del estado actual y reemplaza merkagro.sqlite3 en la carpeta de datos indicada en la aplicación.\n')
                return self.send(data.getvalue(),mime='application/zip',filename=f'Merkagro-copia-{now():%Y%m%d-%H%M%S}.zip')
            if self.path=='/api/export':
                buf=io.StringIO(newline='');writer=csv.writer(buf,delimiter=';')
                # Prevent CSV formula injection from user-controlled identifiers.
                def safe(x):
                    s=str(x or '')
                    return "'"+s if s.lstrip().startswith(('=','+','-','@','\t','\r')) else s
                sid=p.get('supplier_id')
                with connect() as c:
                    if sid:
                        writer.writerow(['Proveedor','Fecha','Fecha original','Factura / referencia','Valor factura COP','Abono COP','Observaciones','Hoja origen','Fila origen'])
                        for r in c.execute('SELECT s.name,e.* FROM entries e JOIN suppliers s ON e.supplier_id=s.id WHERE s.id=? ORDER BY e.id',(sid,)):
                            writer.writerow([safe(r['name']),r['date'] or '',safe(r['original_date']),safe(r['invoice']),r['amount'],r['payment'],safe(r['notes']),safe(r['source_sheet']),r['source_row'] or ''])
                    else:
                        writer.writerow(['Proveedor','Cargos COP','Abonos COP','Saldo COP','En resumen original'])
                        for s in state()['suppliers']:writer.writerow([safe(s['name']),s['charges'],s['payments'],s['balance'],'Sí' if s['in_summary'] else 'No'])
                return self.send(('\ufeff'+buf.getvalue()).encode('utf-8'),mime='text/csv; charset=utf-8',filename='Merkagro-facturas.csv')
            return self.send({'error':'Operación no encontrada.'},404)
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e:self.send({'error':str(e)},400)
        except sqlite3.Error:self.send({'error':'No se pudo guardar. Verifica que la carpeta de datos esté disponible e inténtalo de nuevo.'},500)

def main():
    global UPDATES
    if '--apply-update' in sys.argv:
        updater.apply_worker(sys.argv[sys.argv.index('--apply-update')+1],ASSETS)
        return
    initialize()
    UPDATES=updater.Manager(DATA,ASSETS)
    UPDATES.start()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    url=f'http://127.0.0.1:{server.server_port}'
    if '--no-browser' not in sys.argv:
        webbrowser.open(url)
    port_file=os.environ.get('MERKAGRO_PORT_FILE')
    if port_file:Path(port_file).write_text(url,encoding='utf-8')
    try:server.serve_forever()
    finally:
        UPDATES.stop.set()
        server.server_close()

if __name__=='__main__':
    try:main()
    except Exception as e:
        import tkinter.messagebox
        tkinter.messagebox.showerror('MERKAGRO Local',f'No se pudo iniciar el programa:\n{e}')
