"""Build public, data-free executable. No private key or customer workbook is copied."""
import json,shutil,subprocess,sys
from pathlib import Path
from updater import VERSION

ROOT=Path(__file__).parent.resolve()
stage=ROOT/'public-build-assets'
stage.mkdir(exist_ok=True)
for f in ['beta.html','beta.js','update_channel.json']:shutil.copy2(ROOT/'assets'/f,stage/f)
counts={str(d):0 for d in [100000,50000,20000,10000,5000,2000,1000,500,200,100]}
clean={'suppliers':[],'entries':[],'cash':[{'name':'Conteo inicial','counts':counts}],'sources':[],'summary_balance':0,'issues':[],'snapshot':{}}
(stage/'seed.json').write_text(json.dumps(clean),encoding='utf-8')
(stage/'monthly_seed.json').write_text('[]',encoding='utf-8')
out=ROOT/'release-public'
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed','--name','MerkagroLocal','--add-data',str(stage)+';assets','--distpath',str(out),'--workpath',str(ROOT/'build-public'),'--specpath',str(ROOT),str(ROOT/'app.py')],check=True)
print('Build público sin datos personales: '+str(out/'MerkagroLocal.exe'))
