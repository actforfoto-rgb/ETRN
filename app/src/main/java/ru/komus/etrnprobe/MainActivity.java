package ru.komus.etrnprobe;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DatePickerDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Date;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    // R2_FIX1_CERTIFICATE_PREFLIGHT
    private JSONObject currentUser = new JSONObject();
    private JSONObject approvedSigner;
    private String approvedPair = "";
    private String lastPreparationKey = "";
    private volatile boolean createAttempted;
    private final List<String> protectedValues = java.util.Collections.synchronizedList(new ArrayList<>());

    private static final String AUTH_URL = "https://online.sbis.ru/auth/service/";
    private static final String ONLINE_SERVICE_URL = "https://online.sbis.ru/service/?srv=1";
    private static final String TMS_SERVICE_URL = "https://tms.saby.ru/service/";
    private static final String USER_AGENT = "KOMUS-ETRN-GOSKEY-BATCH-PROBE-R2-FIX1/2.1";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final List<Candidate> candidates = new ArrayList<>();
    private final List<CheckBox> candidateChecks = new ArrayList<>();

    private EditText loginField;
    private EditText passwordField;
    private EditText accountField;
    private EditText codeField;
    private EditText dateField;
    private TextView authState;
    private TextView docsState;
    private TextView preparedState;
    private TextView logView;
    private LinearLayout docsContainer;

    private Button authButton;
    private Button sendCodeButton;
    private Button confirmCodeButton;
    private Button loadButton;
    private Button prepareButton;
    private Button createButton;
    private Button statusButton;

    private volatile String sessionId;
    private volatile String tempSessionId;
    private volatile String authChallengeId;
    private volatile String operationId;
    private volatile boolean busy;
    private volatile boolean prepared;

    private static class Candidate {
        String docId;
        String number;
        String date;
        String state;
        String stageId;
        String stageName;
        String actionName;
        JSONObject certificate;
        String ogrnip;
        JSONObject ownFl = new JSONObject();
        final List<String> preparedAttachmentIds = new ArrayList<>();

        String display() {
            String n = number == null || number.isEmpty() ? "(без номера)" : number;
            String s = state == null || state.isEmpty() ? "состояние не указано" : state;
            String a = ((stageName == null ? "" : stageName) + " → " + (actionName == null ? "" : actionName)).trim();
            return "№ " + n + " | " + s + "\n" + a;
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildUi());
        setDefaultDate();
        createAttempted = getPreferences(MODE_PRIVATE).getBoolean("createAttempted", false);
        operationId = getPreferences(MODE_PRIVATE).getString("operationId", "");
        log("R2_FIX1 START. Проверка реквизитов подписанта включена. Отправка в Госключ — отдельное подтверждение.");
        if (createAttempted) log("CREATE LOCK: запрос уже запускался на этом телефоне. Повторная отправка запрещена; проверь Госключ и статус.");
        updateButtons();
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        sessionId = null;
        tempSessionId = null;
        authChallengeId = null;
        operationId = null;
        if (passwordField != null) passwordField.setText("");
        if (codeField != null) codeField.setText("");
        super.onDestroy();
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int p = dp(16);
        root.setPadding(p, p, p, p);
        scroll.addView(root);

        TextView title = new TextView(this);
        title.setText("ЭТрН — Госключ · R2 FIX1");
        title.setTextSize(22);
        root.addView(title);

        TextView subtitle = new TextView(this);
        subtitle.setText("Пилот: Saby сам находит ЭТрН за дату, определяет этапы с подписью, готовит вложения двух выбранных ЭТрН и отправляет их ОДНОЙ операцией в Госключ. СБИС.ВыполнитьДействие не вызывается.");
        subtitle.setPadding(0, dp(8), 0, dp(12));
        root.addView(subtitle);

        addHeader(root, "1. Вход в Saby");
        loginField = addField(root, "Логин Saby", false);
        passwordField = addField(root, "Пароль Saby", true);
        accountField = addField(root, "Номер аккаунта — только если у логина несколько кабинетов", false);
        authButton = addButton(root, "Войти", v -> authenticate());

        authState = addInfo(root, "Сессии нет");

        codeField = addField(root, "Код двухфакторной аутентификации", false);
        codeField.setInputType(InputType.TYPE_CLASS_NUMBER);
        sendCodeButton = addButton(root, "Отправить код 2FA", v -> sendTwoFactorCode());
        confirmCodeButton = addButton(root, "Подтвердить код 2FA", v -> confirmTwoFactorCode());

        addHeader(root, "2. Найти ЭТрН, ожидающие подписи");
        dateField = addField(root, "Дата ДД.ММ.ГГГГ", false);
        dateField.setFocusable(false);
        dateField.setOnClickListener(v -> chooseDate());
        loadButton = addButton(root, "Загрузить ЭТрН за дату", v -> loadCandidates());

        docsState = addInfo(root, "ЭТрН ещё не загружены");
        docsContainer = new LinearLayout(this);
        docsContainer.setOrientation(LinearLayout.VERTICAL);
        root.addView(docsContainer, matchWrap());

        addHeader(root, "3. Подготовить две ЭТрН");
        TextView prepareHelp = addInfo(root, "Выбери ровно две ЭТрН. R2 сама вызовет СБИС.ПодготовитьДействие и возьмёт AttachmentID файлов, которые Saby пометил «Подписать».");
        prepareHelp.setPadding(0, 0, 0, dp(6));
        prepareButton = addButton(root, "Проверить подписанта и подготовить 2 ЭТрН", v -> reviewSigner());
        preparedState = addInfo(root, "Пакет не подготовлен");

        addHeader(root, "4. Проверить пакетный Госключ");
        TextView warning = addInfo(root, "Этот шаг создаёт реальный запрос на подпись. По документации Saby его нельзя отозвать через API. До нажатия кнопки ЭТрН НЕ переходят на следующий этап.");
        createButton = addButton(root, "ОТПРАВИТЬ ОДИН ПАКЕТ В ГОСКЛЮЧ", v -> confirmCreateOperation());
        statusButton = addButton(root, "Проверить статус операции", v -> getStatus());

        addHeader(root, "Диагностика");
        Button copyButton = addButton(root, "Скопировать лог", v -> copyLog());
        copyButton.setEnabled(true);
        Button clearButton = addButton(root, "Очистить лог", v -> logView.setText(""));
        clearButton.setEnabled(true);

        logView = new TextView(this);
        logView.setTextIsSelectable(true);
        logView.setTextSize(12);
        root.addView(logView, matchWrap());

        return scroll;
    }

    private void addHeader(LinearLayout root, String text) {
        TextView h = new TextView(this);
        h.setText(text);
        h.setTextSize(18);
        h.setPadding(0, dp(18), 0, dp(6));
        root.addView(h);
    }

    private TextView addInfo(LinearLayout root, String text) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(14);
        root.addView(t, matchWrap());
        return t;
    }

    private EditText addField(LinearLayout root, String hint, boolean password) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setSaveEnabled(false);
        e.setSingleLine(true);
        if (password) {
            e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        }
        root.addView(e, matchWrap());
        return e;
    }

    private Button addButton(LinearLayout root, String text, View.OnClickListener listener) {
        Button b = new Button(this);
        b.setText(text);
        b.setOnClickListener(listener);
        root.addView(b, matchWrap());
        return b;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private void setDefaultDate() {
        dateField.setText(new SimpleDateFormat("dd.MM.yyyy", Locale.ROOT).format(new Date()));
    }

    private void chooseDate() {
        Calendar c = Calendar.getInstance();
        String value = text(dateField);
        try {
            Date d = new SimpleDateFormat("dd.MM.yyyy", Locale.ROOT).parse(value);
            if (d != null) c.setTime(d);
        } catch (Exception ignored) {
        }
        DatePickerDialog dialog = new DatePickerDialog(
                this,
                (view, year, month, day) -> dateField.setText(String.format(Locale.ROOT, "%02d.%02d.%04d", day, month + 1, year)),
                c.get(Calendar.YEAR),
                c.get(Calendar.MONTH),
                c.get(Calendar.DAY_OF_MONTH)
        );
        dialog.show();
    }

    private void authenticate() {
        String login = text(loginField);
        String password = text(passwordField);
        if (login.isEmpty() || password.isEmpty()) {
            toast("Нужны логин и пароль Saby");
            return;
        }

        JSONObject parameter = new JSONObject();
        JSONObject params = new JSONObject();
        try {
            sessionId = null;
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
            parameter.put("Логин", login);
            parameter.put("Пароль", password);
            String account = text(accountField);
            if (!account.isEmpty()) parameter.put("НомерАккаунта", account);
            params.put("Параметр", parameter);
        } catch (Exception e) {
            log("AUTH JSON ERROR: " + e);
            return;
        }

        setBusy(true);
        executor.execute(() -> {
            try {
                JSONObject response = rpc(AUTH_URL, "СБИС.Аутентифицировать", params, null);
                Object result = response.opt("result");
                if (result instanceof String && !((String) result).isEmpty()) {
                    sessionId = (String) result;
                    rememberSecret(sessionId);
                    tempSessionId = null;
                    authChallengeId = null;
                    logOnUi("AUTH OK. Сессия хранится только в памяти приложения.");
                    runOnUiThread(() -> authState.setText("Вход выполнен"));
                } else if (captureTwoFactor(response)) {
                    logOnUi("AUTH 2FA REQUIRED. R2 получила временную сессию/идентификатор подтверждения. Нажми «Отправить код 2FA».");
                    runOnUiThread(() -> authState.setText("Нужно подтверждение 2FA"));
                } else {
                    logOnUi("AUTH ERROR\n" + pretty(response));
                    runOnUiThread(() -> authState.setText("Вход не выполнен"));
                }
            } catch (Exception e) {
                logOnUi("AUTH EXCEPTION: " + e);
            } finally {
                runOnUiThread(() -> {
                    passwordField.setText("");
                    setBusy(false);
                    updateButtons();
                });
            }
        });
    }

    private boolean captureTwoFactor(JSONObject response) {
        String temp = deepString(response, new String[]{"ИдентификаторСессии", "SessionID", "ИдентификаторСессииАутентификации"});
        String challenge = deepString(response, new String[]{"Идентификатор", "AuthID", "ИдентификаторПодтверждения"});
        if (temp == null || temp.isEmpty()) return false;
        tempSessionId = temp;
        rememberSecret(temp);
        rememberSecret(challenge);
        authChallengeId = challenge;
        return true;
    }

    private void sendTwoFactorCode() {
        if (tempSessionId == null || tempSessionId.isEmpty() || authChallengeId == null || authChallengeId.isEmpty()) {
            toast("Нет данных для 2FA. Сначала выполни вход.");
            return;
        }
        JSONObject params = new JSONObject();
        try {
            params.put("Идентификатор", authChallengeId);
        } catch (Exception e) {
            log("2FA JSON ERROR: " + e);
            return;
        }

        setBusy(true);
        executor.execute(() -> {
            try {
                JSONObject response = rpc(AUTH_URL, "СБИС.ОтправитьКодАутентификации", params, tempSessionId);
                Object result = response.opt("result");
                if (result instanceof String && !((String) result).isEmpty()) {
                    authChallengeId = (String) result;
                    logOnUi("2FA CODE SENT. Код отправлен на подтверждённый телефон.");
                    runOnUiThread(() -> authState.setText("Код 2FA отправлен"));
                } else {
                    logOnUi("2FA SEND RESPONSE\n" + pretty(response));
                }
            } catch (Exception e) {
                logOnUi("2FA SEND EXCEPTION: " + e);
            } finally {
                setBusy(false);
                updateButtons();
            }
        });
    }

    private void confirmTwoFactorCode() {
        String code = text(codeField);
        if (tempSessionId == null || tempSessionId.isEmpty() || authChallengeId == null || authChallengeId.isEmpty() || code.isEmpty()) {
            toast("Нужны временная сессия, идентификатор 2FA и код");
            return;
        }
        JSONObject params = new JSONObject();
        try {
            params.put("Идентификатор", authChallengeId);
            rememberSecret(code);
            params.put("Код", code);
        } catch (Exception e) {
            log("2FA CONFIRM JSON ERROR: " + e);
            return;
        }

        setBusy(true);
        executor.execute(() -> {
            try {
                JSONObject response = rpc(AUTH_URL, "СБИС.ПодтвердитьВход", params, tempSessionId);
                Object result = response.opt("result");
                if (result instanceof String && !((String) result).isEmpty()) {
                    sessionId = (String) result;
                    rememberSecret(sessionId);
                    tempSessionId = null;
                    authChallengeId = null;
                    logOnUi("2FA OK. Полная Saby-сессия получена.");
                    runOnUiThread(() -> {
                        codeField.setText("");
                        authState.setText("Вход выполнен");
                    });
                } else {
                    logOnUi("2FA CONFIRM RESPONSE\n" + pretty(response));
                }
            } catch (Exception e) {
                logOnUi("2FA CONFIRM EXCEPTION: " + e);
            } finally {
                setBusy(false);
                updateButtons();
            }
        });
    }

    private void loadCandidates() {
        if (!hasSession()) return;
        String date = text(dateField);
        if (!date.matches("\\d{2}\\.\\d{2}\\.\\d{4}")) {
            toast("Дата должна быть ДД.ММ.ГГГГ");
            return;
        }

        prepared = false;
        candidates.clear();
        runOnUiThread(() -> {
            docsContainer.removeAllViews();
            docsState.setText("Загрузка...");
            preparedState.setText("Пакет не подготовлен");
        });
        setBusy(true);

        executor.execute(() -> {
            try {
                // A read-only call: current user's name, never another document party.
                try {
                    JSONObject userResponse = rpc(ONLINE_SERVICE_URL, "СБИС.ИнформацияОТекущемПользователе",
                            new JSONObject().put("Параметр", new JSONObject()), sessionId);
                    currentUser = SignerSupport.user(userResponse);
                    rememberSecret(SignerSupport.name(currentUser));
                    logOnUi("PROFILE: ФИО=" + (!SignerSupport.name(currentUser).isEmpty() ? "PRESENT" : "MISSING"));
                    if (userResponse.has("error")) logOnUi("PROFILE RESPONSE\n" + pretty(userResponse));
                } catch (Exception profileError) {
                    currentUser = new JSONObject();
                    logOnUi("PROFILE UNAVAILABLE: " + profileError.getClass().getSimpleName());
                }
                approvedSigner = null;
                approvedPair = "";
                JSONObject filter = new JSONObject();
                filter.put("ДатаВремяС", date + " 00.00.00");
                filter.put("ДатаВремяПо", date + " 23.59.59");
                filter.put("Тип", "ConsignmentNote");
                filter.put("ПолныйСертификатЭП", "Нет");
                filter.put("Навигация", new JSONObject().put("РазмерСтраницы", "50"));

                JSONObject params = new JSONObject().put("Фильтр", filter);
                JSONObject response = rpc(TMS_SERVICE_URL, "СБИС.СписокИзменений", params, sessionId);
                if (response.has("error")) {
                    logOnUi("LIST ERROR\n" + pretty(response));
                    runOnUiThread(() -> docsState.setText("Ошибка загрузки списка"));
                    return;
                }

                JSONArray docs = findDocumentsArray(response.opt("result"));
                if (docs == null || docs.length() == 0) {
                    logOnUi("LIST OK: документов за дату не найдено.");
                    runOnUiThread(() -> docsState.setText("Документы за дату не найдены"));
                    return;
                }

                Set<String> seen = new HashSet<>();
                int scanned = 0;
                int eligible = 0;
                boolean hasMore = "Да".equalsIgnoreCase(deepString(response, new String[]{"ЕстьЕще"}));

                for (int i = 0; i < docs.length(); i++) {
                    JSONObject summary = docs.optJSONObject(i);
                    if (summary == null) continue;
                    String docId = summary.optString("Идентификатор", "");
                    if (docId.isEmpty() || seen.contains(docId)) continue;
                    seen.add(docId);
                    String docDate = summary.optString("Дата", "");
                    if (!docDate.isEmpty() && !date.equals(docDate)) continue;

                    scanned++;
                    int current = scanned;
                    runOnUiThread(() -> docsState.setText("Проверяю документы: " + current));

                    Candidate c = inspectDocument(summary);
                    if (c != null) {
                        candidates.add(c);
                        eligible++;
                    }
                }

                int finalScanned = scanned;
                int finalEligible = eligible;
                runOnUiThread(() -> {
                    renderCandidates();
                    String tail = hasMore ? " (у Saby есть ещё страница; для пилота просмотрены первые 50 событий)" : "";
                    docsState.setText("Проверено: " + finalScanned + ". Ожидают подписания: " + finalEligible + tail);
                });
                logOnUi("LIST/READ OK. Проверено=" + scanned + ", кандидатов на подпись=" + eligible + (hasMore ? ", ЕстьЕще=Да" : ""));
            } catch (Exception e) {
                logOnUi("LOAD EXCEPTION: " + e);
                runOnUiThread(() -> docsState.setText("Ошибка: " + e.getClass().getSimpleName()));
            } finally {
                setBusy(false);
                updateButtons();
            }
        });
    }

    private Candidate inspectDocument(JSONObject summary) throws Exception {
        String docId = summary.optString("Идентификатор", "");
        JSONObject params = new JSONObject().put("Документ", new JSONObject().put("Идентификатор", docId));
        JSONObject response = rpc(TMS_SERVICE_URL, "СБИС.ПрочитатьДокумент", params, sessionId);
        if (response.has("error")) {
            logOnUi("READ SKIP " + shortId(docId) + "\n" + pretty(response));
            return null;
        }

        JSONObject doc = findDocumentObject(response.opt("result"));
        if (doc == null) return null;

        JSONArray stages = doc.optJSONArray("Этап");
        if (stages == null) stages = findArrayByKey(doc, "Этап");
        if (stages == null) return null;

        JSONObject bestStage = null;
        JSONObject bestAction = null;
        int bestScore = Integer.MIN_VALUE;

        for (int i = 0; i < stages.length(); i++) {
            JSONObject stage = stages.optJSONObject(i);
            if (stage == null) continue;
            JSONArray actions = stage.optJSONArray("Действие");
            if (actions == null) continue;

            for (int j = 0; j < actions.length(); j++) {
                JSONObject action = actions.optJSONObject(j);
                if (action == null) continue;
                if (!isYes(action.optString("ТребуетПодписания", action.optString("ТребуетПодписание", "")))) continue;
                String actionName = action.optString("Название", "");
                int score = scoreAction(actionName);
                if (score > bestScore) {
                    bestScore = score;
                    bestStage = stage;
                    bestAction = action;
                }
            }
        }

        if (bestStage == null || bestAction == null) return null;

        Candidate c = new Candidate();
        c.docId = docId;
        c.number = firstNonEmpty(doc.optString("Номер", ""), summary.optString("Номер", ""));
        c.date = firstNonEmpty(doc.optString("Дата", ""), summary.optString("Дата", ""));
        JSONObject state = doc.optJSONObject("Состояние");
        if (state == null) state = summary.optJSONObject("Состояние");
        c.state = state == null ? "" : state.optString("Название", "");
        c.stageId = bestStage.optString("Идентификатор", "");
        c.stageName = bestStage.optString("Название", "");
        c.actionName = bestAction.optString("Название", "");
        c.ownFl = SignerSupport.ownFl(doc);
        c.certificate = SignerSupport.suggest(currentUser, c.ownFl, bestAction.opt("Сертификат"), doc.opt("Сертификат"));
        rememberSigner(c.certificate);
        logOnUi("CERT DISCOVERY doc=" + shortId(docId)
                + ": action_entries=" + SignerSupport.objects(bestAction.opt("Сертификат")).size()
                + ", document_entries=" + SignerSupport.objects(doc.opt("Сертификат")).size()
                + ", OUR_FL=" + (c.ownFl.length() > 0 ? "PRESENT" : "MISSING")
                + ", FIO=" + present(c.certificate, "ФИО") + ", INN=" + present(c.certificate, "ИНН")
                + ", OGRNIP=" + present(c.certificate, "ОГРНИП"));
        c.ogrnip = extractOgrnip(c.certificate);

        return c;
    }

    private int scoreAction(String name) {
        if (name == null) return 0;
        String n = name.trim();
        if ("Погружен".equalsIgnoreCase(n) || "Принят".equalsIgnoreCase(n) ||
                "Выдан".equalsIgnoreCase(n) || "Согласовано".equalsIgnoreCase(n)) return 100;
        if (n.toLowerCase(Locale.ROOT).contains("не принят") ||
                n.toLowerCase(Locale.ROOT).contains("не выдан") ||
                n.toLowerCase(Locale.ROOT).contains("переназнач")) return 10;
        return 50;
    }

    private JSONObject chooseCertificate(JSONArray certificates) {
        if (certificates == null || certificates.length() == 0) return null;
        JSONObject fallback = null;
        for (int i = 0; i < certificates.length(); i++) {
            JSONObject c = certificates.optJSONObject(i);
            if (c == null) continue;
            if (fallback == null) fallback = c;
            JSONObject key = c.optJSONObject("Ключ");
            String keyType = key == null ? "" : key.optString("Тип", "");
            String activation = key == null ? "" : key.optString("СпособАктивации", "");
            String joined = (keyType + " " + activation).toLowerCase(Locale.ROOT);
            if (joined.contains("гос") || joined.contains("gos")) return c;
            if (!c.optString("ОГРНИП", "").isEmpty()) fallback = c;
        }
        return fallback;
    }

    private String extractOgrnip(JSONObject certificate) {
        if (certificate == null) return "";
        return certificate.optString("ОГРНИП", certificate.optString("Огрнип", ""));
    }

    private void renderCandidates() {
        docsContainer.removeAllViews();
        candidateChecks.clear();
        prepared = false;

        if (candidates.isEmpty()) {
            TextView empty = new TextView(this);
            empty.setText("Saby не вернул текущих действий, требующих подписания, по документам этой даты.");
            docsContainer.addView(empty, matchWrap());
            updateButtons();
            return;
        }

        for (Candidate c : candidates) {
            CheckBox cb = new CheckBox(this);
            cb.setText(c.display());
            cb.setPadding(0, dp(4), 0, dp(4));
            cb.setOnCheckedChangeListener((buttonView, isChecked) -> {
                if (isChecked && selectedCandidates().size() > 2) {
                    buttonView.setChecked(false);
                    toast("Для Gate R2 нужно выбрать ровно две ЭТрН");
                }
                prepared = false;
                preparedState.setText("Пакет не подготовлен");
                updateButtons();
            });
            candidateChecks.add(cb);
            docsContainer.addView(cb, matchWrap());
        }
        updateButtons();
    }

    private List<Candidate> selectedCandidates() {
        List<Candidate> selected = new ArrayList<>();
        for (int i = 0; i < candidateChecks.size() && i < candidates.size(); i++) {
            if (candidateChecks.get(i).isChecked()) selected.add(candidates.get(i));
        }
        return selected;
    }

    private String pairKey(List<Candidate> selected) {
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

    private void prepareSelected() {
        if (!hasSession()) return;
        List<Candidate> selected = selectedCandidates();
        if (selected.size() != 2) {
            toast("Выбери ровно две ЭТрН");
            return;
        }

        prepared = false;
        if (busy || createAttempted) return;
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
        for (Candidate c : selected) c.preparedAttachmentIds.clear();
        runOnUiThread(() -> preparedState.setText("Подготовка..."));
        setBusy(true);

        executor.execute(() -> {
            try {
                for (Candidate c : selected) {
                    runOnUiThread(() -> preparedState.setText("Подготавливаю ЭТрН № " + (c.number == null ? "" : c.number)));
                    JSONObject response = prepareCandidate(c);
                    if (response.has("error")) {
                        logOnUi("PREPARE ERROR doc=" + shortId(c.docId) + "\n" + pretty(response));
                        runOnUiThread(() -> preparedState.setText("Подготовка остановлена: Saby вернул ошибку"));
                        return;
                    }

                    collectSignableAttachmentIds(response.opt("result"), c.preparedAttachmentIds);
                    dedupe(c.preparedAttachmentIds);
                    if (c.preparedAttachmentIds.isEmpty()) {
                        logOnUi("PREPARE STOP doc=" + shortId(c.docId) + ": нет вложений с ТребуемоеДействие=Подписать\n" + pretty(response));
                        runOnUiThread(() -> preparedState.setText("Подготовка остановлена: нет подписываемого вложения"));
                        return;
                    }
                    logOnUi("PREPARE OK doc=" + shortId(c.docId) + ", signable attachments=" + c.preparedAttachmentIds.size());
                }

                prepared = true;
                int files = selected.get(0).preparedAttachmentIds.size() + selected.get(1).preparedAttachmentIds.size();
                String ogrnip = chooseOgrnip(selected);
                String extra = ogrnip.isEmpty() ? "\nВНИМАНИЕ: ОГРНИП не найден в данных сертификата. Create будет заблокирован, чтобы не отправлять неверный тип подписи." : "\nОГРНИП указан в подтверждённых реквизитах подписанта.";
                runOnUiThread(() -> preparedState.setText("Готово: 2 ЭТрН, подписываемых файлов: " + files + extra));
                logOnUi("PACKAGE READY. 2 docs, files=" + files + ", OGRNIP=" + (ogrnip.isEmpty() ? "MISSING" : "AUTO"));
            } catch (Exception e) {
                logOnUi("PREPARE EXCEPTION: " + e);
                runOnUiThread(() -> preparedState.setText("Ошибка подготовки: " + e.getClass().getSimpleName()));
            } finally {
                setBusy(false);
                updateButtons();
            }
        });
    }

    private JSONObject prepareCandidate(Candidate c) throws Exception {
        SignerSupport.requireComplete(c.certificate);
        JSONObject action = new JSONObject().put("Название", c.actionName);
        JSONObject sanitizedCert = sanitizeCertificate(c.certificate);
        if (sanitizedCert.length() > 0) action.put("Сертификат", sanitizedCert);

        JSONObject stage = new JSONObject();
        if (c.stageId != null && !c.stageId.isEmpty()) {
            stage.put("Идентификатор", c.stageId);
        } else {
            stage.put("Название", c.stageName);
        }
        stage.put("Действие", action);

        JSONObject doc = new JSONObject()
                .put("Идентификатор", c.docId)
                .put("Этап", stage);

        JSONObject params = new JSONObject().put("Документ", doc);
        return rpc(TMS_SERVICE_URL, "СБИС.ПодготовитьДействие", params, sessionId);
    }

    private JSONObject sanitizeCertificate(JSONObject source) {
        JSONObject out = new JSONObject();
        if (source == null) return out;
        String[] fields = new String[]{"Отпечаток", "ФИО", "Должность", "ИНН", "ОГРНИП", "КодСтраны", "Название"};
        try {
            for (String f : fields) {
                String v = source.optString(f, "");
                if (!v.isEmpty()) out.put(f, v);
            }
        } catch (Exception ignored) {
        }
        return out;
    }

    private void confirmCreateOperation() {
        if (!prepared) {
            toast("Сначала подготовь две ЭТрН");
            return;
        }
        List<Candidate> selected = selectedCandidates();
        if (selected.size() != 2) {
            toast("Выбранные ЭТрН изменились — подготовь пакет заново");
            prepared = false;
            updateButtons();
            return;
        }

        String ogrnip = chooseOgrnip(selected);
        if (ogrnip.isEmpty()) {
            new AlertDialog.Builder(this)
                    .setTitle("R2 остановила Create")
                    .setMessage("Saby не вернул ОГРНИП в данных доступного сертификата. Для КЭП ИП параметр нужен в sabyCryptoOperation.Create. Я намеренно не предлагаю вводить его вручную в этой версии: пришли лог PREPARE, и я исправлю автоопределение.")
                    .setPositiveButton("Понятно", null)
                    .show();
            return;
        }

        int count = selected.get(0).preparedAttachmentIds.size() + selected.get(1).preparedAttachmentIds.size();
        String message = "Будет создан ОДИН реальный sabyCryptoOperation.Create:\n\n" +
                "ЭТрН A: № " + selected.get(0).number + "\n" +
                "ЭТрН B: № " + selected.get(1).number + "\n" +
                "Файлов в одном запросе: " + count + "\n\n" +
                "DocumentID будет от ЭТрН A, Files — из ОБЕИХ ЭТрН. Это и есть наш Gate. Запрос в Госключ через API отозвать нельзя.";

        new AlertDialog.Builder(this)
                .setTitle("Запустить пакетный Gate?")
                .setMessage(message)
                .setNegativeButton("Отмена", null)
                .setPositiveButton("ОТПРАВИТЬ", (d, which) -> createOperation(selected, ogrnip))
                .show();
    }

    private void createOperation(List<Candidate> selected, String ogrnip) {
        if (busy || createAttempted) { toast("Повторная отправка в Госключ заблокирована"); return; }
        if (!getPreferences(MODE_PRIVATE).edit().putBoolean("createAttempted", true).commit()) {
            log("CREATE STOP: не удалось сохранить защиту от повторной отправки."); return;
        }
        createAttempted = true;
        setBusy(true);
        executor.execute(() -> {
            try {
                JSONArray files = new JSONArray();
                for (Candidate c : selected) {
                    for (String id : c.preparedAttachmentIds) {
                        files.put(new JSONObject().put("AttachmentID", id));
                    }
                }

                JSONObject operation = new JSONObject()
                        .put("CertificateType", "Госключ")
                        .put("GoskeySignatureKind", "КЭПЮЛ")
                        .put("GoskeyOgrnip", ogrnip)
                        .put("DocumentID", selected.get(0).docId)
                        .put("Files", files);

                JSONObject params = new JSONObject().put("Operation", operation);
                logOnUi("CREATE: отправляю ОДНУ crypto operation; docs=2, files=" + files.length() + ". IDs/сессия в лог не выводятся.");

                JSONObject response = rpc(ONLINE_SERVICE_URL, "sabyCryptoOperation.Create", params, sessionId);
                logOnUi("CREATE RESPONSE\n" + pretty(response));

                String opId = response.has("error") ? "" : extractOperationId(response.opt("result"));
                if (opId != null && !opId.isEmpty()) {
                    operationId = opId;
                    getPreferences(MODE_PRIVATE).edit().putString("operationId", opId).commit();
                    logOnUi("GATE PENDING: получен OperationID. Это ещё не подтверждение пакетного подписания. Открой Госключ — проверяем, пришёл ли один пакет и включает ли он обе ЭТрН.");
                    runOnUiThread(() -> preparedState.setText("Запрос создан. Проверь обе ЭТрН в Госключе. Итог пока не доказан."));
                } else if (response.has("error")) {
                    runOnUiThread(() -> preparedState.setText("CREATE ERROR: Saby вернул ошибку. Скопируй лог — причина требует разбора."));
                } else {
                    runOnUiThread(() -> preparedState.setText("GATE INDETERMINATE: нет OperationID. Скопируй лог."));
                }
            } catch (Exception e) {
                logOnUi("CREATE EXCEPTION: " + e);
                runOnUiThread(() -> preparedState.setText("CREATE exception: " + e.getClass().getSimpleName()));
            } finally {
                setBusy(false);
                updateButtons();
            }
        });
    }

    private String chooseOgrnip(List<Candidate> selected) {
        for (Candidate c : selected) {
            if (c.ogrnip != null && !c.ogrnip.isEmpty()) return c.ogrnip;
        }
        return "";
    }

    private void getStatus() {
        if (!hasSession()) return;
        if (operationId == null || operationId.isEmpty()) {
            toast("OperationID ещё нет");
            return;
        }
        JSONObject params = new JSONObject();
        try {
            params.put("OperationID", operationId);
        } catch (Exception e) {
            log("STATUS JSON ERROR: " + e);
            return;
        }

        setBusy(true);
        executor.execute(() -> {
            try {
                JSONObject response = rpc(ONLINE_SERVICE_URL, "sabyCryptoOperation.GetStatus", params, sessionId);
                logOnUi("STATUS RESPONSE\n" + pretty(response));
            } catch (Exception e) {
                logOnUi("STATUS EXCEPTION: " + e);
            } finally {
                setBusy(false);
                updateButtons();
            }
        });
    }

    private JSONObject rpc(String endpoint, String method, JSONObject params, String session) throws Exception {
        JSONObject request = new JSONObject();
        request.put("jsonrpc", "2.0");
        request.put("method", method);
        request.put("params", params);
        request.put("id", System.currentTimeMillis());

        byte[] body = request.toString().getBytes(StandardCharsets.UTF_8);
        HttpURLConnection connection = (HttpURLConnection) new URL(endpoint).openConnection();
        connection.setConnectTimeout(20000);
        connection.setReadTimeout(90000);
        connection.setRequestMethod("POST");
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json-rpc;charset=utf-8");
        connection.setRequestProperty("User-Agent", USER_AGENT);
        if (session != null && !session.isEmpty()) {
            connection.setRequestProperty("X-SBISSessionID", session);
        }
        connection.setFixedLengthStreamingMode(body.length);

        try (OutputStream out = connection.getOutputStream()) {
            out.write(body);
        }

        int code = connection.getResponseCode();
        InputStream stream = code >= 400 ? connection.getErrorStream() : connection.getInputStream();
        String responseBody = readAll(stream);
        connection.disconnect();

        JSONObject envelope = new JSONObject();
        envelope.put("_httpCode", code);
        if (responseBody == null || responseBody.trim().isEmpty()) {
            envelope.put("raw", "");
            return envelope;
        }
        try {
            JSONObject parsed = new JSONObject(responseBody);
            if (!parsed.has("_httpCode")) parsed.put("_httpCode", code);
            return parsed;
        } catch (Exception ignored) {
            envelope.put("raw", responseBody);
            return envelope;
        }
    }

    private String readAll(InputStream in) throws Exception {
        if (in == null) return "";
        StringBuilder sb = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) sb.append(line).append('\n');
        }
        return sb.toString();
    }

    private JSONArray findDocumentsArray(Object root) {
        if (root instanceof JSONObject) {
            JSONObject o = (JSONObject) root;
            JSONArray direct = o.optJSONArray("Документ");
            if (direct != null) return direct;
            return findArrayByKey(o, "Документ");
        }
        if (root instanceof JSONArray) {
            JSONArray a = (JSONArray) root;
            for (int i = 0; i < a.length(); i++) {
                JSONArray found = findDocumentsArray(a.opt(i));
                if (found != null) return found;
            }
        }
        return null;
    }

    private JSONObject findDocumentObject(Object root) {
        if (root instanceof JSONObject) {
            JSONObject o = (JSONObject) root;
            Object direct = o.opt("Документ");
            if (direct instanceof JSONObject) return (JSONObject) direct;
            if (o.has("Идентификатор") && (o.has("Этап") || o.has("Тип"))) return o;
            JSONArray names = o.names();
            if (names != null) {
                for (int i = 0; i < names.length(); i++) {
                    JSONObject found = findDocumentObject(o.opt(names.optString(i)));
                    if (found != null) return found;
                }
            }
        } else if (root instanceof JSONArray) {
            JSONArray a = (JSONArray) root;
            for (int i = 0; i < a.length(); i++) {
                JSONObject found = findDocumentObject(a.opt(i));
                if (found != null) return found;
            }
        }
        return null;
    }

    private JSONArray findArrayByKey(Object root, String key) {
        if (root instanceof JSONObject) {
            JSONObject o = (JSONObject) root;
            Object direct = o.opt(key);
            if (direct instanceof JSONArray) return (JSONArray) direct;
            JSONArray names = o.names();
            if (names != null) {
                for (int i = 0; i < names.length(); i++) {
                    JSONArray found = findArrayByKey(o.opt(names.optString(i)), key);
                    if (found != null) return found;
                }
            }
        } else if (root instanceof JSONArray) {
            JSONArray a = (JSONArray) root;
            for (int i = 0; i < a.length(); i++) {
                JSONArray found = findArrayByKey(a.opt(i), key);
                if (found != null) return found;
            }
        }
        return null;
    }

    private void collectSignableAttachmentIds(Object root, List<String> out) {
        if (root instanceof JSONObject) {
            JSONObject o = (JSONObject) root;
            String required = o.optString("ТребуемоеДействие", "");
            if ("Подписать".equalsIgnoreCase(required)) {
                String id = o.optString("Идентификатор", "");
                if (!id.isEmpty()) out.add(id);
            }
            JSONArray names = o.names();
            if (names != null) {
                for (int i = 0; i < names.length(); i++) {
                    collectSignableAttachmentIds(o.opt(names.optString(i)), out);
                }
            }
        } else if (root instanceof JSONArray) {
            JSONArray a = (JSONArray) root;
            for (int i = 0; i < a.length(); i++) collectSignableAttachmentIds(a.opt(i), out);
        }
    }

    private void dedupe(List<String> values) {
        Set<String> seen = new HashSet<>();
        List<String> clean = new ArrayList<>();
        for (String v : values) {
            if (v != null && !v.isEmpty() && seen.add(v)) clean.add(v);
        }
        values.clear();
        values.addAll(clean);
    }

    private String deepString(Object root, String[] keys) {
        if (root instanceof JSONObject) {
            JSONObject o = (JSONObject) root;
            for (String key : keys) {
                Object direct = o.opt(key);
                if (direct instanceof String && !((String) direct).isEmpty()) return (String) direct;
            }
            JSONArray names = o.names();
            if (names != null) {
                for (int i = 0; i < names.length(); i++) {
                    String found = deepString(o.opt(names.optString(i)), keys);
                    if (found != null && !found.isEmpty()) return found;
                }
            }
        } else if (root instanceof JSONArray) {
            JSONArray a = (JSONArray) root;
            for (int i = 0; i < a.length(); i++) {
                String found = deepString(a.opt(i), keys);
                if (found != null && !found.isEmpty()) return found;
            }
        }
        return "";
    }

    private String extractOperationId(Object result) {
        return SignerSupport.operationId(result);
    }

    private boolean hasSession() {
        if (sessionId == null || sessionId.isEmpty()) {
            toast("Сначала войди в Saby");
            return false;
        }
        return true;
    }

    private boolean isYes(String value) {
        return "Да".equalsIgnoreCase(value) || "Yes".equalsIgnoreCase(value) || "true".equalsIgnoreCase(value);
    }

    private String firstNonEmpty(String a, String b) {
        return a != null && !a.isEmpty() ? a : (b == null ? "" : b);
    }

    private String shortId(String id) {
        if (id == null) return "";
        return id.length() <= 12 ? id : id.substring(0, 6) + "…" + id.substring(id.length() - 4);
    }

    private void updateButtons() {
        runOnUiThread(() -> {
            boolean signedIn = sessionId != null && !sessionId.isEmpty();
            boolean twoFactor = tempSessionId != null && !tempSessionId.isEmpty();
            for (CheckBox cb : candidateChecks) cb.setEnabled(!busy && !createAttempted);
            for (EditText f : new EditText[]{loginField, passwordField, accountField, codeField, dateField})
                if (f != null) f.setEnabled(!busy);

            if (authButton != null) authButton.setEnabled(!busy);
            if (sendCodeButton != null) sendCodeButton.setEnabled(!busy && twoFactor);
            if (confirmCodeButton != null) confirmCodeButton.setEnabled(!busy && twoFactor);
            if (loadButton != null) loadButton.setEnabled(!busy && signedIn);
            if (prepareButton != null) prepareButton.setEnabled(!busy && !prepared && !createAttempted && signedIn && selectedCandidates().size() == 2);
            if (createButton != null) createButton.setEnabled(!busy && !createAttempted && signedIn && prepared && selectedCandidates().size() == 2);
            if (statusButton != null) statusButton.setEnabled(!busy && signedIn && operationId != null && !operationId.isEmpty());
        });
    }

    private void setBusy(boolean value) {
        busy = value;
        updateButtons();
    }

    private void copyLog() {
        ClipboardManager clipboard = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (clipboard != null) {
            clipboard.setPrimaryClip(ClipData.newPlainText("ETRN R2 log", logView.getText()));
            toast("Лог скопирован");
        }
    }

    private void log(String message) {
        message = SignerSupport.redactText(message, secretsSnapshot());
        String old = logView == null ? "" : String.valueOf(logView.getText());
        if (logView != null) logView.setText(old + time() + " " + message + "\n");
    }

    private void logOnUi(String message) {
        runOnUiThread(() -> log(message));
    }

    private String time() {
        return new SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(new Date());
    }

    private String pretty(Object value) {
        try {
            Object safe = SignerSupport.safeJson(value, secretsSnapshot());
            if (safe instanceof JSONObject) return ((JSONObject) safe).toString(2);
            if (safe instanceof JSONArray) return ((JSONArray) safe).toString(2);
            return String.valueOf(safe);
        } catch (Exception e) { return "[ответ скрыт: ошибка безопасного форматирования]"; }
    }

    private String text(EditText e) {
        return e == null ? "" : e.getText().toString().trim();
    }

    private void toast(String text) {
        Toast.makeText(this, text, Toast.LENGTH_LONG).show();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
