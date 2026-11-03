import hashlib,json,re,zipfile,xml.etree.ElementTree as ET,unicodedata
from datetime import datetime,timedelta,date
from pathlib import Path
from ledger import valid_date,integer
CATEGORIES=['Servicios','Nómina','Arriendo','Transporte','Alimentación','Mantenimiento','Otros']

def read_original(path):
    """Read original archived workbook with standard library; no customer data in releases."""
    n={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    z=zipfile.ZipFile(path)
    strings=[]
    if 'xl/sharedStrings.xml' in z.namelist():
        strings=[''.join(t.text or '' for t in si.findall('.//m:t',n)) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',n)]
    rels={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
    sheets={s.attrib['name']:rels[s.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']] for s in ET.fromstring(z.read('xl/workbook.xml')).findall('m:sheets/m:sheet',n)}
    months={'ENERO':1,'FEBRERO':2,'MARZO':3,'ABRIL':4,'MAYO':5,'JUNIO':6,'JULIO':7,'AGOSTO':8,'SEPTIEMBRE':9,'OCTUBRE':10,'NOVIEMBRE':11,'DICIEMBRE':12}
    def norm(s):return unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().upper()
    def parsed(v):
        if isinstance(v,(int,float)):
            return (datetime(1899,12,30)+timedelta(days=v)).date().isoformat(),'Fecha del Excel'
        s=norm(v)
        for a,b in [('OCCTUBRE','OCTUBRE'),('SEPTIENBRE','SEPTIEMBRE'),('AOSTO','AGOSTO'),('GOSTO','AGOSTO')]:
            if b not in s:s=s.replace(a,b)
        s=re.sub(r'\bSEPT\b','SEPTIEMBRE',s)
        for name,month in months.items():
            if name not in s:continue
            head,tail=s.split(name,1);ds=re.findall(r'\d+',head);m=re.search(r'\d+',tail);day=int(ds[-1]) if ds else int(m[0]) if m else 0
            y=re.search(r'20\d{2}',s)
            try:return date(int(y[0]) if y else 2026,month,day).isoformat(),'Texto normalizado; '+('año explícito' if y else 'año 2026 del libro')
            except ValueError:pass
        return None,'Fecha sin confirmar'
    rows=[]
    for sheet,kind in [('COMPRAS CONTADO','purchase'),('GASTOS','expense')]:
        target=sheets[sheet];target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
        month=None
        for row in ET.fromstring(z.read(target)).findall('m:sheetData/m:row',n):
            cells={};formulas=set()
            for cell in row.findall('m:c',n):
                col=re.sub(r'\d','',cell.attrib['r']);v=cell.find('m:v',n);value=v.text if v is not None else None
                typ=cell.attrib.get('t')
                if typ=='s':value=strings[int(value)] if value is not None else ''
                elif typ=='inlineStr':value=''.join(t.text or '' for t in cell.findall('.//m:t',n))
                elif typ not in ['str','e'] and value is not None:
                    try:value=float(value);value=int(value) if value.is_integer() else value
                    except ValueError:pass
                cells[col]=value
                if cell.find('m:f',n) is not None:formulas.add(col)
            amount=cells.get('C')
            if not isinstance(amount,(int,float)) or amount<=0:continue
            if int(amount)!=amount:raise ValueError('Importe con decimales en el archivo original.')
            dt,note=parsed(cells.get('A'))
            if dt:month=dt[:7]
            if not month:raise ValueError('Mes sin identificar en '+sheet)
            concept=str(cells.get('B') or '').strip();text=norm(concept);category='Compras' if kind=='purchase' else 'Otros'
            if kind=='expense':
                for cat,pattern in [('Nómina','EMPL|NOMINA|SUELDO|SALARIO'),('Servicios','SERVIC|INTERNET|INTERN[T]?|ENERGIA|AGUA'),('Arriendo','ARRENDO|ARRIEND'),('Transporte','GASOLINA|TRASPORTE|TRANSPORTE'),('Alimentación','CARNE|ALMUER|REFRI|HUEVO|PESCADO|VERDURA|YUCA|DESAY|LACTEO'),('Mantenimiento','ARREG|MANTEN|REPUEST|MAESTRO|FERRETER|CEMENTO|SERAM|PEGACOR')]:
                    if re.search(pattern,text):category=cat;break
            original=cells.get('A');original=date.fromisoformat(dt).isoformat() if isinstance(original,(int,float)) else str(original or '')
            rows.append(dict(kind=kind,date=dt,month=month,concept=concept,category=category,amount=int(amount),method='Histórico',reference='',notes='' if 'D' in formulas else str(cells.get('D') or ''),original_date=original,date_note=note if dt else 'Mes inferido del bloque; día sin confirmar',source_sheet=sheet,source_row=int(row.attrib['r'])))
    z.close()
    return rows

def migrate(c,assets,data=None):
    c.execute('''CREATE TABLE IF NOT EXISTS monthly_movements(id INTEGER PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('purchase','expense')),date TEXT,month TEXT NOT NULL,concept TEXT NOT NULL,category TEXT NOT NULL,amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>0),method TEXT NOT NULL DEFAULT '',reference TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',original_date TEXT NOT NULL DEFAULT '',date_note TEXT NOT NULL DEFAULT '',source_sheet TEXT,source_row INTEGER,version INTEGER NOT NULL DEFAULT 1,request_key TEXT UNIQUE,payload_hash TEXT,UNIQUE(kind,source_sheet,source_row))''')
    if c.execute("SELECT 1 FROM meta WHERE key='monthly_import_v1'").fetchone():return
    originals=list((Path(data)/'originales').glob('FACTURAS*.xlsx')) if data else []
    imported=read_original(originals[0]) if originals else json.loads((Path(assets)/'monthly_seed.json').read_text(encoding='utf-8'))
    for x in imported:
        c.execute('''INSERT OR IGNORE INTO monthly_movements(kind,date,month,concept,category,amount,method,reference,notes,original_date,date_note,source_sheet,source_row) VALUES(:kind,:date,:month,:concept,:category,:amount,:method,:reference,:notes,:original_date,:date_note,:source_sheet,:source_row)''',x)
    c.execute("INSERT INTO meta VALUES('monthly_import_v1','2026-11-local')")

def view(c,kind):
    if kind not in ['purchase','expense']:raise ValueError('Módulo no válido.')
    return {'rows':[dict(r) for r in c.execute('SELECT * FROM monthly_movements WHERE kind=? ORDER BY month DESC,COALESCE(date,month) DESC,id DESC',(kind,))],'months':[dict(r) for r in c.execute('SELECT month,SUM(amount) AS total,COUNT(*) AS count FROM monthly_movements WHERE kind=? GROUP BY month ORDER BY month DESC',(kind,))],'categories':CATEGORIES if kind=='expense' else ['Compras']}

def save(c,p,today,stamp):
    kind=p.get('kind')
    if kind not in ['purchase','expense']:raise ValueError('Módulo no válido.')
    dt=valid_date(p.get('date'));amount=integer(p.get('amount'))
    concept=str(p.get('concept','')).strip();category=p.get('category');notes=str(p.get('notes','')).strip();reference=str(p.get('reference','')).strip();method=p.get('method','')
    if not concept or len(concept)>300 or len(notes)>2000 or len(reference)>100:raise ValueError('Completa un concepto válido y revisa la extensión de las observaciones.')
    if not amount:raise ValueError('El valor debe ser mayor a cero.')
    if category not in (CATEGORIES if kind=='expense' else ['Compras']):raise ValueError('Selecciona una categoría válida.')
    if method not in ['EFECTIVO','TRANSFERENCIA','OTRO','Histórico']:raise ValueError('Medio de pago no válido.')
    if p.get('id'):
        old=c.execute('SELECT * FROM monthly_movements WHERE id=? AND kind=?',(p['id'],kind)).fetchone()
        if not old or old['version']!=p.get('version'):raise ValueError('El registro cambió. Actualiza antes de corregirlo.')
        c.execute('UPDATE monthly_movements SET date=?,month=?,concept=?,category=?,amount=?,method=?,reference=?,notes=?,date_note=?,version=version+1 WHERE id=?',(dt,dt[:7],concept,category,amount,method,reference,notes,'Fecha corregida por usuario',old['id']))
        eid=old['id'];before=dict(old)
    else:
        k=p.get('operation_key','');digest=hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest()
        if not isinstance(k,str) or not re.fullmatch('[A-Za-z0-9-]{16,100}',k):raise ValueError('Abre de nuevo el formulario.')
        old=c.execute('SELECT * FROM monthly_movements WHERE request_key=?',(k,)).fetchone()
        if old:
            if old['payload_hash']!=digest:raise ValueError('La operación ya existe con otros valores.')
            return {'ok':True,'id':old['id'],'month':old['month'],'repeated':True}
        cur=c.execute('INSERT INTO monthly_movements(kind,date,month,concept,category,amount,method,reference,notes,date_note,request_key,payload_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(kind,dt,dt[:7],concept,category,amount,method,reference,notes,'Fecha ingresada por usuario',k,digest))
        eid=cur.lastrowid;before=None
    c.execute('INSERT INTO audit(created,action,payload) VALUES(?,?,?)',(stamp,'Compra de contado' if kind=='purchase' else 'Gasto del negocio',json.dumps({'before':before,'after':p},ensure_ascii=False)))
    return {'ok':True,'id':eid,'month':dt[:7]}
