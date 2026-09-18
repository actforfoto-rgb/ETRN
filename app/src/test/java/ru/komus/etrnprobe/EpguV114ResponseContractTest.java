package ru.komus.etrnprobe;

import org.json.*;
import org.junit.Test;
import static org.junit.Assert.*;

public class EpguV114ResponseContractTest {
    @Test public void pushOrderIdAcceptsIntegerAndDigitString()throws Exception{
        assertEquals(77,EpguV114ResponseContract.parsePushOrderId(new JSONObject().put("orderId",77)));
        assertEquals(88,EpguV114ResponseContract.parsePushOrderId(new JSONObject().put("orderId","88")));
    }
    @Test public void pushOrderIdRejectsMissingFractionalAndZero()throws Exception{
        assertThrows(IllegalArgumentException.class,()->EpguV114ResponseContract.parsePushOrderId(new JSONObject()));
        assertThrows(IllegalArgumentException.class,()->EpguV114ResponseContract.parsePushOrderId(new JSONObject().put("orderId",1.5)));
        assertThrows(IllegalArgumentException.class,()->EpguV114ResponseContract.parsePushOrderId(new JSONObject().put("orderId",0)));
    }
    @Test public void parsesStringifiedOrderAndResponseFiles()throws Exception{
        JSONObject nested=new JSONObject()
            .put("id",123).put("eserviceId","60025907")
            .put("currentStatusHistoryId",9001).put("orderStatusId",42).put("orderStatusName","Подписано")
            .put("closed",true)
            .put("currentStatusHistory",new JSONObject().put("finalStatus",true))
            .put("orderResponseFiles",new JSONArray()
                .put(new JSONObject().put("fileName","piev_epgu.zip").put("link","terrabyte://bucket/piev_epgu.zip/result%20type"))
                .put(new JSONObject().put("fileName","").put("link","ignored")));
        EpguV114ResponseContract.OrderDetails d=EpguV114ResponseContract.parseOrder(
            new JSONObject().put("order",nested.toString()));
        assertEquals(123,d.orderId);assertEquals(Long.valueOf(9001),d.currentStatusHistoryId);
        assertEquals(Integer.valueOf(42),d.statusId);assertEquals("Подписано",d.statusName);
        assertTrue(d.finalStatus);assertTrue(d.closed);assertEquals(1,d.responseFiles.size());
        assertEquals("result type",d.responseFiles.get(0).objectType);
        assertTrue(d.responseFiles.get(0).downloadPath(9001).contains("mnemonic=piev_epgu.zip"));
        assertTrue(d.responseFiles.get(0).downloadPath(9001).contains("eserviceCode=60025907"));
    }
    @Test public void parsesObjectAndLegacyDirectShapes()throws Exception{
        JSONObject current=new JSONObject().put("id",91).put("statusId",7).put("title","Готово").put("finalStatus",true);
        JSONObject o=new JSONObject().put("orderId","5").put("serviceCode","60025907").put("currentStatusHistory",current);
        assertEquals(5,EpguV114ResponseContract.parseOrder(new JSONObject().put("order",o)).orderId);
        EpguV114ResponseContract.OrderDetails d=EpguV114ResponseContract.parseOrder(o);
        assertEquals(Long.valueOf(91),d.currentStatusHistoryId);assertEquals(Integer.valueOf(7),d.statusId);
        assertEquals("Готово",d.statusName);
    }
    @Test public void rejectsWrongServiceAndDuplicateResultNames()throws Exception{
        JSONObject wrong=new JSONObject().put("id",1).put("eserviceId","OTHER");
        assertThrows(IllegalArgumentException.class,()->EpguV114ResponseContract.parseOrder(wrong));
        JSONObject dup=new JSONObject().put("id",1).put("eserviceId","60025907")
            .put("orderResponseFiles",new JSONArray()
                .put(new JSONObject().put("fileName","x.zip").put("link","https://x/type"))
                .put(new JSONObject().put("fileName","x.zip").put("link","https://x/type2")));
        assertThrows(IllegalArgumentException.class,()->EpguV114ResponseContract.parseOrder(dup));
    }
    @Test public void objectTypeSupportsTerrabyteAndOrdinaryLinks()throws Exception{
        assertEquals("type 3",EpguV114ResponseContract.objectType("terrabyte://host/path/file.zip/type%203"));
        assertEquals("type",EpguV114ResponseContract.objectType("https://host/path/type?x=1"));
    }
}
