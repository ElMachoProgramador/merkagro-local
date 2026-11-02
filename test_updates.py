import base64,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import updater

class UpdatesTest(unittest.TestCase):
    def setUp(self):
        self.key=Ed25519PrivateKey.generate()
        self.pub=base64.b64encode(self.key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
        self.repo='example/merkagro'
        self.binary=b'MZ'+b'synthetic-test-binary'*50
        self.p={'app_id':updater.APP_ID,'platform':'windows-x64','version':'1.2.0','url':'https://github.com/example/merkagro/releases/download/v1.2.0/MerkagroLocal.exe','sha256':hashlib.sha256(self.binary).hexdigest(),'size':len(self.binary)}
    def env(self,p=None):
        p=p or self.p
        return {'payload':p,'signature':base64.b64encode(self.key.sign(updater.canonical(p))).decode()}
    def test_signature_origin_and_version(self):
        self.assertEqual(updater.verify(self.env(),'1.1.0',self.repo,self.pub),self.p)
        self.assertIsNone(updater.verify(self.env(),'1.2.0',self.repo,self.pub))
        self.assertIsNone(updater.verify(self.env(),'1.3.0',self.repo,self.pub))
        for field,val in [('url','https://evil.example/app.exe'),('app_id','other'),('size',0),('platform','linux'),('version','../../evil')]:
            p=dict(self.p);p[field]=val
            with self.assertRaises(ValueError):updater.verify(self.env(p),'1.1.0',self.repo,self.pub)
        e=self.env();e['payload']=dict(self.p,size=42)
        with self.assertRaises(ValueError):updater.verify(e,'1.1.0',self.repo,self.pub)
    def test_corrupt_download_and_replacement_preserves_data(self):
        with tempfile.TemporaryDirectory() as td:
            t=Path(td);dest=t/'MERKAGRO Local.exe';dest.write_bytes(b'old app');new=t/'new.exe';new.write_bytes(self.binary)
            database=t/'merkagro.sqlite3';database.write_bytes(b'untouched database')
            oldhash=hashlib.sha256(dest.read_bytes()).hexdigest()
            previous=updater.replace_binary(dest,new,self.p,oldhash)
            self.assertEqual(dest.read_bytes(),self.binary);self.assertEqual(previous.read_bytes(),b'old app')
            self.assertEqual(database.read_bytes(),b'untouched database')
            new.write_bytes(self.binary[:-1])
            with self.assertRaises(ValueError):updater.verify_file(new,self.p)
    def test_rollback_when_final_rename_fails(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);target=d/'MerkagroLocal.exe';target.write_bytes(b'old');new=d/'new.exe';new.write_bytes(self.binary)
            real=updater.os.replace
            def fail(a,b):
                if str(a).endswith('.nuevo'):raise OSError('Simulated disk error')
                return real(a,b)
            with patch.object(updater.os,'replace',side_effect=fail):
                with self.assertRaises(OSError):updater.replace_binary(target,new,self.p,hashlib.sha256(b'old').hexdigest())
            self.assertEqual(target.read_bytes(),b'old')
    def test_https_only(self):
        for url in ['http://github.com/a','https://github.com.evil.example/a','https://user:pass@github.com/a','https://example.com/a']:
            with self.assertRaises(ValueError):updater.safe_url(url)
        updater.safe_url('https://github.com/example/merkagro/releases/download/v1.2.0/MerkagroLocal.exe')

if __name__=='__main__':unittest.main(verbosity=2)
