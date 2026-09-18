from pathlib import Path
import hashlib, json, os, subprocess
import xml.etree.ElementTree as ET
suites = [ET.parse(p).getroot() for p in Path('app/build/test-results/testDebugUnitTest').glob('TEST-*.xml')]
totals = {key: sum(int(s.get(key, '0')) for s in suites) for key in ['tests','failures','errors','skipped']}
assert totals['tests'] >= 34 and totals['failures'] == 0 and totals['errors'] == 0 and totals['skipped'] == 0, totals
files = ['app/src/main/java/ru/komus/etrnprobe/MainActivity.java', 'app/src/main/java/ru/komus/etrnprobe/SignerSupport.java', 'app/src/main/java/ru/komus/etrnprobe/SabyRpcContract.java', 'dist/ETRN_GOSKEY_BATCH_PROBE_R2_FIX2.apk']
main = Path(files[0]).read_text()
assert 'JSONObject request = SabyRpcContract.envelope(method, params, System.currentTimeMillis());' in main
assert 'rpc(TMS_SERVICE_URL, "СБИС.ВыполнитьДействие"' not in main
assert 'rpc(ONLINE_SERVICE_URL, "СБИС.ВыполнитьДействие"' not in main
proof = {'release':'R2 FIX2', 'trigger_commit':os.environ['GITHUB_SHA'], 'built_source_commit':subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(), 'tests':totals, 'live_saby_test':'NOT_RUN', 'android_device_test':'NOT_RUN', 'fixed_request_paths':['params.Params.Operation','params.Params.OperationID'], 'hashes':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files}}
Path('dist/BUILD_PROOF.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(proof, ensure_ascii=False, indent=2))
