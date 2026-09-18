package ru.komus.etrnprobe;
import org.junit.Test;
import static org.junit.Assert.*;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.Arrays;

/** Synthetic fixtures only; these are not real Saby responses or real identities. */
public class SignerSupportTest {
    private JSONObject profile() throws Exception {
        return new JSONObject().put("Фамилия", "Тестов").put("Имя", "Тест").put("Отчество", "Тестович");
    }
    private JSONObject own() throws Exception {
        return profile().put("ИНН", "111111111111").put("ОГРНИП", "111111111111111");
    }
    private JSONObject complete() throws Exception {
        return new JSONObject().put("ФИО", "Тестов Тест Тестович").put("ИНН", "111111111111")
                .put("ОГРНИП", "111111111111111").put("Должность", "Индивидуальный предприниматель");
    }
    @Test public void incompleteCertificateIsBlocked() {
        assertThrows(IllegalArgumentException.class, () -> SignerSupport.requireComplete(new JSONObject()));
    }
    @Test public void missingFioAndInnReproduceLiveFailureLocally() throws Exception {
        JSONObject bad = complete(); bad.remove("ФИО"); bad.remove("ИНН");
        assertThrows(IllegalArgumentException.class, () -> SignerSupport.requireComplete(bad));
    }
    @Test public void completeRequisitesPass() throws Exception {
        SignerSupport.validate(complete(), profile(), own(), own());
        SignerSupport.requireComplete(complete());
    }
    @Test public void ownOrganizationFillsMissingActionCertificate() throws Exception {
        JSONObject got = SignerSupport.suggest(profile(), own(), null, null);
        assertEquals("Тестов Тест Тестович", got.getString("ФИО"));
        assertEquals("111111111111", got.getString("ИНН"));
        assertEquals("111111111111111", got.getString("ОГРНИП"));
    }
    @Test public void acceptsCertificateAsObject() throws Exception {
        JSONObject got = SignerSupport.suggest(profile(), new JSONObject(), complete(), null);
        assertEquals("111111111111", got.getString("ИНН"));
    }
    @Test public void acceptsCertificateAsArray() throws Exception {
        JSONObject got = SignerSupport.suggest(profile(), new JSONObject(), new JSONArray().put(complete()), null);
        assertEquals("111111111111111", got.getString("ОГРНИП"));
    }
    @Test public void readsDocumentCertificateList() throws Exception {
        JSONObject got = SignerSupport.suggest(profile(), new JSONObject(), null, new JSONArray().put(complete()));
        assertEquals("111111111111", got.getString("ИНН"));
    }
    @Test public void doesNotChooseFirstOtherPerson() throws Exception {
        JSONObject other = complete().put("ФИО", "Другой Человек").put("ИНН", "222222222222");
        JSONObject got = SignerSupport.suggest(profile(), new JSONObject(), new JSONArray().put(other).put(complete()), null);
        assertEquals("111111111111", got.getString("ИНН"));
    }
    @Test public void ambiguousOwnersAreNotAutoSelected() throws Exception {
        JSONObject second = complete().put("ИНН", "222222222222");
        JSONObject got = SignerSupport.suggest(profile(), new JSONObject(), new JSONArray().put(complete()).put(second), null);
        assertEquals("", SignerSupport.str(got, "ИНН"));
    }
    @Test public void conflictingOgrnipIsNotGuessed() throws Exception {
        JSONObject second = complete().put("ОГРНИП", "222222222222222");
        JSONObject got = SignerSupport.suggest(profile(), own(), new JSONArray().put(complete()).put(second), null);
        assertEquals("", SignerSupport.str(got, "ОГРНИП"));
    }
    @Test public void neverReadsCounterpartyAsOurOrganization() throws Exception {
        JSONObject doc = new JSONObject().put("Контрагент", new JSONObject().put("СвФЛ", own()));
        assertEquals(0, SignerSupport.ownFl(doc).length());
    }
    @Test public void rejectsDifferentOwnersAcrossTwoDocuments() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> SignerSupport.validate(complete(), profile(), own(), own().put("ИНН", "222222222222")));
    }
    @Test public void rejectsDifferentAuthenticatedUser() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> SignerSupport.validate(complete(), profile().put("Фамилия", "Другой"), own(), own()));
    }
    @Test public void rejectsBadInnLength() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> SignerSupport.validate(complete().put("ИНН", "1111111111"), profile(), own(), own()));
    }
    @Test public void rejectsMissingOgrnip() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> SignerSupport.validate(complete().put("ОГРНИП", ""), profile(), own(), own()));
    }
    @Test public void mergeRejectsConflictingInn() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> SignerSupport.merge(complete(), complete().put("ИНН", "222222222222")));
    }
    @Test public void nameComponentsPreserveCompoundSurname() throws Exception {
        assertEquals("Два_Слова Тест Тестович", SignerSupport.name(profile().put("Фамилия", "Два Слова")));
    }
    @Test public void operationIdIsNotAnyNestedString() throws Exception {
        assertEquals("", SignerSupport.operationId(new JSONObject().put("message", "just-some-string")));
        assertEquals("", SignerSupport.operationId(new JSONObject().put("data", "abcd1234-abcd-1234-abcd-abcd12345678")));
    }
    @Test public void knownOperationIdIsAccepted() throws Exception {
        String id = "abcd1234-abcd-1234-abcd-abcd12345678";
        assertEquals(id, SignerSupport.operationId(new JSONObject().put("OperationID", id)));
        assertEquals(id, SignerSupport.operationId(id));
        assertEquals("", SignerSupport.operationId("OK"));
    }
    @Test public void secretsAndPayloadsAreRedacted() throws Exception {
        JSONObject raw = new JSONObject().put("SessionID", "private-session").put("Сертификат", complete())
                .put("result", new JSONObject().put("Файл", new JSONObject().put("ДвоичныеДанные", "a-private-file")))
                .put("error", new JSONObject().put("details", "password=my-secret at https://example.test/token"));
        String safe = SignerSupport.safeJson(raw, Arrays.asList("my-secret")).toString();
        assertFalse(safe.contains("private-session")); assertFalse(safe.contains("Тестов"));
        assertFalse(safe.contains("111111111111")); assertFalse(safe.contains("a-private-file"));
        assertFalse(safe.contains("my-secret")); assertFalse(safe.contains("https://"));
    }
    @Test public void diagnosticErrorMeaningIsRetained() throws Exception {
        JSONObject error = new JSONObject().put("error", new JSONObject().put("code", -32000)
                .put("details", "Отсутствуют значения обязательных полей: 'Сертификат.ФИО', 'Сертификат.ИНН'"));
        String safe = SignerSupport.safeJson(error, Arrays.asList("private-token")).toString();
        assertTrue(safe.contains("Сертификат.ФИО")); assertTrue(safe.contains("-32000"));
    }
    @Test public void userParserDoesNotFindRandomNestedName() throws Exception {
        JSONObject resp = new JSONObject().put("result", new JSONObject().put("Контрагент", profile()));
        assertEquals(0, SignerSupport.user(resp).length());
    }
}
