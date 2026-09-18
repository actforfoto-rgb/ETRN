package ru.komus.etrnprobe;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import java.nio.charset.StandardCharsets;
import java.util.*;
import static org.junit.Assert.*;

public class SabyExecuteActionPayloadTest {
    @Test public void revisionStageActionAndDetachedSignatureAreMappedExactly() throws Exception {
        JSONObject root=SabyExecuteActionPayload.build(
            "doc-ignored","rev-1","stage-1","Принят",
            Collections.singletonList(new SabyExecuteActionPayload.SignedAttachment(
                "att-1","signature".getBytes(StandardCharsets.UTF_8))));
        JSONObject doc=root.getJSONObject("Документ");
        assertFalse(doc.has("Идентификатор"));
        assertEquals("rev-1",doc.getJSONObject("Редакция").getString("Идентификатор"));
        JSONObject stage=doc.getJSONObject("Этап");
        assertEquals("stage-1",stage.getString("Идентификатор"));
        assertEquals("Принят",stage.getJSONArray("Действие").getJSONObject(0).getString("Название"));
        JSONObject att=stage.getJSONArray("Вложение").getJSONObject(0);
        assertEquals("att-1",att.getString("Идентификатор"));
        String b64=att.getJSONArray("Подпись").getJSONObject(0).getJSONObject("Файл").getString("ДвоичныеДанные");
        assertEquals("signature",new String(Base64.getDecoder().decode(b64),StandardCharsets.UTF_8));
    }

    @Test public void documentIdIsUsedWhenRevisionIsUnavailable() throws Exception {
        JSONObject root=SabyExecuteActionPayload.build(
            "doc-1","","stage-1","Выдан",
            Collections.singletonList(new SabyExecuteActionPayload.SignedAttachment("att",new byte[]{7})));
        JSONObject doc=root.getJSONObject("Документ");
        assertEquals("doc-1",doc.getString("Идентификатор"));
        assertFalse(doc.has("Редакция"));
    }

    @Test public void payloadDoesNotResendDocumentBodyOrCertificate() throws Exception {
        JSONObject root=SabyExecuteActionPayload.build(
            "doc-1","rev-1","stage-1","Принят",
            Collections.singletonList(new SabyExecuteActionPayload.SignedAttachment("att",new byte[]{1,2})));
        String wire=root.toString();
        assertFalse(wire.contains("Сертификат"));
        JSONObject att=root.getJSONObject("Документ").getJSONObject("Этап").getJSONArray("Вложение").getJSONObject(0);
        assertFalse(att.has("Файл"));
        assertTrue(att.has("Подпись"));
    }

    @Test public void multipleSignaturesStayOnTheirOwnAttachmentIds() throws Exception {
        JSONObject root=SabyExecuteActionPayload.build(
            "doc-1","rev-1","stage-1","Принят",
            Arrays.asList(
                new SabyExecuteActionPayload.SignedAttachment("a",new byte[]{1}),
                new SabyExecuteActionPayload.SignedAttachment("b",new byte[]{2})
            ));
        JSONArray a=root.getJSONObject("Документ").getJSONObject("Этап").getJSONArray("Вложение");
        assertEquals(2,a.length());
        assertEquals("a",a.getJSONObject(0).getString("Идентификатор"));
        assertEquals("b",a.getJSONObject(1).getString("Идентификатор"));
        assertEquals("AQ==",a.getJSONObject(0).getJSONArray("Подпись").getJSONObject(0).getJSONObject("Файл").getString("ДвоичныеДанные"));
        assertEquals("Ag==",a.getJSONObject(1).getJSONArray("Подпись").getJSONObject(0).getJSONObject("Файл").getString("ДвоичныеДанные"));
    }

    @Test public void duplicateAttachmentIsRejected() {
        assertThrows(IllegalArgumentException.class,()->SabyExecuteActionPayload.build(
            "d","r","s","a",Arrays.asList(
                new SabyExecuteActionPayload.SignedAttachment("x",new byte[]{1}),
                new SabyExecuteActionPayload.SignedAttachment("x",new byte[]{2})
            )));
    }

    @Test public void missingDocumentAndRevisionIsRejected() {
        assertThrows(IllegalArgumentException.class,()->SabyExecuteActionPayload.build(
            "","","s","a",Collections.singletonList(
                new SabyExecuteActionPayload.SignedAttachment("x",new byte[]{1}))));
    }

    @Test public void emptySignatureSetIsRejected() {
        assertThrows(IllegalArgumentException.class,()->SabyExecuteActionPayload.build(
            "d","r","s","a",Collections.emptyList()));
    }
}
