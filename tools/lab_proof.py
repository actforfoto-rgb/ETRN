from pathlib import Path
import xml.etree.ElementTree as ET
import json,hashlib,subprocess,os

def collect(pattern):
 cases=[]
 for p in Path('.').glob(pattern):
  try:
   r=ET.parse(p).getroot()
   for t in r.iter('testcase'):
    cases.append({'class':t.get('classname'),'name':t.get('name'),'passed':t.find('failure') is None and t.find('error') is None and t.find('skipped') is None})
  except ET.ParseError: pass
 return cases
unit=collect('app/build/test-results/testDebugUnitTest/TEST-*.xml')
device=collect('app/build/outputs/androidTest-results/connected/**/*.xml')
p=Path('app/build/outputs/apk/debug/app-debug.apk')
proof={'branch':'dev/no-driver-lab-r1','trigger_commit':os.getenv('GITHUB_SHA'),'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'unit':unit,'android':device,'unit_passed':sum(x['passed'] for x in unit),'unit_total':len(unit),'android_passed':sum(x['passed'] for x in device),'android_total':len(device),'apk_sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None,'evidence_kind':'SYNTHETIC_GATEWAY_AND_ANDROID_EMULATOR','live_saby_test':'NOT_RUN','real_goskey_signatures':'NOT_RUN','production_release':'BLOCKED','main_branch_and_drive_apk':'NOT_MODIFIED','limitations':['Saby client SignedFile carries per-file docId, but live cross-document Saby server acceptance is not confirmed','direct Госключ 60025907 route is contract/synthetic tested only; authorized EPGU contour and real Goskey signatures are not available in CI','production signature verifier and durable Android Store are not yet implemented','direct phone batch route is not yet wired into the production MainActivity/Saby HTTP adapter']}
Path('evidence/LAB_PROOF.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in proof.items() if k not in ['unit','android']},ensure_ascii=False,indent=2))
