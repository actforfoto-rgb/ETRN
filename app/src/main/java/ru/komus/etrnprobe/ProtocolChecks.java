package ru.komus.etrnprobe;

import org.json.*;
import java.util.*;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.format.ResolverStyle;

/** Strict parsing of documented parts only. No inferred crypto payload schema. */
public final class ProtocolChecks {
    private ProtocolChecks() {}
    public interface PageReader { JSONObject read(JSONObject params) throws Exception; }
    public static final class Choice {
        public final JSONObject stage, action;
        Choice(JSONObject stage, JSONObject action) { this.stage=stage;this.action=action; }
    }
    public static String nonempty(JSONObject o,String key) {
        Object v=o==null?null:o.opt(key);
        if(!(v instanceof String)||((String)v).trim().isEmpty()) throw new IllegalArgumentException("MISSING_"+key);
        return ((String)v).trim();
    }
    public static boolean yes(Object v) { return "Да".equals(v); }
    public static JSONObject twoFactor(JSONObject envelope) throws Exception {
        JSONObject error=envelope.optJSONObject("error");
        JSONObject data=error==null?null:error.optJSONObject("data");
        Object info=data==null?null:data.opt("addinfo");
        if(info instanceof String) {
            try { info=new JSONObject((String)info); } catch(JSONException ignored) { return new JSONObject(); }
        }
        if(!(info instanceof JSONObject)) return new JSONObject();
        JSONObject a=(JSONObject)info;
        String challenge=a.optString("Идентификатор", "");
        String temp=a.optString("SessionID",a.optString("ИдентификаторСессии", ""));
        if(challenge.isEmpty()||temp.isEmpty()) return new JSONObject();
        return new JSONObject().put("challenge",challenge).put("session",temp);
    }
    public static List<Choice> signingChoices(JSONObject doc) {
        List<Choice> out=new ArrayList<>();JSONArray stages=doc.optJSONArray("Этап");
        if(stages==null) return out;
        for(int i=0;i<stages.length();i++) {
            JSONObject stage=stages.optJSONObject(i);
            if(stage==null||stage.optString("Идентификатор", "").isEmpty()) continue;
            JSONArray actions=stage.optJSONArray("Действие");if(actions==null) continue;
            for(int j=0;j<actions.length();j++) {
                JSONObject action=actions.optJSONObject(j);
                if(action!=null&&yes(action.opt("ТребуетПодписания"))&&!action.optString("Название", "").isEmpty()) out.add(new Choice(stage,action));
            }
        }
        return out;
    }
    public static Choice onlySigningChoice(JSONObject doc) {
        List<Choice> c=signingChoices(doc);
        if(c.size()!=1) throw new IllegalArgumentException(c.isEmpty()?"NO_SIGNING_ACTION":"AMBIGUOUS_SIGNING_ACTION");
        return c.get(0);
    }
    public static JSONObject exactDocument(Object result,String id) {
        JSONObject found=null;List<JSONObject> possible=new ArrayList<>();
        if(result instanceof JSONObject) {
            JSONObject r=(JSONObject)result;Object doc=r.opt("Документ");
            if(doc==null&&r.has("Идентификатор")) possible.add(r);
            else possible.addAll(SignerSupport.objects(doc));
        } else if(result instanceof JSONArray) {
            JSONArray r=(JSONArray)result;
            for(int i=0;i<r.length();i++) {
                JSONObject x=r.optJSONObject(i);if(x==null) continue;
                if(x.has("Документ")) possible.addAll(SignerSupport.objects(x.opt("Документ")));else possible.add(x);
            }
        }
        for(JSONObject d:possible) if(id.equals(d.optString("Идентификатор", ""))) {
            if(found!=null) throw new IllegalArgumentException("DUPLICATE_DOCUMENT_RESPONSE");found=d;
        }
        if(found==null) throw new IllegalArgumentException("DOCUMENT_RESPONSE_MISMATCH");return found;
    }
    public static List<String> signableIds(Object result,String docId,String stageId) {
        JSONObject doc=exactDocument(result,docId);JSONArray stages=doc.optJSONArray("Этап");
        if(stages==null) throw new IllegalArgumentException("NO_PREPARED_STAGES");
        JSONObject matched=null;
        for(int i=0;i<stages.length();i++) {
            JSONObject s=stages.optJSONObject(i);
            if(s!=null&&stageId.equals(s.optString("Идентификатор", ""))) {
                if(matched!=null) throw new IllegalArgumentException("DUPLICATE_STAGE_RESPONSE");matched=s;
            }
        }
        if(matched==null) throw new IllegalArgumentException("PREPARED_STAGE_MISMATCH");
        JSONArray files=matched.optJSONArray("Вложение");List<String> out=new ArrayList<>();Set<String> seen=new HashSet<>();
        if(files!=null) for(int i=0;i<files.length();i++) {
            JSONObject f=files.optJSONObject(i);
            if(f==null||!"Подписать".equals(f.optString("ТребуемоеДействие", ""))) continue;
            String id=nonempty(f,"Идентификатор");
            if(!seen.add(id)) throw new IllegalArgumentException("DUPLICATE_SIGNABLE_ATTACHMENT");out.add(id);
        }
        if(out.isEmpty()) throw new IllegalArgumentException("NO_SIGNABLE_FILES_IN_SELECTED_STAGE");return out;
    }
    public static JSONObject validateEnvelope(JSONObject request,JSONObject response) throws Exception {
        if(response==null||!"2.0".equals(response.optString("jsonrpc"))) throw new IllegalArgumentException("INVALID_JSONRPC_ENVELOPE");
        if(!String.valueOf(request.get("id")).equals(String.valueOf(response.opt("id")))) throw new IllegalArgumentException("RPC_ID_MISMATCH");
        boolean result=response.has("result"),error=response.has("error");
        if(result==error) throw new IllegalArgumentException("RESULT_XOR_ERROR_REQUIRED");
        if(error&&response.optJSONObject("error")==null) throw new IllegalArgumentException("ERROR_NOT_OBJECT");return response;
    }
    /** Document-date pagination. Availability for ConsignmentNote on TMS needs live acceptance. */
    public static List<JSONObject> listByDocumentDate(String day,PageReader reader) throws Exception {
        LocalDate.parse(day,DateTimeFormatter.ofPattern("dd.MM.uuuu").withResolverStyle(ResolverStyle.STRICT));
        LinkedHashMap<String,JSONObject> docs=new LinkedHashMap<>();Set<String> pagesSeen=new HashSet<>();
        for(int page=0;page<200;page++) {
            JSONObject filter=new JSONObject().put("ДатаС",day).put("ДатаПо",day).put("Тип","ConsignmentNote")
                    .put("Навигация",new JSONObject().put("РазмерСтраницы","50").put("Страница",String.valueOf(page)));
            JSONObject response=reader.read(new JSONObject().put("Фильтр",filter));
            if(response.has("error")) throw new IllegalStateException("LIST_RPC_ERROR");
            JSONObject result=response.optJSONObject("result");if(result==null) throw new IllegalArgumentException("LIST_RESULT_NOT_OBJECT");
            JSONArray entries=result.optJSONArray("Документ");JSONObject nav=result.optJSONObject("Навигация");
            if(entries==null||nav==null) throw new IllegalArgumentException("LIST_NAVIGATION_MISSING");
            if(!String.valueOf(page).equals(nav.optString("Страница"))) throw new IllegalArgumentException("WRONG_PAGE_NUMBER");
            String more=nav.optString("ЕстьЕще");if(!more.equals("Да")&&!more.equals("Нет")) throw new IllegalArgumentException("MORE_FLAG_UNKNOWN");
            StringBuilder fingerprint=new StringBuilder();
            for(int i=0;i<entries.length();i++) {
                JSONObject d=entries.getJSONObject(i);String id=nonempty(d,"Идентификатор");fingerprint.append(id).append('|');
                if(!day.equals(d.optString("Дата"))) throw new IllegalArgumentException("DOCUMENT_DATE_MISMATCH");
                if(d.has("Тип")&&!"ConsignmentNote".equals(d.optString("Тип"))) throw new IllegalArgumentException("DOCUMENT_TYPE_MISMATCH");docs.put(id,d);
            }
            if(!more.equals("Да")) return new ArrayList<>(docs.values());
            if(entries.length()==0||!pagesSeen.add(fingerprint.toString())) throw new IllegalArgumentException("PAGINATION_STALLED");
        }
        throw new IllegalArgumentException("PAGE_LIMIT_NO_PARTIAL_SUCCESS");
    }
    public static JSONObject finalization(BatchEngine.Document doc,List<BatchEngine.Signature> signatures) throws Exception {
        if(signatures.isEmpty()) throw new IllegalArgumentException("NO_SIGNATURES");
        JSONArray files=new JSONArray();Set<String> seen=new HashSet<>();
        for(BatchEngine.Signature s:signatures) {
            if(!doc.id.equals(s.document)||!seen.add(s.attachment)) throw new IllegalArgumentException("SIGNATURE_DOCUMENT_MISMATCH");
            JSONObject binary=new JSONObject().put("ДвоичныеДанные",java.util.Base64.getEncoder().encodeToString(s.bytes()));
            files.put(new JSONObject().put("Идентификатор",s.attachment).put("Подпись",new JSONArray().put(new JSONObject().put("Файл",binary))));
        }
        JSONObject stage=new JSONObject().put("Идентификатор",doc.stage)
                .put("Действие",new JSONArray().put(new JSONObject().put("Название",doc.action))).put("Вложение",files);
        return new JSONObject().put("Документ",new JSONObject().put("Редакция",new JSONObject().put("Идентификатор",doc.revision)).put("Этап",stage));
    }
}
