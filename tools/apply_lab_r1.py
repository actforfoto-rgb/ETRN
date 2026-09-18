from pathlib import Path
import hashlib
p=Path('app/src/main/java/ru/komus/etrnprobe/MainActivity.java')
s=p.read_text()
if '// NO_DRIVER_LAB_R1' in s:
 print('LAB_ALREADY_APPLIED');raise SystemExit(0)
b=p.read_bytes();h=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
assert h=='222487dafccc886a099f72c90dc2c3f565eaf375',h

def once(a,b):
 global s
 assert s.count(a)==1,(a[:100],s.count(a));s=s.replace(a,b)

once('public class MainActivity extends Activity {','public class MainActivity extends Activity {\n    // NO_DRIVER_LAB_R1')
once('title.setText("ЭТрН — Госключ · R2 FIX2");','title.setText("ЭТрН · СТЕНД РАЗРАБОТКИ · без сети");')
once('setDefaultDate();','setDefaultDate();\n        log("LAB ONLY. Реальная сеть заблокирована. Синтетические ответы доступны только автоматическим тестам.");')
start=s.index('    private boolean captureTwoFactor(')
end=s.index('    private void sendTwoFactorCode()',start)
s=s[:start]+'''    private boolean captureTwoFactor(JSONObject response) {
        try {
            JSONObject challenge=ProtocolChecks.twoFactor(response);
            if(challenge.length()==0) return false;
            tempSessionId=challenge.getString("session");
            authChallengeId=challenge.getString("challenge");
            rememberSecret(tempSessionId);rememberSecret(authChallengeId);
            return true;
        } catch(Exception e) { return false; }
    }

'''+s[end:]
start=s.index('                JSONObject filter = new JSONObject();',s.index('private void loadCandidates()'))
end=s.index('                if (docs == null || docs.length() == 0)',start)
s=s[:start]+'''                List<JSONObject> pages=ProtocolChecks.listByDocumentDate(date, parameters ->
                        rpc(TMS_SERVICE_URL,"СБИС.СписокДокументов",parameters,sessionId));
                JSONArray docs=new JSONArray();
                for(JSONObject pageDoc:pages) docs.put(pageDoc);
'''+s[end:]
once('boolean hasMore = "Да".equalsIgnoreCase(deepString(response, new String[]{"ЕстьЕще"}));','boolean hasMore = false; // every declared page was read, otherwise an exception is raised')
once('JSONObject doc = findDocumentObject(response.opt("result"));','JSONObject doc = ProtocolChecks.exactDocument(response.opt("result"),docId);')
start=s.index('        JSONArray stages = doc.optJSONArray("Этап");',s.index('private Candidate inspectDocument'))
end=s.index('        Candidate c = new Candidate();',start)
s=s[:start]+'''        final ProtocolChecks.Choice choice;
        try { choice=ProtocolChecks.onlySigningChoice(doc); }
        catch(IllegalArgumentException e) {
            logOnUi("ACTION STOP doc="+shortId(docId)+": "+e.getMessage());return null;
        }
        JSONObject bestStage=choice.stage, bestAction=choice.action;

'''+s[end:]
start=s.index('    private int scoreAction(')
end=s.index('    private JSONObject chooseCertificate',start)
s=s[:start]+s[end:]
once('collectSignableAttachmentIds(response.opt("result"), c.preparedAttachmentIds);','c.preparedAttachmentIds.addAll(ProtocolChecks.signableIds(response.opt("result"),c.docId,c.stageId));')
once('JSONObject request = SabyRpcContract.envelope(method, params, System.currentTimeMillis());','''JSONObject request = SabyRpcContract.envelope(method, params, System.currentTimeMillis());
        if (BuildConfig.LAB_ONLY) {
            LabHooks.Transport transport=LabHooks.transport;
            if(transport==null) throw new IllegalStateException("LAB_NETWORK_DISABLED: real Saby/Goskey calls are prohibited");
            return ProtocolChecks.validateEnvelope(request,transport.call(endpoint,request,session));
        }''')
once('(view, year, month, day) -> dateField.setText(String.format(Locale.ROOT, "%02d.%02d.%04d", day, month + 1, year)),','''(view, year, month, day) -> {
                    dateField.setText(String.format(Locale.ROOT, "%02d.%02d.%04d", day, month + 1, year));
                    prepared=false;approvedSigner=null;approvedPair="";
                    candidates.clear();candidateChecks.clear();docsContainer.removeAllViews();
                    preparedState.setText("Дата изменена — загрузи документы заново");updateButtons();
                },''')
p.write_text(s)
print('LAB_PATCH_APPLIED',hashlib.sha256(p.read_bytes()).hexdigest())
