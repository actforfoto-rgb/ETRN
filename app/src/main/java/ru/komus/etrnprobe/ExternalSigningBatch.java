package ru.komus.etrnprobe;

import java.util.*;

/**
 * Pure no-network planner for an external signing service (for example Госключ via API ЕПГУ).
 * It does NOT implement or assume any external protocol. Correlation is explicit and deterministic.
 */
public final class ExternalSigningBatch {
    private ExternalSigningBatch() {}

    public static final class Item {
        public final String documentId;
        public final String attachmentId;
        public final String hash;
        public final String correlationId;

        public Item(String documentId, String attachmentId, String hash) {
            this.documentId = required(documentId);
            this.attachmentId = required(attachmentId);
            this.hash = required(hash);
            this.correlationId = correlation(documentId, attachmentId, hash);
        }

        public String key() { return documentId + "|" + attachmentId; }
    }

    public static final class SignedItem {
        public final String correlationId;
        public final String hash;
        public final byte[] signature;

        public SignedItem(String correlationId, String hash, byte[] signature) {
            this.correlationId = required(correlationId);
            this.hash = required(hash);
            if (signature == null || signature.length == 0) throw new IllegalArgumentException("EMPTY_SIGNATURE");
            this.signature = signature.clone();
        }

        public byte[] signature() { return signature.clone(); }
    }

    public static List<List<Item>> chunk(List<Item> items, int maxItemsPerPackage) {
        if (items == null || items.isEmpty()) throw new IllegalArgumentException("EMPTY_BATCH");
        if (maxItemsPerPackage < 1) throw new IllegalArgumentException("INVALID_PACKAGE_LIMIT");

        Set<String> keys = new HashSet<>();
        Set<String> correlations = new HashSet<>();
        for (Item item : items) {
            if (item == null) throw new IllegalArgumentException("NULL_ITEM");
            if (!keys.add(item.key())) throw new IllegalArgumentException("DUPLICATE_ATTACHMENT");
            if (!correlations.add(item.correlationId)) throw new IllegalArgumentException("DUPLICATE_CORRELATION");
        }

        List<List<Item>> packages = new ArrayList<>();
        for (int i = 0; i < items.size(); i += maxItemsPerPackage) {
            int end = Math.min(items.size(), i + maxItemsPerPackage);
            packages.add(Collections.unmodifiableList(new ArrayList<>(items.subList(i, end))));
        }
        return Collections.unmodifiableList(packages);
    }

    /**
     * Maps returned detached signatures to original Saby document+attachment.
     * Order is deliberately ignored. Any missing/extra/duplicate/hash mismatch blocks the batch.
     */
    public static Map<String, SignedItem> reconcile(List<Item> requested, List<SignedItem> returned) {
        if (requested == null || returned == null) throw new IllegalArgumentException("NULL_SET");
        Map<String, Item> expected = new LinkedHashMap<>();
        for (Item item : requested) {
            if (expected.put(item.correlationId, item) != null) throw new IllegalArgumentException("DUPLICATE_CORRELATION");
        }

        Map<String, SignedItem> matched = new LinkedHashMap<>();
        for (SignedItem sig : returned) {
            Item item = expected.get(sig.correlationId);
            if (item == null) throw new IllegalStateException("EXTRA_OR_FOREIGN_SIGNATURE");
            if (!item.hash.equals(sig.hash)) throw new IllegalStateException("SIGNED_FILE_HASH_MISMATCH");
            if (matched.put(sig.correlationId, sig) != null) throw new IllegalStateException("DUPLICATE_SIGNATURE");
        }
        if (matched.size() != expected.size()) throw new IllegalStateException("MISSING_SIGNATURE");

        Map<String, SignedItem> byAttachment = new LinkedHashMap<>();
        for (Item item : requested) byAttachment.put(item.key(), matched.get(item.correlationId));
        return Collections.unmodifiableMap(byAttachment);
    }

    private static String correlation(String documentId, String attachmentId, String hash) {
        // Not a cryptographic identifier and never used as proof. It is only an unambiguous local join key.
        return documentId.length() + ":" + documentId + "|" + attachmentId.length() + ":" + attachmentId + "|" + hash;
    }

    private static String required(String s) {
        if (s == null || s.trim().isEmpty()) throw new IllegalArgumentException("EMPTY_REQUIRED_VALUE");
        return s;
    }
}
