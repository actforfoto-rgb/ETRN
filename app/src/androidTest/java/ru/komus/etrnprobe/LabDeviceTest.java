package ru.komus.etrnprobe;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.*;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;
import android.content.ClipboardManager;
import android.content.Context;
import android.widget.*;
import org.json.*;
import java.lang.reflect.Field;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import static androidx.test.espresso.Espresso.onView;
import static androidx.test.espresso.matcher.ViewMatchers.*;
import static androidx.test.espresso.matcher.RootMatchers.isDialog;
import static androidx.test.espresso.action.ViewActions.*;
import static org.hamcrest.Matchers.startsWith;

@RunWith(AndroidJUnit4.class)
public class LabDeviceTest {
    private ActivityScenario<MainActivity> scenario;
    private static final String LOGIN="lab-user-2468", PASSWORD="lab-password-x";
    static class FakeTransport implements LabHooks.Transport {
        int calls,create,prepare,lists;boolean ambiguous,wrongId;
        public JSONObject call(String endpoint,JSONObject wire,String session)throws Exception{
            calls++;String method=wire.getString("method");Object result;
            if(method.equals("СБИС.Аутентифицировать")) result="lab-session-memory-only";
            else if(method.equals("СБИС.ИнформацияОТекущемПользователе")) result=new JSONObject().put("Пользователь",owner());
            else if(method.equals("СБИС.СписокДокументов")) {
                lists++;JSONObject f=wire.getJSONObject("params").getJSONObject("Фильтр");int page=Integer.parseInt(f.getJSONObject("Навигация").getString("Страница"));
                JSONArray docs=new JSONArray();for(int i=0;i<(page<2?50:20);i++)docs.put(new JSONObject().put("Идентификатор","d"+(page*50+i)).put("Дата",f.getString("ДатаС")).put("Номер","TEST-"+(page*50+i)).put("Тип","ConsignmentNote"));
                result=new JSONObject().put("Документ",docs).put("Навигация",new JSONObject().put("Страница",String.valueOf(page)).put("ЕстьЕще",page<2?"Да":"Нет"));
            }else if(method.equals("СБИС.ПрочитатьДокумент")){
                String id=wire.getJSONObject("params").getJSONObject("Документ").getString("Идентификатор");
                JSONArray a=new JSONArray().put(new JSONObject().put("Название","Принят").put("ТребуетПодписания","Да"));
                if(ambiguous)a.put(new JSONObject().put("Название","Не принят").put("ТребуетПодписания","Да"));
                result=new JSONObject().put("Документ",new JSONObject().put("Идентификатор",id).put("Тип","ConsignmentNote")
                        .put("НашаОрганизация",new JSONObject().put("СвФЛ",owner().put("ИНН","111111111111")))
                        .put("Этап",new JSONArray().put(new JSONObject().put("Идентификатор","stage-"+id).put("Название","Приемка").put("Действие",a))));
            }else if(method.equals("СБИС.ПодготовитьДействие")){
                prepare++;JSONObject d=wire.getJSONObject("params").getJSONObject("Документ");String id=d.getString("Идентификатор");
                JSONObject cert=d.getJSONObject("Этап").getJSONObject("Действие").getJSONObject("Сертификат");
                assertFalse(cert.getString("ФИО").isEmpty());assertEquals("111111111111",cert.getString("ИНН"));
                result=new JSONObject().put("Документ",new JSONObject().put("Идентификатор",id).put("Этап",new JSONArray()
                    .put(new JSONObject().put("Идентификатор","stage-"+id).put("Вложение",new JSONArray().put(new JSONObject().put("Идентификатор","file-"+id).put("ТребуемоеДействие","Подписать"))))));
            }else { create++;throw new IllegalStateException("LIVE_CRYPTO_CONTRACT_UNVERIFIED"); }
            return new JSONObject().put("jsonrpc","2.0").put("id",wire.getLong("id")+(wrongId?1:0)).put("result",result).put("_httpCode",200);
        }
        JSONObject owner()throws Exception{return new JSONObject().put("Фамилия","Тестов").put("Имя","Тест").put("Отчество","Тестович");}
    }
    @Before public void reset(){LabHooks.transport=null;Context c=InstrumentationRegistry.getInstrumentation().getTargetContext();c.getSharedPreferences("MainActivity",Context.MODE_PRIVATE).edit().clear().commit();}
    @After public void finish(){if(scenario!=null)scenario.close();LabHooks.transport=null;}
    @SuppressWarnings("unchecked") private static <T>T field(MainActivity a,String name){try{Field f=MainActivity.class.getDeclaredField(name);f.setAccessible(true);return (T)f.get(a);}catch(Exception e){throw new AssertionError(e);}}
    private String log(){AtomicReference<String> r=new AtomicReference<>("");scenario.onActivity(a->r.set(((TextView)field(a,"logView")).getText().toString()));return r.get();}
    private void waitLog(String expected)throws Exception{long end=System.currentTimeMillis()+15000;while(System.currentTimeMillis()<end){if(log().contains(expected))return;Thread.sleep(30);}fail("Did not see "+expected+"; log="+log());}
    private void launchLogin(FakeTransport fake){LabHooks.transport=fake;scenario=ActivityScenario.launch(MainActivity.class);scenario.onActivity(a->{((EditText)field(a,"loginField")).setText(LOGIN);((EditText)field(a,"passwordField")).setText(PASSWORD);((Button)field(a,"authButton")).performClick();});}
    @Test public void physicalNetworkIsDisabledWithoutTestHook()throws Exception{launchLogin(null);waitLog("LAB_NETWORK_DISABLED");assertFalse(log().contains(PASSWORD));assertFalse(log().contains("AUTH OK"));}
    @Test public void loginPaginationPreparationAndCopyWorkOnAndroid()throws Exception{
        FakeTransport fake=new FakeTransport();launchLogin(fake);waitLog("AUTH OK");
        scenario.onActivity(a->((Button)field(a,"loadButton")).performClick());waitLog("Проверено=120");assertEquals(3,fake.lists);
        scenario.onActivity(a->{List<CheckBox> c=field(a,"candidateChecks");assertEquals(120,c.size());c.get(0).setChecked(true);c.get(1).setChecked(true);((Button)field(a,"prepareButton")).performClick();});
        InstrumentationRegistry.getInstrumentation().waitForIdleSync();
        onView(withHint("ОГРНИП — 15 цифр")).inRoot(isDialog()).perform(scrollTo(),replaceText("111111111111111"),closeSoftKeyboard());
        onView(withText(startsWith("Это мои реквизиты."))).inRoot(isDialog()).perform(scrollTo(),click());
        onView(withText("Подготовить")).inRoot(isDialog()).perform(click());
        waitLog("PACKAGE READY");assertEquals(2,fake.prepare);assertEquals(0,fake.create);
        scenario.onActivity(a->{try{java.lang.reflect.Method m=MainActivity.class.getDeclaredMethod("copyLog");m.setAccessible(true);m.invoke(a);ClipboardManager cm=(ClipboardManager)a.getSystemService(Context.CLIPBOARD_SERVICE);String copied=cm.getPrimaryClip().getItemAt(0).getText().toString();assertTrue(copied.contains("PACKAGE READY"));assertFalse(copied.contains(PASSWORD));assertFalse(copied.contains("lab-session-memory-only"));assertFalse(copied.contains("111111111111"));}catch(Exception e){throw new AssertionError(e);}});
    }
    @Test public void ambiguousActionsAreBlockedOnAndroid()throws Exception{FakeTransport f=new FakeTransport();f.ambiguous=true;launchLogin(f);waitLog("AUTH OK");scenario.onActivity(a->((Button)field(a,"loadButton")).performClick());waitLog("кандидатов на подпись=0");assertTrue(log().contains("AMBIGUOUS_SIGNING_ACTION"));assertEquals(0,f.prepare);}
    @Test public void mismatchedResponseCannotAuthorize()throws Exception{FakeTransport f=new FakeTransport();f.wrongId=true;launchLogin(f);waitLog("RPC_ID_MISMATCH");assertFalse(log().contains("AUTH OK"));}
}
