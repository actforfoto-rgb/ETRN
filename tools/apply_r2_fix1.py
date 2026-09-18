"""One-time, hash-guarded source migration. CI commits the resulting Java source.
It never contacts Saby and never handles production credentials or documents.
"""
from pathlib import Path
import hashlib

p = Path('app/src/main/java/ru/komus/etrnprobe/MainActivity.java')
raw = p.read_bytes()
s = raw.decode('utf-8')
if '// R2_FIX1_CERTIFICATE_PREFLIGHT' in s:
    print('R2_FIX1 already applied; source is unchanged')
    raise SystemExit(0)
blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
if blob != 'c51d3970574f7e2b5dd6f6c2dc28e1681916f674':
    raise RuntimeError('Refusing to patch an unknown MainActivity revision: ' + blob)

def replace(a, b, count=1):
    global s
    if s.count(a) != count:
        raise RuntimeError('Patch anchor count mismatch: ' + repr(a[:100]))
    s = s.replace(a, b)

def method(start, end, code):
    global s
    i, j = s.index(start), s.index(end, s.index(start))
    s = s[:i] + code.rstrip() + '\n\n' + s[j:]

replace('public class MainActivity extends Activity {', '''public class MainActivity extends Activity {
    // R2_FIX1_CERTIFICATE_PREFLIGHT
    private JSONObject currentUser = new JSONObject();
    private JSONObject approvedSigner;
    private String approvedPair = "";
    private String lastPreparationKey = "";
    private volatile boolean createAttempted;
    private final List<String> protectedValues = java.util.Collections.synchronizedList(new ArrayList<>());
''')
replace('KOMUS-ETRN-GOSKEY-BATCH-PROBE-R2/2.0', 'KOMUS-ETRN-GOSKEY-BATCH-PROBE-R2-FIX1/2.1')
replace('title.setText("ETRN_GOSKEY_BATCH_PROBE_R2");', 'title.setText("ЭТрН — Госключ · R2 FIX1");')
replace('String ogrnip;\n        final List<String>', 'String ogrnip;\n        JSONObject ownFl = new JSONObject();\n        final List<String>')
replace('setDefaultDate();\n        updateButtons();', '''setDefaultDate();
        createAttempted = getPreferences(MODE_PRIVATE).getBoolean("createAttempted", false);
        operationId = getPreferences(MODE_PRIVATE).getString("operationId", "");
        log("R2_FIX1 START. Проверка реквизитов подписанта включена. Отправка в Госключ — отдельное подтверждение.");
        if (createAttempted) log("CREATE LOCK: запрос уже запускался на этом телефоне. Повторная отправка запрещена; проверь Госключ и статус.");
        updateButtons();''')
replace('v -> prepareSelected());', 'v -> reviewSigner());')
replace('Подготовить выбранные 2 ЭТрН', 'Проверить подписанта и подготовить 2 ЭТрН')
replace('e.setHint(hint);\n        e.setSingleLine(true);', 'e.setHint(hint);\n        e.setSaveEnabled(false);\n        e.setSingleLine(true);')
replace('parameter.put("Логин", login);', '''sessionId = null;
            tempSessionId = null;
            authChallengeId = null;
            currentUser = new JSONObject();
            prepared = false;
            approvedSigner = null;
            approvedPair = "";
            candidates.clear();
            docsContainer.removeAllViews();
            candidateChecks.clear();
            rememberSecret(login);
            rememberSecret(password);
            parameter.put("Логин", login);''')
replace('tempSessionId = temp;', 'tempSessionId = temp;\n        rememberSecret(temp);\n        rememberSecret(challenge);')
replace('params.put("Код", code);', 'rememberSecret(code);\n            params.put("Код", code);')
replace('sessionId = (String) result;', 'sessionId = (String) result;\n                    rememberSecret(sessionId);', count=2)
replace('JSONObject filter = new JSONObject();', '''// A read-only call: current user's name, never another document party.
                try {
                    JSONObject userResponse = rpc(ONLINE_SERVICE_URL, "СБИС.ИнформацияОТекущемПользователе",
                            new JSONObject().put("Параметр", new JSONObject()), sessionId);
                    currentUser = SignerSupport.user(userResponse);
                    rememberSecret(SignerSupport.name(currentUser));
                    logOnUi("PROFILE: ФИО=" + (!SignerSupport.name(currentUser).isEmpty() ? "PRESENT" : "MISSING"));
                    if (userResponse.has("error")) logOnUi("PROFILE RESPONSE\\n" + pretty(userResponse));
                } catch (Exception profileError) {
                    currentUser = new JSONObject();
                    logOnUi("PROFILE UNAVAILABLE: " + profileError.getClass().getSimpleName());
                }
                approvedSigner = null;
                approvedPair = "";
                JSONObject filter = new JSONObject();''')
replace('c.certificate = chooseCertificate(bestAction.optJSONArray("Сертификат"));', '''c.ownFl = SignerSupport.ownFl(doc);
        c.certificate = SignerSupport.suggest(currentUser, c.ownFl, bestAction.opt("Сертификат"), doc.opt("Сертификат"));
        rememberSigner(c.certificate);
        logOnUi("CERT DISCOVERY doc=" + shortId(docId)
                + ": action_entries=" + SignerSupport.objects(bestAction.opt("Сертификат")).size()
                + ", document_entries=" + SignerSupport.objects(doc.opt("Сертификат")).size()
                + ", OUR_FL=" + (c.ownFl.length() > 0 ? "PRESENT" : "MISSING")
                + ", FIO=" + present(c.certificate, "ФИО") + ", INN=" + present(c.certificate, "ИНН")
                + ", OGRNIP=" + present(c.certificate, "ОГРНИП"));''')
replace('prepared = false;\n        operationId = null;', 'prepared = false;', count=2)
# Keep existing proven list/auth/prepare call sequence. Insert signatory review before it.
anchor = '    private void prepareSelected() {'
review = r'''    private String pairKey(List<Candidate> selected) {
        if (selected.size() != 2) return "";
        return selected.get(0).docId + "|" + selected.get(1).docId;
    }

    private String present(JSONObject o, String key) {
        return SignerSupport.str(o, key).isEmpty() ? "MISSING" : "PRESENT";
    }

    private void rememberSecret(String value) {
        if (value != null && !value.isEmpty()) protectedValues.add(value);
    }

    private List<String> secretsSnapshot() {
        synchronized (protectedValues) { return new ArrayList<>(protectedValues); }
    }

    private void rememberSigner(JSONObject o) {
        for (String key : new String[]{"ФИО", "ИНН", "ОГРНИП"}) rememberSecret(SignerSupport.str(o, key));
    }

    private void reviewSigner() {
        if (busy || !hasSession()) return;
        if (createAttempted) { toast("Запрос в Госключ уже запускался. Скопируй лог и проверь статус."); return; }
        List<Candidate> selected = selectedCandidates();
        if (selected.size() != 2) { toast("Выбери ровно две ЭТрН"); return; }
        final JSONObject suggested;
        try { suggested = SignerSupport.merge(selected.get(0).certificate, selected.get(1).certificate); }
        catch (Exception e) { log("SIGNER STOP: " + e.getMessage()); toast(e.getMessage()); return; }

        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(20), dp(8), dp(20), dp(8));
        addInfo(form, "Проверь свои реквизиты ИП. Доступные сведения подставлены из Saby. Пустые поля заполни по своим документам ИП или сертификату в Госключе. Это подготовка титулов, не выпуск и не проверка действительности ЭП.");
        EditText fio = addField(form, "Фамилия Имя Отчество", false);
        EditText inn = addField(form, "ИНН ИП — 12 цифр", false);
        EditText ogrnip = addField(form, "ОГРНИП — 15 цифр", false);
        EditText job = addField(form, "Статус подписанта", false);
        inn.setInputType(InputType.TYPE_CLASS_NUMBER);
        ogrnip.setInputType(InputType.TYPE_CLASS_NUMBER);
        fio.setText(SignerSupport.str(suggested, "ФИО"));
        inn.setText(SignerSupport.str(suggested, "ИНН"));
        ogrnip.setText(SignerSupport.str(suggested, "ОГРНИП"));
        job.setText(firstNonEmpty(SignerSupport.str(suggested, "Должность"), "Индивидуальный предприниматель"));
        CheckBox owner = new CheckBox(this);
        owner.setText("Это мои реквизиты. Я сам водитель-ИП и буду подписывать своей КЭП ИП в Госключе.");
        form.addView(owner);
        TextView error = addInfo(form, "");
        ScrollView scroll = new ScrollView(this);
        scroll.addView(form);
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Кто подписывает две ЭТрН")
                .setView(scroll).setNegativeButton("Отмена", null)
                .setPositiveButton("Подготовить", null).create();
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            try {
                if (!owner.isChecked()) throw new IllegalArgumentException("Сначала проверь реквизиты и подтверди, что подписываешь как владелец ИП.");
                if (!pairKey(selected).equals(pairKey(selectedCandidates()))) throw new IllegalArgumentException("Выбор ЭТрН изменился. Открой проверку подписанта заново.");
                JSONObject signer = new JSONObject().put("ФИО", text(fio)).put("ИНН", text(inn))
                        .put("ОГРНИП", text(ogrnip)).put("Должность", text(job));
                SignerSupport.validate(signer, currentUser, selected.get(0).ownFl, selected.get(1).ownFl);
                rememberSigner(signer);
                approvedSigner = signer;
                approvedPair = pairKey(selected);
                for (Candidate c : selected) {
                    c.certificate = new JSONObject(signer.toString());
                    c.ogrnip = SignerSupport.str(signer, "ОГРНИП");
                }
                dialog.dismiss();
                prepareSelected();
            } catch (Exception e) { error.setText(e.getMessage()); }
        }));
        dialog.show();
    }

'''
replace(anchor, review + anchor)
replace('for (Candidate c : selected) c.preparedAttachmentIds.clear();', '''if (busy || createAttempted) return;
        if (approvedSigner == null || !approvedPair.equals(pairKey(selected))) {
            log("CERT PREFLIGHT STOP: подписант не подтверждён для этих двух ЭТрН."); return;
        }
        try { SignerSupport.requireComplete(approvedSigner); }
        catch (Exception e) { log(e.getMessage()); return; }
        String preparationKey = approvedPair + "|" + approvedSigner.toString();
        if (preparationKey.equals(lastPreparationKey)) {
            log("PREPARE REPEAT BLOCKED: эта подготовка уже запускалась. Не повторяй запрос; скопируй лог.");
            toast("Эта подготовка уже запускалась. Скопируй лог."); return;
        }
        lastPreparationKey = preparationKey;
        log("CERT PREFLIGHT OK: FIO=PRESENT, INN=PRESENT, OGRNIP=PRESENT, JOB=PRESENT; owner=CONFIRMED. Это не проверка действительности сертификата.");
        for (Candidate c : selected) c.preparedAttachmentIds.clear();''')
replace('private JSONObject prepareCandidate(Candidate c) throws Exception {\n        JSONObject action', 'private JSONObject prepareCandidate(Candidate c) throws Exception {\n        SignerSupport.requireComplete(c.certificate);\n        JSONObject action')
replace('private void createOperation(List<Candidate> selected, String ogrnip) {\n        setBusy(true);', '''private void createOperation(List<Candidate> selected, String ogrnip) {
        if (busy || createAttempted) { toast("Повторная отправка в Госключ заблокирована"); return; }
        if (!getPreferences(MODE_PRIVATE).edit().putBoolean("createAttempted", true).commit()) {
            log("CREATE STOP: не удалось сохранить защиту от повторной отправки."); return;
        }
        createAttempted = true;
        setBusy(true);''')
replace('String opId = extractOperationId(response.opt("result"));', 'String opId = response.has("error") ? "" : extractOperationId(response.opt("result"));')
replace('operationId = opId;', '''operationId = opId;
                    getPreferences(MODE_PRIVATE).edit().putString("operationId", opId).commit();''')
replace('GATE SERVER ACCEPT: получен OperationID.', 'GATE PENDING: получен OperationID. Это ещё не подтверждение пакетного подписания.')
replace('GATE ACCEPT: OperationID получен. Теперь смотри Госключ.', 'Запрос создан. Проверь обе ЭТрН в Госключе. Итог пока не доказан.')
replace('GATE REJECT: Saby отверг пакет. Скопируй лог.', 'CREATE ERROR: Saby вернул ошибку. Скопируй лог — причина требует разбора.')
replace('if (prepareButton != null) prepareButton.setEnabled(!busy && signedIn && selectedCandidates().size() == 2);', 'if (prepareButton != null) prepareButton.setEnabled(!busy && !prepared && !createAttempted && signedIn && selectedCandidates().size() == 2);')
replace('if (createButton != null) createButton.setEnabled(!busy && signedIn && prepared && selectedCandidates().size() == 2);', 'if (createButton != null) createButton.setEnabled(!busy && !createAttempted && signedIn && prepared && selectedCandidates().size() == 2);')
replace('boolean twoFactor = tempSessionId != null && !tempSessionId.isEmpty();', '''boolean twoFactor = tempSessionId != null && !tempSessionId.isEmpty();
            for (CheckBox cb : candidateChecks) cb.setEnabled(!busy && !createAttempted);
            for (EditText f : new EditText[]{loginField, passwordField, accountField, codeField, dateField})
                if (f != null) f.setEnabled(!busy);''')
method('    private String extractOperationId(Object result) {', '    private boolean hasSession()', '''    private String extractOperationId(Object result) {
        return SignerSupport.operationId(result);
    }''')
replace('private void log(String message) {\n        String old', 'private void log(String message) {\n        message = SignerSupport.redactText(message, secretsSnapshot());\n        String old')
method('    private String pretty(Object value) {', '    private String text(EditText e)', '''    private String pretty(Object value) {
        try {
            Object safe = SignerSupport.safeJson(value, secretsSnapshot());
            if (safe instanceof JSONObject) return ((JSONObject) safe).toString(2);
            if (safe instanceof JSONArray) return ((JSONArray) safe).toString(2);
            return String.valueOf(safe);
        } catch (Exception e) { return "[ответ скрыт: ошибка безопасного форматирования]"; }
    }''')
replace('ОГРНИП получен из сертификата Saby.', 'ОГРНИП указан в подтверждённых реквизитах подписанта.')
# No change to credential or document endpoints; no new finalization/signature upload call.
for forbidden in ['rpc(TMS_SERVICE_URL, "СБИС.ВыполнитьДействие"', 'rpc(ONLINE_SERVICE_URL, "СБИС.ВыполнитьДействие"']:
    assert forbidden not in s
p.write_text(s, encoding='utf-8')
print('PATCH_APPLIED=YES MAIN_SHA256=' + hashlib.sha256(p.read_bytes()).hexdigest())
