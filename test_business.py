"""Public tests use synthetic records, never customer data."""
import sqlite3,unittest,uuid,tempfile,zipfile
from pathlib import Path
import ledger
import monthly

class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
        self.c.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE audit(id INTEGER PRIMARY KEY,created TEXT,action TEXT,payload TEXT);CREATE TABLE suppliers(id INTEGER PRIMARY KEY,name TEXT);CREATE TABLE entries(id INTEGER PRIMARY KEY,supplier_id INTEGER,date TEXT,original_date TEXT DEFAULT '',invoice TEXT,amount INTEGER,payment INTEGER,notes TEXT DEFAULT '',source_row INTEGER,source_sheet TEXT,version INTEGER DEFAULT 1);INSERT INTO suppliers VALUES(1,'Synthetic supplier');''')
        ledger.migrate(self.c,'2026-01-01')
    def tearDown(self):self.c.close()
    def inv(self,ref,amount,dt):return ledger.invoice(self.c,dict(supplier_id=1,invoice=ref,amount=amount,date=dt,operation_key=uuid.uuid4().hex),'2026-11-02','stamp')['entry_id']
    def test_exact_age_bands(self):
        for ref,dt,status in [('G','2026-10-08','green'),('Y','2026-10-07','yellow'),('L','2026-10-03','yellow'),('R','2026-10-02','red')]:
            self.inv(ref,1000,dt)
        self.assertEqual([i['status'] for i in ledger.view(self.c,1,'2026-11-02')['invoices']],['red','yellow','yellow','green'])
    def test_split_payment_idempotency_and_overpayment(self):
        a=self.inv('A',1000,'2026-10-01');b=self.inv('B',2000,'2026-10-01')
        p=dict(supplier_id=1,date='2026-11-02',method='EFECTIVO',receipt='SYNTH-1',allocations=[{'invoice_id':a,'amount':1000},{'invoice_id':b,'amount':500}],operation_key=uuid.uuid4().hex)
        r=ledger.pay(self.c,p,'2026-11-02','stamp');self.assertIn('Se canceló la factura A',r['notes']);self.assertIn('Se abonó a la factura B',r['notes'])
        self.assertEqual(ledger.pay(self.c,p,'2026-11-02','stamp')['entry_id'],r['entry_id'])
        v=ledger.view(self.c,1,'2026-11-02');self.assertEqual(v['balance'],1500);self.assertEqual(v['invoices'][0]['status'],'paid')
        p.update(operation_key=uuid.uuid4().hex,receipt='SYNTH-2',allocations=[{'invoice_id':b,'amount':1501}])
        with self.assertRaises(ValueError):ledger.pay(self.c,p,'2026-11-02','stamp')
    def test_monthly_xml_preserves_money_and_missing_dates(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'synthetic.xlsx'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="COMPRAS CONTADO" r:id="r1"/><sheet name="GASTOS" r:id="r2"/></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/s1.xml"/><Relationship Id="r2" Target="worksheets/s2.xml"/></Relationships>')
                xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="3"><c r="A3"><v>46000</v></c><c r="B3" t="inlineStr"><is><t>Synthetic item</t></is></c><c r="C3"><v>313000</v></c><c r="D3"><f>SUM(C3:C4)</f><v>313012</v></c></row><row r="4"><c r="B4" t="inlineStr"><is><t>Other item</t></is></c><c r="C4"><v>12</v></c></row></sheetData></worksheet>'
                z.writestr('xl/worksheets/s1.xml',xml);z.writestr('xl/worksheets/s2.xml',xml)
            rows=monthly.read_original(p)
            self.assertEqual(len(rows),4);self.assertEqual(rows[0]['amount'],313000);self.assertEqual(rows[0]['notes'],'');self.assertIsNone(rows[1]['date']);self.assertEqual(rows[1]['month'],rows[0]['month'])

if __name__=='__main__':unittest.main()
