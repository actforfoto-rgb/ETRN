package ru.komus.etrnprobe;

import android.app.Activity;
import android.os.Bundle;
import android.os.StrictMode;
import android.text.InputType;
import android.view.View;
import android.widget.*;
import org.json.*;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {
    private EditText login, password, account, docA, attA, docB, attB, ogrnip, endpoint, output;
    private Spinner signatureKind;
    private String session;
    private final String authEndpoint = "https://online.sbis.ru/auth/service/";
    private String lastOperationId;

    @Override public void onCreate(Bundle b) {
        super.onCreate(b);
        StrictMode.setThreadPolicy(new StrictMode.ThreadPolicy.Builder().permitAll().build());
        setContentView(buildUi());
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setPadding(24,24,24,24);
        TextView intro = new TextView(this);
        intro.setText("ETRN_GOSKEY_BATCH_PROBE_R1\n\nПроверяет один Gate: DocumentID ЭТрН A + AttachmentID от A и B в одной официальной sabyCryptoOperation.Create. НЕ вызывает СБИС.ВыполнитьДействие.");
        root.addView(intro);
        login = field(root,"Логин Saby",false); password = field(root,"Пароль Saby",true); account = field(root,"Номер аккаунта (необязательно)",false);
        docA = field(root,"DocumentID ЭТрН A",false); attA = field(root,"AttachmentID A",false);
        docB = field(root,"DocumentID ЭТрН B (для журнала)",false); attB = field(root,"AttachmentID B",false);
        ogrnip = field(root,"ОГРНИП (для КЭПЮЛ/ИП)",false);
        endpoint = field(root,"API endpoint",false); endpoint.setText("https://online.sbis.ru/service/?srv=1");
        signatureKind = new Spinner(this); String[] kinds={"КЭПЮЛ","КЭП","НЭП","КЭПДЛ"}; signatureKind.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, kinds)); root.addView(signatureKind);
        Button loginBtn = new Button(this); loginBtn.setText("1. Войти в Saby"); loginBtn.setOnClickListener(v -> runSafe(this::authenticate)); root.addView(loginBtn);
        Button createBtn = new Button(this); createBtn.setText("2. СОЗДАТЬ ОДНУ ОПЕРАЦИЮ НА 2 ФАЙЛА"); createBtn.setOnClickListener(v -> runSafe(this::createBatch)); root.addView(createBtn);
        Button statusBtn = new Button(this); statusBtn.setText("3. Проверить статус последней операции"); statusBtn.setOnClickListener(v -> runSafe(this::getStatus)); root.addView(statusBtn);
        output = new EditText(this); output.setMinLines(16); output.setGravity(48); output.setTextIsSelectable(true); root.addView(output);
        scroll.addView(root); return scroll;
    }

    private EditText field(LinearLayout root, String hint, boolean secret){
        EditText e=new EditText(this); e.setHint(hint); if(secret)e.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD); root.addView(e); return e;
    }

    private void runSafe(RunnableEx r){ try{r.run();}catch(Exception e){log("ERROR: "+e.getClass().getSimpleName()+": "+e.getMessage());} }
    private interface RunnableEx { void run() throws Exception; }

    private void authenticate() throws Exception {
        JSONObject p = new JSONObject(); p.put("Логин", login.getText().toString()); p.put("Пароль", password.getText().toString());
        if(!account.getText().toString().trim().isEmpty()) p.put("НомерАккаунта", account.getText().toString().trim());
        JSONObject params = new JSONObject().put("Параметр",p);
        JSONObject response = rpc(authEndpoint,"СБИС.Аутентифицировать",params,null);
        Object result=response.opt("result");
        if(result instanceof String){session=(String)result; log("AUTH OK: session получена (в журнал не выводится)");}
        else { log("AUTH RESPONSE: "+response.toString(2)); }
    }

    private void createBatch() throws Exception {
        requireSession();
        String a=docA.getText().toString().trim(), fa=attA.getText().toString().trim(), b=docB.getText().toString().trim(), fb=attB.getText().toString().trim();
        if(a.isEmpty()||fa.isEmpty()||b.isEmpty()||fb.isEmpty()) throw new IllegalArgumentException("Нужны два DocumentID/AttachmentID");
        JSONArray files = new JSONArray().put(new JSONObject().put("AttachmentID",fa)).put(new JSONObject().put("AttachmentID",fb));
        JSONObject op = new JSONObject();
        op.put("CertificateType","Госключ");
        op.put("GoskeySignatureKind",signatureKind.getSelectedItem().toString());
        if(!ogrnip.getText().toString().trim().isEmpty()) op.put("GoskeyOgrnip",ogrnip.getText().toString().trim());
        op.put("DocumentID",a); op.put("Files",files);
        JSONObject params = new JSONObject().put("Operation",op);
        log("GATE REQUEST: DocumentID(A)="+mask(a)+"; Attachment A="+mask(fa)+"; Attachment B(from doc B="+mask(b)+")="+mask(fb));
        JSONObject response = rpc(endpoint.getText().toString().trim(),"sabyCryptoOperation.Create",params,session);
        log("CREATE RESPONSE:\n"+response.toString(2));
        lastOperationId=findOperationId(response.opt("result"));
        if(lastOperationId!=null) log("GATE-A CANDIDATE: сервер принял 2 AttachmentID в одной операции; OperationID="+mask(lastOperationId));
    }

    private void getStatus() throws Exception {
        requireSession(); if(lastOperationId==null) throw new IllegalStateException("Нет OperationID из Create");
        JSONObject params=new JSONObject().put("OperationID",lastOperationId);
        JSONObject response=rpc(endpoint.getText().toString().trim(),"sabyCryptoOperation.GetStatus",params,session);
        log("STATUS RESPONSE:\n"+response.toString(2));
    }

    private JSONObject rpc(String url,String method,JSONObject params,String sess) throws Exception {
        JSONObject body=new JSONObject().put("jsonrpc","2.0").put("method",method).put("params",params).put("id",System.currentTimeMillis());
        byte[] bytes=body.toString().getBytes(StandardCharsets.UTF_8);
        HttpURLConnection c=(HttpURLConnection)new URL(url).openConnection(); c.setConnectTimeout(20000); c.setReadTimeout(30000); c.setDoOutput(true); c.setRequestMethod("POST");
        c.setRequestProperty("Content-Type","application/json-rpc;charset=utf-8"); c.setRequestProperty("User-Agent","ETRN_GOSKEY_BATCH_PROBE_R1/1.0");
        if(sess!=null)c.setRequestProperty("X-SBISSessionID",sess);
        try(OutputStream os=c.getOutputStream()){os.write(bytes);} int code=c.getResponseCode(); InputStream is=code>=400?c.getErrorStream():c.getInputStream();
        String text=readAll(is); if(text==null||text.isEmpty()) throw new IOException("HTTP "+code+" empty response");
        JSONObject result=new JSONObject(text); if(code>=400) log("HTTP "+code); return result;
    }

    private static String readAll(InputStream in) throws Exception { if(in==null)return ""; ByteArrayOutputStream b=new ByteArrayOutputStream(); byte[] x=new byte[8192]; int n; while((n=in.read(x))>=0)b.write(x,0,n); return b.toString("UTF-8"); }
    private void requireSession(){ if(session==null||session.isEmpty()) throw new IllegalStateException("Сначала войди в Saby"); }
    private void log(String s){ output.append((output.length()>0?"\n\n":"")+s); }
    private static String mask(String s){ if(s==null)return "null"; return s.length()<=10?s:s.substring(0,6)+"…"+s.substring(s.length()-4); }
    private static String findOperationId(Object r){ if(r==null)return null; if(r instanceof String)return (String)r; if(r instanceof JSONObject){JSONObject o=(JSONObject)r; for(String k:new String[]{"OperationID","OperationId","ИдентификаторОперации","id","ID"}){String v=o.optString(k,null); if(v!=null&&!v.isEmpty())return v;}} return null; }
}
