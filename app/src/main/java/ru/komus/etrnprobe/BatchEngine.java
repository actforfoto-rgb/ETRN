package ru.komus.etrnprobe;

import java.util.*;

/** Domain coordinator. Gateway is NOT a Saby protocol implementation.
 * The lab uses a synthetic gateway. Production signature verification and the
 * cross-document Saby adapter remain release blockers, never inferred here.
 */
public final class BatchEngine {
    public enum Phase { NEW, PREPARING, READY, CREATE_STARTED, WAITING, APPLYING, COMPLETE, UNKNOWN, BLOCKED }
    public enum Status { WAITING, SIGNED, REFUSED, UNKNOWN }
    public static final class Document {
        public final String id, revision, stage, action, principal;
        public Document(String id, String revision, String stage, String action, String principal) {
            this.id=required(id); this.revision=required(revision); this.stage=required(stage);
            this.action=required(action); this.principal=required(principal);
        }
        String key() { return id+"|"+revision+"|"+stage+"|"+action+"|"+principal; }
    }
    public static final class FileRef {
        public final String document, attachment, hash;
        public FileRef(String document, String attachment, String hash) {
            this.document=required(document); this.attachment=required(attachment); this.hash=required(hash);
        }
        String key() { return document+"|"+attachment; }
    }
    public static final class Signature {
        public final String document, attachment, hash, signer;
        private final byte[] bytes;
        public Signature(String document, String attachment, String hash, String signer, byte[] bytes) {
            this.document=required(document); this.attachment=required(attachment); this.hash=required(hash);
            this.signer=required(signer);
            if(bytes==null||bytes.length==0) throw new IllegalArgumentException("EMPTY_SIGNATURE");
            this.bytes=bytes.clone();
        }
        public byte[] bytes() { return bytes.clone(); }
        String key() { return document+"|"+attachment; }
    }
    public static final class Poll {
        public final Status status;
        public final List<Signature> signatures;
        public Poll(Status status, List<Signature> signatures) {
            this.status=Objects.requireNonNull(status);
            this.signatures=Collections.unmodifiableList(new ArrayList<>(signatures));
        }
    }
    public interface Gateway {
        String principal();
        boolean hasVerifiedCrossDocumentContract();
        List<FileRef> prepare(Document document) throws Exception;
        String create(List<FileRef> files) throws Exception;
        Poll poll(String operation) throws Exception;
        boolean unchanged(Document document, List<FileRef> files) throws Exception;
        void apply(Document document, List<Signature> signatures) throws Exception;
        boolean confirmed(Document document, List<FileRef> files) throws Exception;
    }
    public interface SignatureVerifier {
        // Must verify detached signature, exact file hash, identity and trust in production.
        boolean verify(FileRef file, Signature signature, String principal) throws Exception;
    }
    public interface Store {
        Snapshot load() throws Exception;
        // Must durably save atomically BEFORE returning. No credentials in Snapshot.
        void save(Snapshot snapshot) throws Exception;
    }
    public static final class Snapshot {
        public Phase phase=Phase.NEW;
        public String operation="", reason="", plan="";
        public final List<FileRef> prepared=new ArrayList<>();
        public final Set<String> applying=new LinkedHashSet<>(), confirmed=new LinkedHashSet<>();
        public Snapshot copy() {
            Snapshot x=new Snapshot();x.phase=phase;x.operation=operation;x.reason=reason;x.plan=plan;
            x.prepared.addAll(prepared);x.applying.addAll(applying);x.confirmed.addAll(confirmed);return x;
        }
    }
    private final List<Document> documents;
    private final Gateway gateway;
    private final SignatureVerifier verifier;
    private final Store store;
    private Snapshot state;
    public BatchEngine(List<Document> documents, Gateway gateway, SignatureVerifier verifier, Store store) throws Exception {
        if(documents==null||documents.size()<2||documents.size()>50) throw new IllegalArgumentException("BATCH_SIZE_2_TO_50");
        this.documents=Collections.unmodifiableList(new ArrayList<>(documents));
        this.gateway=Objects.requireNonNull(gateway);this.verifier=Objects.requireNonNull(verifier);this.store=Objects.requireNonNull(store);
        Set<String> ids=new HashSet<>();String principal=documents.get(0).principal,action=documents.get(0).action;
        List<String> keys=new ArrayList<>();
        for(Document d:documents) {
            if(!ids.add(d.id)) throw new IllegalArgumentException("DUPLICATE_DOCUMENT");
            if(!principal.equals(d.principal)) throw new IllegalArgumentException("MIXED_PRINCIPALS");
            if(!action.equals(d.action)) throw new IllegalArgumentException("MIXED_ACTIONS");
            keys.add(d.key());
        }
        Collections.sort(keys);String plan=String.join("\n",keys);
        Snapshot saved=store.load();state=saved==null?new Snapshot():saved.copy();
        if(!state.plan.isEmpty()&&!state.plan.equals(plan)) throw new IllegalStateException("PLAN_CHANGED");
        state.plan=plan;
    }
    public synchronized Snapshot snapshot() { return state.copy(); }
    private void save() throws Exception { store.save(state.copy()); }
    private void principal() {
        if(!documents.get(0).principal.equals(gateway.principal())) throw new IllegalStateException("ACCOUNT_CHANGED");
    }
    private void block(String reason) throws Exception { state.phase=Phase.BLOCKED;state.reason=reason;save(); }
    private void uncertain(String reason) throws Exception { state.phase=Phase.UNKNOWN;state.reason=reason;save(); }
    public synchronized void prepare(boolean consent) throws Exception {
        if(!consent) throw new IllegalStateException("CONSENT_REQUIRED");
        if(state.phase!=Phase.NEW) throw new IllegalStateException("PREPARE_NOT_REPEATABLE");
        principal();
        state.phase=Phase.PREPARING;save();
        try {
            Set<String> attachmentIds=new HashSet<>();
            for(Document d:documents) {
                principal();List<FileRef> refs=gateway.prepare(d);
                if(refs==null||refs.isEmpty()) { block("NO_SIGNABLE_FILES");return; }
                for(FileRef f:refs) {
                    if(!d.id.equals(f.document)||!attachmentIds.add(f.attachment)) { block("ATTACHMENT_OWNERSHIP_OR_DUPLICATE");return; }
                    state.prepared.add(f);
                }
                save();
            }
            state.phase=Phase.READY;save();
        } catch(Exception e) { uncertain("PREPARE_OUTCOME_UNKNOWN");throw e; }
    }
    public synchronized void submit(boolean consent) throws Exception {
        if(!consent) throw new IllegalStateException("CONSENT_REQUIRED");
        if(state.phase!=Phase.READY) throw new IllegalStateException("CREATE_NOT_REPEATABLE");
        principal();
        if(!gateway.hasVerifiedCrossDocumentContract()) { block("CROSS_DOCUMENT_CONTRACT_UNVERIFIED");return; }
        for(Document d:documents) {
            if(!gateway.unchanged(d,files(d))) { block("DOCUMENT_CHANGED_BEFORE_SIGNING");return; }
        }
        state.phase=Phase.CREATE_STARTED;save();
        try {
            state.operation=required(gateway.create(Collections.unmodifiableList(state.prepared)));
            state.phase=Phase.WAITING;save();
        } catch(Exception e) { uncertain("CREATE_OUTCOME_UNKNOWN");throw e; }
    }
    /** Reads status; no duplicate Create. Revalidates signatures on every resume. */
    public synchronized void continueAfterSigning() throws Exception {
        if(state.phase!=Phase.WAITING&&state.phase!=Phase.APPLYING) throw new IllegalStateException("NO_ACTIVE_OPERATION");
        principal();
        Poll poll=gateway.poll(required(state.operation));
        if(poll.status==Status.WAITING) return;
        if(poll.status!=Status.SIGNED) { block(poll.status==Status.REFUSED?"SIGNING_REFUSED":"UNRECOGNIZED_SIGNING_STATUS");return; }
        Map<String,Signature> signatures=new LinkedHashMap<>();
        for(Signature s:poll.signatures) {
            if(signatures.put(s.key(),s)!=null) { block("DUPLICATE_SIGNATURE");return; }
        }
        if(signatures.size()!=state.prepared.size()) { block("INCOMPLETE_OR_EXTRA_SIGNATURES");return; }
        for(FileRef f:state.prepared) {
            Signature s=signatures.get(f.key());
            if(s==null||!f.hash.equals(s.hash)||!documents.get(0).principal.equals(s.signer)||!verifier.verify(f,s,documents.get(0).principal)) {
                block("SIGNATURE_VERIFICATION_FAILED");return;
            }
        }
        for(Document d:documents) {
            principal();
            if(state.confirmed.contains(d.id)) continue;
            if(state.applying.contains(d.id)) {
                if(gateway.confirmed(d,files(d))) { state.confirmed.add(d.id);save();continue; }
                uncertain("APPLY_RECONCILIATION_REQUIRED");return;
            }
            if(!gateway.unchanged(d,files(d))) { block("DOCUMENT_CHANGED_BEFORE_APPLY");return; }
            List<Signature> one=new ArrayList<>();for(FileRef f:files(d)) one.add(signatures.get(f.key()));
            state.phase=Phase.APPLYING;state.applying.add(d.id);save();
            try { gateway.apply(d,Collections.unmodifiableList(one)); }
            catch(Exception e) { uncertain("APPLY_OUTCOME_UNKNOWN");throw e; }
            if(!gateway.confirmed(d,files(d))) { uncertain("READBACK_NOT_CONFIRMED");return; }
            state.confirmed.add(d.id);save();
        }
        state.phase=Phase.COMPLETE;save();
    }
    /** Recovery does not sign or finalize. It only resolves already-attempted applies. */
    public synchronized void reconcile() throws Exception {
        principal();
        if(state.phase!=Phase.UNKNOWN&&state.phase!=Phase.APPLYING) throw new IllegalStateException("NO_RECONCILIATION");
        if(state.applying.isEmpty()) throw new IllegalStateException("NO_KNOWN_APPLY_TO_RECONCILE");
        for(Document d:documents) if(state.applying.contains(d.id)&&!state.confirmed.contains(d.id)) {
            if(!gateway.confirmed(d,files(d))) { uncertain("APPLY_RECONCILIATION_REQUIRED");return; }
            state.confirmed.add(d.id);save();
        }
        state.reason="";state.phase=state.confirmed.size()==documents.size()?Phase.COMPLETE:Phase.APPLYING;save();
    }
    private List<FileRef> files(Document d) {
        List<FileRef> out=new ArrayList<>();for(FileRef f:state.prepared) if(f.document.equals(d.id)) out.add(f);
        return Collections.unmodifiableList(out);
    }
    private static String required(String s) {
        if(s==null||s.trim().isEmpty()) throw new IllegalArgumentException("EMPTY_REQUIRED_VALUE");return s;
    }
}
