"""Invoice-specific allocations. No guessed allocation of ambiguous legacy payments."""
import hashlib,json,re,unicodedata
from datetime import date,datetime

def key(s):
    return re.sub(r'[^A-Z0-9]','',unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode().upper())

def valid_date(value):
    try:return datetime.strptime(value,'%Y-%m-%d').date().isoformat()
    except (TypeError,ValueError):raise ValueError('Ingresa una fecha de recepción o pago válida.')

def integer(value,label='Valor'):
    if type(value) is not int or not 0<=value<=999999999999:
        raise ValueError(label+': ingresa pesos enteros, sin decimales y sin valores negativos.')
    return value

def money(n):return '$ '+f'{n:,}'.replace(',','.')

def migrate(c,stamp):
    for name,definition in [('kind',"TEXT NOT NULL DEFAULT 'legacy'"),('method',"TEXT NOT NULL DEFAULT ''"),('receipt',"TEXT NOT NULL DEFAULT ''")]:
        if name not in {r['name'] for r in c.execute('PRAGMA table_info(entries)')}:
            c.execute(f'ALTER TABLE entries ADD COLUMN {name} {definition}')
    c.execute('CREATE TABLE IF NOT EXISTS allocations(payment_id INTEGER NOT NULL REFERENCES entries(id),invoice_id INTEGER NOT NULL REFERENCES entries(id),amount INTEGER NOT NULL CHECK(typeof(amount)=\'integer\' AND amount>0),PRIMARY KEY(payment_id,invoice_id))')
    c.execute('CREATE TABLE IF NOT EXISTS operation_keys(key TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,entry_id INTEGER NOT NULL REFERENCES entries(id))')
    if c.execute("SELECT 1 FROM meta WHERE key='invoice_allocations_v1'").fetchone():return
    invoices=[dict(r) for r in c.execute('SELECT * FROM entries WHERE amount>0 ORDER BY id')]
    references={}
    for inv in invoices:
        if inv['invoice'].strip():references.setdefault((inv['supplier_id'],key(inv['invoice'])),[]).append(inv['id'])
    for pay in c.execute('SELECT * FROM entries WHERE payment>0 ORDER BY id').fetchall():
        available=pay['payment']
        candidates=[pay['id']] if pay['amount']>0 else references.get((pay['supplier_id'],key(pay['invoice'])),[]) if pay['invoice'].strip() else []
        if len(candidates)!=1:continue
        inv=c.execute('SELECT * FROM entries WHERE id=?',(candidates[0],)).fetchone()
        paid=c.execute('SELECT COALESCE(SUM(amount),0) FROM allocations WHERE invoice_id=?',(inv['id'],)).fetchone()[0]
        remaining=max(0,inv['amount']-paid)
        amount=min(available,remaining) if pay['id']==inv['id'] else available if available<=remaining else 0
        if pay['date'] and inv['date'] and pay['date']<inv['date']:amount=0
        if amount:
            c.execute('INSERT OR IGNORE INTO allocations VALUES(?,?,?)',(pay['id'],inv['id'],amount))
    c.execute("INSERT INTO meta VALUES('invoice_allocations_v1',?)",(stamp,))
    c.execute('INSERT INTO audit(created,action,payload) VALUES(?,?,?)',(stamp,'Migración conservadora de facturas','{"regla":"Solo referencias únicas exactas o cargo y abono en la misma fila. Saldos conservados."}'))

def view(c,supplier_id,today):
    supplier=c.execute('SELECT * FROM suppliers WHERE id=?',(supplier_id,)).fetchone()
    if not supplier:raise ValueError('Proveedor no encontrado.')
    payments=[dict(r) for r in c.execute('''SELECT e.*,COALESCE((SELECT SUM(a.amount) FROM allocations a WHERE a.payment_id=e.id),0) AS applied FROM entries e WHERE e.supplier_id=? AND e.payment>0 ORDER BY e.id DESC''',(supplier_id,))]
    unassigned=sum(p['payment']-p['applied'] for p in payments)
    invoices=[]
    for row in c.execute('''SELECT e.*,COALESCE((SELECT SUM(a.amount) FROM allocations a WHERE a.invoice_id=e.id),0) AS paid FROM entries e WHERE e.supplier_id=? AND e.amount>0 ORDER BY COALESCE(e.date,'9999'),e.id''',(supplier_id,)):
        inv=dict(row);inv['remaining']=inv['amount']-inv['paid']
        inv['display_ref']=inv['invoice'] or f'Sin número · fila {inv["source_row"] or inv["id"]}'
        age=None
        try:age=(date.fromisoformat(today)-date.fromisoformat(inv['date'])).days
        except (TypeError,ValueError):pass
        inv['age_days']=age
        inv['overdue_days']=max(0,age-30) if age is not None else None
        inv['needs_reconciliation']=inv['remaining']>0 and inv['kind']=='legacy' and unassigned>0
        inv['status']='paid' if inv['remaining']==0 else 'review' if inv['needs_reconciliation'] else 'unknown' if age is None else 'future' if age<0 else 'green' if age<=25 else 'yellow' if age<=30 else 'red'
        inv['allocations']=[dict(a) for a in c.execute('''SELECT a.amount,e.date,e.receipt,e.method,e.notes,e.id AS payment_id FROM allocations a JOIN entries e ON e.id=a.payment_id WHERE a.invoice_id=? ORDER BY a.payment_id''',(inv['id'],))]
        invoices.append(inv)
    return {'supplier':dict(supplier),'invoices':invoices,'payments':payments,'unassigned':unassigned,'balance':sum(i['amount'] for i in invoices)-sum(p['payment'] for p in payments)}

def enrich(c,suppliers,today):
    metrics={'total_balance':0,'to_pay':0,'credit':0,'red_count':0,'yellow_count':0,'green_count':0,'review_count':0,'red_amount':0,'yellow_amount':0}
    for s in suppliers:
        v=view(c,s['id'],today);pending=[i for i in v['invoices'] if i['remaining']>0]
        dated=[i['age_days'] for i in pending if i['age_days'] is not None and i['age_days']>=0]
        s.update(unassigned=v['unassigned'],pending_count=len(pending),oldest_days=max(dated) if dated else None)
        statuses={i['status'] for i in pending}
        s['status']='red' if 'red' in statuses else 'yellow' if 'yellow' in statuses else 'review' if 'review' in statuses else 'unknown' if 'unknown' in statuses else 'green' if 'green' in statuses else 'paid' if not pending else 'future'
        if s['balance']==0:s['status']='paid'
        s['red_count']=sum(i['status']=='red' for i in pending)
        metrics['total_balance']+=s['balance'];metrics['to_pay']+=max(0,s['balance']);metrics['credit']+=max(0,-s['balance'])
        for inv in pending:
            if inv['status'] in ['red','yellow','green']:metrics[inv['status']+'_count']+=1
            if inv['status'] in ['red','yellow']:metrics[inv['status']+'_amount']+=inv['remaining']
            if inv['needs_reconciliation']:metrics['review_count']+=1
    return metrics

def op_start(c,p):
    operation=p.get('operation_key')
    if not isinstance(operation,str) or not re.fullmatch('[A-Za-z0-9-]{16,100}',operation):raise ValueError('Falta el identificador de operación. Abre de nuevo el formulario.')
    digest=hashlib.sha256(json.dumps(p,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    existing=c.execute('SELECT * FROM operation_keys WHERE key=?',(operation,)).fetchone()
    if existing:
        if existing['payload_hash']!=digest:raise ValueError('La solicitud ya fue usada con otros valores. Actualiza el formulario.')
        return operation,digest,existing['entry_id']
    return operation,digest,None

def supplier_check(c,sid):
    if type(sid) is not int or not c.execute('SELECT 1 FROM suppliers WHERE id=?',(sid,)).fetchone():raise ValueError('Selecciona un proveedor de la lista.')

def invoice(c,p,today,stamp):
    operation,digest,existing=op_start(c,p)
    if existing:return {'ok':True,'entry_id':existing,'repeated':True}
    sid=p.get('supplier_id');supplier_check(c,sid)
    dt=valid_date(p.get('date'));amount=integer(p.get('amount'),'Valor de factura')
    ref=str(p.get('invoice','')).strip();notes=str(p.get('notes','')).strip()
    if not ref or len(ref)>120 or len(notes)>3000:raise ValueError('Ingresa un número de factura válido y una observación de hasta 3.000 caracteres.')
    if amount<=0:raise ValueError('El valor de la factura debe ser mayor a cero.')
    if any(key(r['invoice'])==key(ref) for r in c.execute('SELECT invoice FROM entries WHERE supplier_id=? AND amount>0',(sid,))):raise ValueError('Este proveedor ya tiene una factura con ese número. Consulta o corrige la existente.')
    cur=c.execute("INSERT INTO entries(supplier_id,date,invoice,amount,payment,notes,kind) VALUES(?,?,?,?,0,?,'invoice')",(sid,dt,ref,amount,notes))
    c.execute('INSERT INTO operation_keys VALUES(?,?,?)',(operation,digest,cur.lastrowid))
    c.execute('INSERT INTO audit(created,action,payload) VALUES(?,?,?)',(stamp,'Recepción de factura',json.dumps(p,ensure_ascii=False)))
    return {'ok':True,'entry_id':cur.lastrowid}

def allocation_list(c,p,sid,allow_review,today):
    rows=p.get('allocations')
    if not isinstance(rows,list) or not rows or len(rows)>500:raise ValueError('Selecciona una o más facturas para aplicar el pago.')
    all_inv={i['id']:i for i in view(c,sid,today)['invoices']};seen=set();selected=[]
    for a in rows:
        if not isinstance(a,dict):raise ValueError('Distribución de pago no válida.')
        iid=a.get('invoice_id');amount=integer(a.get('amount'),'Abono')
        if type(iid) is not int or iid in seen or iid not in all_inv:raise ValueError('La factura está repetida o no pertenece al proveedor seleccionado.')
        seen.add(iid);inv=all_inv[iid]
        if not 0<amount<=inv['remaining']:raise ValueError(f'{inv["display_ref"]}: el pago debe ser mayor a cero y no superar el saldo de {money(inv["remaining"])}. Actualiza los saldos.')
        if inv['needs_reconciliation'] and not allow_review:raise ValueError('Esta factura tiene abonos históricos pendientes de asignar. Usa primero «Conciliar abonos anteriores» para no pagar dos veces.')
        selected.append((inv,amount))
    return selected

def pay(c,p,today,stamp):
    operation,digest,existing=op_start(c,p)
    if existing:return {'ok':True,'entry_id':existing,'repeated':True}
    sid=p.get('supplier_id');supplier_check(c,sid)
    dt=valid_date(p.get('date'));method=p.get('method');receipt=str(p.get('receipt','')).strip()
    if method not in ['EFECTIVO','TRANSFERENCIA','OTRO']:raise ValueError('Selecciona el medio de pago.')
    if method=='EFECTIVO' and (not receipt or len(receipt)>100):raise ValueError('Para pagos en efectivo ingresa el número de recibo de caja.')
    if method=='EFECTIVO' and c.execute("SELECT 1 FROM entries WHERE supplier_id=? AND method='EFECTIVO' AND receipt=?",(sid,receipt)).fetchone():raise ValueError('Este proveedor ya tiene un pago con ese recibo de caja. Consulta el historial para evitar duplicarlo.')
    if len(receipt)>100:raise ValueError('La referencia del recibo es demasiado larga.')
    rows=allocation_list(c,p,sid,False,today)
    if any(inv['date'] and dt<inv['date'] for inv,a in rows):raise ValueError('La fecha de pago no puede ser anterior a la recepción de las facturas seleccionadas.')
    extra=str(p.get('notes','')).strip()
    if len(extra)>1500:raise ValueError('La observación adicional es demasiado larga.')
    observations=[]
    for inv,amount in rows:
        observations.append(('Se canceló la factura ' if amount==inv['remaining'] else 'Se abonó a la factura ')+inv['display_ref']+' por '+money(amount)+'.')
    observations.append('Pago en '+method.lower()+'.')
    if receipt:observations.append(('Recibo de caja N.º ' if method=='EFECTIVO' else 'Referencia ')+receipt+'.')
    if extra:observations.append(extra)
    notes=' '.join(observations);total=sum(a for i,a in rows)
    cur=c.execute("INSERT INTO entries(supplier_id,date,invoice,amount,payment,notes,kind,method,receipt) VALUES(?,?,?,0,?,?,'payment',?,?)",(sid,dt,' / '.join(inv['display_ref'] for inv,a in rows),total,notes,method,receipt))
    pid=cur.lastrowid
    for inv,amount in rows:c.execute('INSERT INTO allocations VALUES(?,?,?)',(pid,inv['id'],amount))
    c.execute('INSERT INTO operation_keys VALUES(?,?,?)',(operation,digest,pid))
    c.execute('INSERT INTO audit(created,action,payload) VALUES(?,?,?)',(stamp,'Pago aplicado a facturas',json.dumps({'request':p,'notes':notes},ensure_ascii=False)))
    return {'ok':True,'entry_id':pid,'notes':notes,'total':total}

def reconcile(c,p,today,stamp):
    operation,digest,existing=op_start(c,p)
    if existing:return {'ok':True,'entry_id':existing,'repeated':True}
    sid=p.get('supplier_id');supplier_check(c,sid)
    pid=p.get('payment_id')
    if type(pid) is not int:raise ValueError('Selecciona el abono histórico que vas a distribuir.')
    payment=c.execute('SELECT * FROM entries WHERE id=? AND supplier_id=? AND payment>0',(pid,sid)).fetchone()
    if not payment:raise ValueError('El abono no pertenece al proveedor seleccionado.')
    available=payment['payment']-c.execute('SELECT COALESCE(SUM(amount),0) FROM allocations WHERE payment_id=?',(pid,)).fetchone()[0]
    rows=allocation_list(c,p,sid,True,today)
    if sum(a for i,a in rows)>available:raise ValueError('La distribución supera el valor sin asignar de este abono. No se guardó ningún cambio.')
    for inv,amount in rows:
        c.execute('INSERT INTO allocations(payment_id,invoice_id,amount) VALUES(?,?,?) ON CONFLICT(payment_id,invoice_id) DO UPDATE SET amount=amount+excluded.amount',(pid,inv['id'],amount))
    c.execute('INSERT INTO operation_keys VALUES(?,?,?)',(operation,digest,pid))
    c.execute('INSERT INTO audit(created,action,payload) VALUES(?,?,?)',(stamp,'Conciliación histórica sin nuevo pago',json.dumps(p,ensure_ascii=False)))
    return {'ok':True,'entry_id':pid,'notes':'Abono anterior asignado. El saldo del proveedor no cambió.'}

def edit_invoice(c,p,today,stamp):
    iid=p.get('invoice_id')
    old=c.execute('SELECT * FROM entries WHERE id=? AND amount>0',(iid,)).fetchone()
    if not old:raise ValueError('Factura no encontrada.')
    if old['version']!=p.get('version'):raise ValueError('La factura cambió en otra ventana. Actualiza antes de corregirla.')
    dt=valid_date(p.get('date'));ref=str(p.get('invoice','')).strip();amount=integer(p.get('amount'),'Valor de factura')
    if not ref or len(ref)>120 or amount<=0:raise ValueError('Ingresa el número y valor válido de la factura.')
    paid=c.execute('SELECT COALESCE(SUM(amount),0) FROM allocations WHERE invoice_id=?',(iid,)).fetchone()[0]
    if amount<paid:raise ValueError('El valor de factura no puede quedar por debajo de los abonos aplicados.')
    for inv in c.execute('SELECT id,invoice FROM entries WHERE supplier_id=? AND amount>0 AND id<>?',(old['supplier_id'],iid)):
        if key(inv['invoice'])==key(ref):raise ValueError('Otra factura del proveedor ya tiene este número.')
    dates=c.execute('SELECT e.date FROM allocations a JOIN entries e ON e.id=a.payment_id WHERE a.invoice_id=?',(iid,)).fetchall()
    if any(r['date'] and r['date']<dt for r in dates):raise ValueError('La recepción no puede quedar después de un pago ya aplicado.')
    notes=str(p.get('notes','')).strip()
    if len(notes)>3000:raise ValueError('La observación es demasiado larga.')
    c.execute('UPDATE entries SET date=?,invoice=?,amount=?,notes=?,version=version+1 WHERE id=?',(dt,ref,amount,notes,iid))
    c.execute('INSERT INTO audit(created,action,payload) VALUES(?,?,?)',(stamp,'Corregir recepción de factura',json.dumps({'before':dict(old),'after':p},ensure_ascii=False)))
    return {'ok':True}
