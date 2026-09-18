package ru.komus.etrnprobe;
import org.junit.Test;
import static org.junit.Assert.*;
import org.json.JSONArray;
import org.json.JSONObject;

/** Synthetic wire-format tests, NOT a claim that Saby accepted a live request. */
public class SabyRpcContractTest {
    private static void assertSameJson(Object expected, Object actual) throws Exception {
        if (expected instanceof JSONObject) {
            assertTrue(actual instanceof JSONObject);
            JSONObject a = (JSONObject) expected, b = (JSONObject) actual;
            assertEquals(a.length(), b.length());
            java.util.Iterator<String> keys = a.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                assertTrue(b.has(key));
                assertSameJson(a.get(key), b.get(key));
            }
        } else if (expected instanceof JSONArray) {
            assertTrue(actual instanceof JSONArray);
            JSONArray a = (JSONArray) expected, b = (JSONArray) actual;
            assertEquals(a.length(), b.length());
            for (int i = 0; i < a.length(); i++) assertSameJson(a.get(i), b.get(i));
        } else {
            assertEquals(expected, actual);
        }
    }
    private JSONObject createArgs() throws Exception {
        return new JSONObject().put("Operation", new JSONObject()
                .put("CertificateType", "Госключ").put("GoskeySignatureKind", "КЭПЮЛ")
                .put("GoskeyOgrnip", "111111111111111").put("DocumentID", "document-A")
                .put("Files", new JSONArray().put(new JSONObject().put("AttachmentID", "file-A"))
                        .put(new JSONObject().put("AttachmentID", "file-B"))));
    }
    @Test public void createHasCaseSensitiveParamsArgument() throws Exception {
        JSONObject wire = new JSONObject(SabyRpcContract.envelope(SabyRpcContract.CREATE, createArgs(), 101).toString());
        assertEquals(1, wire.getJSONObject("params").length());
        assertTrue(wire.getJSONObject("params").has("Params"));
        assertFalse(wire.getJSONObject("params").has("Operation"));
        assertFalse(wire.getJSONObject("params").has("params"));
        assertTrue(wire.getJSONObject("params").getJSONObject("Params").has("Operation"));
    }
    @Test public void regressionOldWireFormatLacksNamedParams() throws Exception {
        JSONObject old = new JSONObject().put("params", createArgs());
        assertNull(old.getJSONObject("params").optJSONObject("Params"));
        JSONObject fixed = SabyRpcContract.envelope(SabyRpcContract.CREATE, createArgs(), 102);
        assertNotNull(fixed.getJSONObject("params").optJSONObject("Params"));
    }
    @Test public void bothPreparedAttachmentsSurviveSerialization() throws Exception {
        JSONArray files = new JSONObject(SabyRpcContract.envelope(SabyRpcContract.CREATE, createArgs(), 103).toString())
                .getJSONObject("params").getJSONObject("Params").getJSONObject("Operation").getJSONArray("Files");
        assertEquals(2, files.length());
        assertEquals("file-A", files.getJSONObject(0).getString("AttachmentID"));
        assertEquals("file-B", files.getJSONObject(1).getString("AttachmentID"));
    }
    @Test public void signatoryAndAnchorAreUnchanged() throws Exception {
        JSONObject operation = SabyRpcContract.envelope(SabyRpcContract.CREATE, createArgs(), 104)
                .getJSONObject("params").getJSONObject("Params").getJSONObject("Operation");
        assertEquals("document-A", operation.getString("DocumentID"));
        assertEquals("111111111111111", operation.getString("GoskeyOgrnip"));
        assertEquals("Госключ", operation.getString("CertificateType"));
        assertEquals("КЭПЮЛ", operation.getString("GoskeySignatureKind"));
    }
    @Test public void statusAlsoHasNamedParams() throws Exception {
        JSONObject wire = new JSONObject(SabyRpcContract.envelope(SabyRpcContract.STATUS,
                new JSONObject().put("OperationID", "operation-001"), 105).toString());
        assertEquals("operation-001", wire.getJSONObject("params").getJSONObject("Params").getString("OperationID"));
        assertEquals(1, wire.getJSONObject("params").length());
    }
    @Test public void loginWireIsUnchanged() throws Exception {
        JSONObject args = new JSONObject().put("Параметр", new JSONObject().put("Логин", "synthetic").put("Пароль", "synthetic-only"));
        assertSameJson(args, SabyRpcContract.envelope("СБИС.Аутентифицировать", args, 106).getJSONObject("params"));
    }
    @Test public void workingPrepareWireIsUnchanged() throws Exception {
        JSONObject args = new JSONObject().put("Документ", new JSONObject().put("Идентификатор", "document-A")
                .put("Этап", new JSONObject().put("Действие", new JSONObject().put("Название", "Принят"))));
        assertSameJson(args, SabyRpcContract.envelope("СБИС.ПодготовитьДействие", args, 107).getJSONObject("params"));
    }
    @Test public void listReadAndProfileWireAreUnchanged() throws Exception {
        for (String method : new String[]{"СБИС.СписокИзменений", "СБИС.ПрочитатьДокумент", "СБИС.ИнформацияОТекущемПользователе"}) {
            JSONObject args = new JSONObject().put("existing", new JSONObject().put("value", 1));
            assertSameJson(args, SabyRpcContract.envelope(method, args, 108).getJSONObject("params"));
        }
    }
    @Test public void sourceArgumentsAreNotMutated() throws Exception {
        JSONObject args = createArgs(); String before = args.toString();
        JSONObject wire = SabyRpcContract.envelope(SabyRpcContract.CREATE, args, 109);
        wire.getJSONObject("params").getJSONObject("Params").getJSONObject("Operation").put("DocumentID", "other");
        assertEquals(before, args.toString());
    }
    @Test public void jsonrpcMethodAndIdRemainCorrect() throws Exception {
        JSONObject wire = SabyRpcContract.envelope(SabyRpcContract.CREATE, createArgs(), 110);
        assertEquals("2.0", wire.getString("jsonrpc"));
        assertEquals(SabyRpcContract.CREATE, wire.getString("method"));
        assertEquals(110L, wire.getLong("id"));
        assertEquals(4, wire.length());
    }
    @Test public void rejectsDoubleWrappingBeforeNetwork() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> SabyRpcContract.envelope(SabyRpcContract.CREATE,
                new JSONObject().put("Params", createArgs()), 111));
    }
    @Test public void rejectsIncompleteCryptoArgumentsBeforeNetwork() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> SabyRpcContract.envelope(SabyRpcContract.CREATE, new JSONObject(), 112));
        assertThrows(IllegalArgumentException.class, () -> SabyRpcContract.envelope(SabyRpcContract.STATUS, new JSONObject(), 112));
        assertThrows(IllegalArgumentException.class, () -> SabyRpcContract.envelope(SabyRpcContract.STATUS, new JSONObject().put("OperationID", ""), 112));
    }
}
