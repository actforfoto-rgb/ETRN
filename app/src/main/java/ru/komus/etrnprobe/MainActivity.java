package ru.komus.etrnprobe;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
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
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final String AUTH_URL = "https://online.sbis.ru/auth/service/";
    private static final String SERVICE_URL = "https://online.sbis.ru/service/?srv=1";
    private static final String USER_AGENT = "KOMUS-ETRN-GOSKEY-BATCH-PROBE-R1/1.0";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    private EditText loginField;
    private EditText passwordField;
    private EditText accountField;
    private EditText ogrnipField;
    private EditText documentIdField;
    private EditText attachmentAField;
    private EditText attachmentBField;
    private EditText operationIdField;
    private Spinner signatureKindSpinner;
    private TextView logView;
    private Button authButton;
    private Button esiaButton;
    private Button createButton;
    private Button statusButton;

    private volatile String sessionId;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildUi());
        updateButtons();
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        sessionId = null;
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
        title.setText("ETRN_GOSKEY_BATCH_PROBE_R1");
        title.setTextSize(22);
        root.addView(title);

        TextView subtitle = new TextView(this);
        subtitle.setText("Gate: один DocumentID + два AttachmentID из двух разных ЭТрН → одна операция Госключа. Приложение НЕ вызывает СБИС.ВыполнитьДействие.");
        subtitle.setPadding(0, dp(8), 0, dp(12));
        root.addView(subtitle);

        loginField = addField(root, "Логин Saby", false);
        passwordField = addField(root, "Пароль Saby", true);
        accountField = addField(root, "Номер аккаунта (если нужен)", false);

        authButton = addButton(root, "1. Войти в Saby", v -> authenticate());
        esiaButton = addButton(root, "2. Проверить привязку ЕСИА", v -> checkEsia());

        TextView signHeader = new TextView(this);
        signHeader.setText("Параметры Госключа");
        signHeader.setTextSize(18);
        signHeader.setPadding(0, dp(16), 0, dp(4));
        root.addView(signHeader);

        signatureKindSpinner = new Spinner(this);
        String[] kinds = new String[]{"КЭПЮЛ", "КЭП", "НЭП", "КЭПДЛ"};
        signatureKindSpinner.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, kinds));
        root.addView(signatureKindSpinner, matchWrap());

        ogrnipField = addField(root, "ОГРНИП водителя (для КЭПЮЛ)", false);
        documentIdField = addField(root, "DocumentID ЭТрН A", false);
        attachmentAField = addField(root, "AttachmentID A (из ЭТрН A)", false);
        attachmentBField = addField(root, "AttachmentID B (из ДРУГОЙ ЭТрН B)", false);

        createButton = addButton(root, "3. СОЗДАТЬ ОДНУ операцию на 2 вложения", v -> confirmCreate());

        operationIdField = addField(root, "OperationID (заполнится из ответа; можно вставить вручную)", false);
        statusButton = addButton(root, "4. Проверить статус операции", v -> getStatus());

        Button clearButton = addButton(root, "Очистить локальный лог", v -> logView.setText(""));
        clearButton.setEnabled(true);

        TextView warning = new TextView(this);
        warning.setText("ВАЖНО: Create создаёт реальный запрос в Госключ. По документации Saby такой запрос нельзя отозвать через API. Запускай только на двух реальных ЭТрН, которые действительно ждут подписи этого водителя.");
        warning.setPadding(0, dp(16), 0, dp(8));
        root.addView(warning);

        logView = new TextView(this);
        logView.setTextIsSelectable(true);
        logView.setTextSize(12);
        root.addView(logView, matchWrap());

        return scroll;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private EditText addField(LinearLayout root, String hint, boolean password) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setSingleLine(false);
        if (password) {
            e.setSingleLine(true);
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
                if (response.has("error")) {
                    logOnUi("AUTH ERROR\n" + pretty(response));
                } else {
                    Object result = response.opt("result");
                    if (result instanceof String && !((String) result).isEmpty()) {
                        sessionId = (String) result;
                        logOnUi("AUTH OK. Session получена и хранится только в памяти процесса.");
                    } else {
                        logOnUi("AUTH: неожиданный ответ\n" + pretty(response));
                    }
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

    private void checkEsia() {
        if (!hasSession()) return;
        JSONObject params = new JSONObject();
        JSONObject parameter = new JSONObject();
        try {
            parameter.put("ДопПоля", "СписокПривязанныхВнешнихПровайдеров");
            params.put("Параметр", parameter);
        } catch (Exception e) {
            log("ESIA JSON ERROR: " + e);
            return;
        }
        callService("СБИС.ИнформацияОТекущемПользователе", params, "ESIA CHECK");
    }

    private void confirmCreate() {
        if (!hasSession()) return;
        if (text(documentIdField).isEmpty() || text(attachmentAField).isEmpty() || text(attachmentBField).isEmpty()) {
            toast("Нужны DocumentID и оба AttachmentID");
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("Создать реальный запрос в Госключ?")
                .setMessage("Будет отправлен ОДИН sabyCryptoOperation.Create: DocumentID ЭТрН A + AttachmentID A + AttachmentID B из другой ЭТрН. Saby предупреждает, что такой запрос через API отозвать нельзя. Сам документ дальше по этапу приложение не проведёт.")
                .setNegativeButton("Отмена", null)
                .setPositiveButton("Создать", (d, which) -> createOperation())
                .show();
    }

    private void createOperation() {
        JSONObject operation = new JSONObject();
        JSONObject params = new JSONObject();
        JSONArray files = new JSONArray();
        try {
            operation.put("CertificateType", "Госключ");
            operation.put("GoskeySignatureKind", String.valueOf(signatureKindSpinner.getSelectedItem()));
            if (!text(ogrnipField).isEmpty()) operation.put("GoskeyOgrnip", text(ogrnipField));
            operation.put("DocumentID", text(documentIdField));
            files.put(new JSONObject().put("AttachmentID", text(attachmentAField)));
            files.put(new JSONObject().put("AttachmentID", text(attachmentBField)));
            operation.put("Files", files);
            params.put("Operation", operation);
        } catch (Exception e) {
            log("CREATE JSON ERROR: " + e);
            return;
        }

        setBusy(true);
        log("CREATE REQUEST (секреты/сессия не логируются):\n" + pretty(params));
        executor.execute(() -> {
            try {
                JSONObject response = rpc(SERVICE_URL, "sabyCryptoOperation.Create", params, sessionId);
                logOnUi("CREATE RESPONSE\n" + pretty(response));
                String opId = extractOperationId(response.opt("result"));
                if (opId != null && !opId.isEmpty()) {
                    runOnUiThread(() -> operationIdField.setText(opId));
                    logOnUi("GATE SERVER ACCEPT: OperationID=" + opId + "\nТеперь смотри Госключ на телефоне: пришёл ли один запрос и содержит ли он оба документа/файла.");
                } else if (response.has("error")) {
                    logOnUi("GATE SERVER REJECT: сервер вернул error. Это и есть диагностический результат; полный ответ выше.");
                } else {
                    logOnUi("GATE INDETERMINATE: нет явного OperationID. Нужен разбор полного ответа выше.");
                }
            } catch (Exception e) {
                logOnUi("CREATE EXCEPTION: " + e);
            } finally {
                runOnUiThread(() -> {
                    setBusy(false);
                    updateButtons();
                });
            }
        });
    }

    private void getStatus() {
        if (!hasSession()) return;
        String opId = text(operationIdField);
        if (opId.isEmpty()) {
            toast("Нужен OperationID");
            return;
        }
        JSONObject params = new JSONObject();
        try {
            params.put("OperationID", opId);
        } catch (Exception e) {
            log("STATUS JSON ERROR: " + e);
            return;
        }
        callService("sabyCryptoOperation.GetStatus", params, "STATUS");
    }

    private void callService(String method, JSONObject params, String label) {
        setBusy(true);
        executor.execute(() -> {
            try {
                JSONObject response = rpc(SERVICE_URL, method, params, sessionId);
                logOnUi(label + " RESPONSE\n" + pretty(response));
            } catch (Exception e) {
                logOnUi(label + " EXCEPTION: " + e);
            } finally {
                runOnUiThread(() -> {
                    setBusy(false);
                    updateButtons();
                });
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
        connection.setReadTimeout(60000);
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
        envelope.put("httpCode", code);
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

    private String extractOperationId(Object result) {
        if (result == null || result == JSONObject.NULL) return null;
        if (result instanceof String) return (String) result;
        if (result instanceof JSONObject) {
            JSONObject o = (JSONObject) result;
            String[] names = new String[]{"OperationID", "OperationId", "operationId", "ИдентификаторОперации"};
            for (String name : names) {
                String value = o.optString(name, "");
                if (!value.isEmpty()) return value;
            }
        }
        return null;
    }

    private boolean hasSession() {
        if (sessionId == null || sessionId.isEmpty()) {
            toast("Сначала войди в Saby");
            return false;
        }
        return true;
    }

    private void updateButtons() {
        boolean signedIn = sessionId != null && !sessionId.isEmpty();
        if (esiaButton != null) esiaButton.setEnabled(signedIn);
        if (createButton != null) createButton.setEnabled(signedIn);
        if (statusButton != null) statusButton.setEnabled(signedIn);
    }

    private void setBusy(boolean busy) {
        runOnUiThread(() -> {
            if (authButton != null) authButton.setEnabled(!busy);
            if (esiaButton != null) esiaButton.setEnabled(!busy && sessionId != null && !sessionId.isEmpty());
            if (createButton != null) createButton.setEnabled(!busy && sessionId != null && !sessionId.isEmpty());
            if (statusButton != null) statusButton.setEnabled(!busy && sessionId != null && !sessionId.isEmpty());
        });
    }

    private void log(String text) {
        if (logView == null) return;
        logView.append(text + "\n\n");
    }

    private void logOnUi(String text) {
        runOnUiThread(() -> log(text));
    }

    private String text(EditText field) {
        return field.getText().toString().trim();
    }

    private String pretty(Object value) {
        try {
            if (value instanceof JSONObject) return ((JSONObject) value).toString(2);
            if (value instanceof JSONArray) return ((JSONArray) value).toString(2);
            return String.valueOf(value);
        } catch (Exception e) {
            return String.valueOf(value);
        }
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
