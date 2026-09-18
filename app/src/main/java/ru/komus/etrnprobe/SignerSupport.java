package ru.komus.etrnprobe;

import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Locale;
import java.util.HashSet;
import java.util.Set;

/** Preparation uses confirmed signatory requisites, not a guessed signing key. */
public final class SignerSupport {
    private SignerSupport() {}
    private static final String[] FIELDS = {"ФИО", "ИНН", "ОГРНИП", "Должность"};
    public static String str(JSONObject o, String key) {
        if (o == null) return "";
        Object v = o.opt(key);
        return v instanceof String ? ((String) v).trim() : "";
    }
    public static String name(JSONObject o) {
        String direct = str(o, "ФИО");
        if (!direct.isEmpty()) return direct;
        StringBuilder out = new StringBuilder();
        for (String k : new String[]{"Фамилия", "Имя", "Отчество"}) {
            String part = str(o, k);
            if (!part.isEmpty()) {
                if (out.length() > 0) out.append(' ');
                out.append(part.replaceAll("\\s+", "_"));
            }
        }
        return out.toString();
    }
    public static String norm(String s) {
        return s == null ? "" : s.replace('_', ' ').trim().replaceAll("\\s+", " ").toLowerCase(Locale.ROOT);
    }
    public static JSONObject user(JSONObject response) {
        if (response == null || response.has("error")) return new JSONObject();
        JSONObject result = response.optJSONObject("result");
        if (result == null) return new JSONObject();
        JSONObject u = result.optJSONObject("Пользователь");
        return u == null ? new JSONObject() : u;
    }
    public static JSONObject ownFl(JSONObject doc) {
        JSONObject own = doc == null ? null : doc.optJSONObject("НашаОрганизация");
        JSONObject fl = own == null ? null : own.optJSONObject("СвФЛ");
        return fl == null ? new JSONObject() : fl;
    }
    public static List<JSONObject> objects(Object source) {
        List<JSONObject> list = new ArrayList<>();
        if (source instanceof JSONObject) list.add((JSONObject) source);
        if (source instanceof JSONArray) {
            JSONArray a = (JSONArray) source;
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.optJSONObject(i);
                if (o != null) list.add(o);
            }
        }
        return list;
    }
    /** Only the selected action, document certificate list and OUR organization are examined. */
    public static JSONObject suggest(JSONObject profile, JSONObject own, Object actionCert, Object docCert) throws Exception {
        JSONObject result = new JSONObject();
        String caller = name(profile), ownName = name(own), ownInn = str(own, "ИНН");
        String fio = !caller.isEmpty() ? caller : ownName;
        result.put("ФИО", fio);
        boolean ownMatches = !fio.isEmpty() && norm(fio).equals(norm(ownName));
        if (ownMatches && ownInn.matches("[0-9]{12}")) {
            result.put("ИНН", ownInn);
            String og = str(own, "ОГРНИП");
            if (og.matches("[0-9]{15}")) result.put("ОГРНИП", og);
        }
        List<JSONObject> sources = objects(actionCert);
        sources.addAll(objects(docCert));
        List<JSONObject> matches = new ArrayList<>();
        Set<String> identities = new HashSet<>();
        for (JSONObject c : sources) {
            String n = name(c), inn = str(c, "ИНН");
            if (fio.isEmpty() || !norm(fio).equals(norm(n)) || !inn.matches("[0-9]{12}")) continue;
            if (!ownInn.isEmpty() && !ownInn.equals(inn)) continue;
            matches.add(c);
            identities.add(inn);
        }
        // Multiple owners are never resolved by choosing the first certificate.
        if (identities.size() == 1) {
            if (str(result, "ИНН").isEmpty()) result.put("ИНН", identities.iterator().next());
            for (String key : new String[]{"ОГРНИП", "Должность"}) {
                Set<String> values = new HashSet<>();
                String known = str(result, key);
                if (!known.isEmpty()) values.add(known);
                for (JSONObject c : matches) {
                    String v = str(c, key);
                    if (!v.isEmpty() && (!key.equals("ОГРНИП") || v.matches("[0-9]{15}"))) values.add(v);
                }
                if (values.size() == 1) result.put(key, values.iterator().next());
                else if (values.size() > 1) result.remove(key);
            }
        }
        return result;
    }
    public static JSONObject merge(JSONObject a, JSONObject b) throws Exception {
        JSONObject result = new JSONObject();
        for (String key : FIELDS) {
            String av = str(a, key), bv = str(b, key);
            if (!av.isEmpty() && !bv.isEmpty() && !norm(av).equals(norm(bv))) {
                if (key.equals("Должность")) continue;
                throw new IllegalArgumentException("Разные сведения о подписанте в двух ЭТрН: " + key + ". Подготовка не выполнена.");
            }
            result.put(key, !av.isEmpty() ? av : bv);
        }
        return result;
    }
    public static void validate(JSONObject signer, JSONObject profile, JSONObject ownA, JSONObject ownB) {
        String fio = str(signer, "ФИО"), inn = str(signer, "ИНН"), ogrnip = str(signer, "ОГРНИП");
        if (fio.isEmpty() || norm(fio).split(" ").length < 2) throw new IllegalArgumentException("Укажи полные фамилию и имя подписанта, отчество — при наличии.");
        if (!inn.matches("[0-9]{12}")) throw new IllegalArgumentException("ИНН водителя-ИП должен содержать 12 цифр.");
        if (!ogrnip.matches("[0-9]{15}")) throw new IllegalArgumentException("ОГРНИП должен содержать 15 цифр. Возьми его из своих реквизитов ИП или сертификата Госключа.");
        if (str(signer, "Должность").isEmpty()) throw new IllegalArgumentException("Укажи статус подписанта. Для владельца ИП — Индивидуальный предприниматель.");
        String caller = name(profile);
        if (!caller.isEmpty() && !norm(caller).equals(norm(fio))) throw new IllegalArgumentException("ФИО не совпадает с пользователем, вошедшим в Saby. Нужен личный вход подписывающего водителя.");
        for (JSONObject own : new JSONObject[]{ownA, ownB}) {
            String expectedInn = str(own, "ИНН"), expectedName = name(own);
            if (!expectedInn.isEmpty() && !expectedInn.equals(inn)) throw new IllegalArgumentException("ИНН подписанта не совпадает с ИП на нашей стороне ЭТрН. Подготовка заблокирована.");
            if (!expectedName.isEmpty() && !norm(expectedName).equals(norm(fio))) throw new IllegalArgumentException("Владелец ИП в ЭТрН не совпадает с подписантом. Этот пилот рассчитан на самого водителя-ИП.");
            String expectedOgrnip = str(own, "ОГРНИП");
            if (!expectedOgrnip.isEmpty() && !expectedOgrnip.equals(ogrnip)) throw new IllegalArgumentException("ОГРНИП не совпадает с нашей стороной ЭТрН.");
        }
    }
    public static void requireComplete(JSONObject signer) {
        for (String key : FIELDS) if (str(signer, key).isEmpty()) throw new IllegalArgumentException("CERT PREFLIGHT STOP: не заполнено Сертификат." + key);
        if (!str(signer, "ИНН").matches("[0-9]{12}") || !str(signer, "ОГРНИП").matches("[0-9]{15}")) throw new IllegalArgumentException("CERT PREFLIGHT STOP: формат ИНН/ОГРНИП");
    }
    public static String operationId(Object result) {
        Object value = result;
        if (result instanceof JSONObject) {
            JSONObject o = (JSONObject) result;
            value = null;
            for (String key : new String[]{"OperationID", "OperationId", "operationId", "ИдентификаторОперации"}) {
                if (o.opt(key) instanceof String) { value = o.opt(key); break; }
            }
        }
        if (!(value instanceof String)) return "";
        String s = ((String) value).trim();
        return s.matches("[A-Za-z0-9_:{\\}\\-]{8,256}") ? s : "";
    }
    public static String redactText(String s, Collection<String> secrets) {
        if (s == null) return "";
        String out = s;
        if (secrets != null) for (String secret : secrets) {
            if (secret != null && !secret.isEmpty()) out = out.replace(secret, "[скрыто]");
        }
        return out.replaceAll("https?://[^\\s\\\"<>]+", "[ссылка скрыта]")
                .replaceAll("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[email скрыт]")
                .replaceAll("(?<![0-9])[0-9]{10,15}(?![0-9])", "[реквизит скрыт]");
    }
    /** Unknown string fields (including session, file contents and links) are not exported. */
    public static Object safeJson(Object value, Collection<String> secrets) throws Exception {
        if (value instanceof JSONObject) {
            JSONObject src = (JSONObject) value, out = new JSONObject();
            java.util.Iterator<String> keys = src.keys();
            while (keys.hasNext()) {
                String k = keys.next(); Object v = src.opt(k);
                String lower = k.toLowerCase(Locale.ROOT);
                boolean visible = lower.matches("jsonrpc|code|error_code|_httpcode|type|message|details|classid|status|state|statuscode|errorcode|статус|состояние|требуемоедействие|требуетподписания|естьеще");
                if (lower.matches(".*(пароль|password|session|сесси|token|токен|двоичн|binary|base64|ссылка|url|email|фио|фамилия|имя|отчество|инн|огрн|снилс|certificate|сертификат).*")) out.put(k, "[скрыто]");
                else if (v instanceof JSONObject || v instanceof JSONArray) out.put(k, safeJson(v, secrets));
                else if (v instanceof String) out.put(k, visible ? redactText((String) v, secrets) : "[строка скрыта]");
                else if (v instanceof Number) out.put(k, visible ? v : "[число скрыто]");
                else out.put(k, v);
            }
            return out;
        }
        if (value instanceof JSONArray) {
            JSONArray src = (JSONArray) value, out = new JSONArray();
            for (int i = 0; i < Math.min(src.length(), 20); i++) out.put(safeJson(src.opt(i), secrets));
            if (src.length() > 20) out.put("[остальные элементы скрыты]");
            return out;
        }
        if (value instanceof String) return "[строка скрыта]";
        return value;
    }
}
