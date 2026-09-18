package ru.komus.etrnprobe;

import org.json.JSONArray;
import org.json.JSONObject;
import java.util.*;
import java.util.Base64;

/** Pure builder for supplying already-created detached signatures to СБИС.ВыполнитьДействие. */
public final class SabyExecuteActionPayload {
    private SabyExecuteActionPayload() {}

    public static final class SignedAttachment {
        public final String attachmentId;
        private final byte[] signature;

        public SignedAttachment(String attachmentId, byte[] signature) {
            this.attachmentId = required(attachmentId);
            if (signature == null || signature.length == 0) throw new IllegalArgumentException("EMPTY_SIGNATURE");
            this.signature = signature.clone();
        }

        byte[] signature() { return signature.clone(); }
    }

    /**
     * Builds only identifiers + detached signatures. It intentionally does not
     * resend the signed document bytes or certificate fields.
     */
    public static JSONObject build(
            String documentId,
            String revisionId,
            String stageId,
            String actionName,
            List<SignedAttachment> attachments) throws Exception {

        if ((documentId == null || documentId.trim().isEmpty())
                && (revisionId == null || revisionId.trim().isEmpty())) {
            throw new IllegalArgumentException("DOCUMENT_OR_REVISION_REQUIRED");
        }
        required(stageId);
        required(actionName);
        if (attachments == null || attachments.isEmpty()) throw new IllegalArgumentException("NO_SIGNATURES");

        Set<String> seen = new HashSet<>();
        JSONArray signedAttachments = new JSONArray();
        for (SignedAttachment item : attachments) {
            if (item == null) throw new IllegalArgumentException("NULL_SIGNATURE");
            if (!seen.add(item.attachmentId)) throw new IllegalArgumentException("DUPLICATE_ATTACHMENT");
            JSONObject file = new JSONObject()
                    .put("ДвоичныеДанные", Base64.getEncoder().encodeToString(item.signature()));
            JSONObject signature = new JSONObject().put("Файл", file);
            signedAttachments.put(new JSONObject()
                    .put("Идентификатор", item.attachmentId)
                    .put("Подпись", new JSONArray().put(signature)));
        }

        JSONObject stage = new JSONObject()
                .put("Идентификатор", stageId)
                .put("Действие", new JSONArray().put(new JSONObject().put("Название", actionName)))
                .put("Вложение", signedAttachments);

        JSONObject document = new JSONObject().put("Этап", stage);
        if (revisionId != null && !revisionId.trim().isEmpty()) {
            document.put("Редакция", new JSONObject().put("Идентификатор", revisionId));
        } else {
            document.put("Идентификатор", documentId);
        }
        return new JSONObject().put("Документ", document);
    }

    private static String required(String s) {
        if (s == null || s.trim().isEmpty()) throw new IllegalArgumentException("EMPTY_REQUIRED_VALUE");
        return s;
    }
}
