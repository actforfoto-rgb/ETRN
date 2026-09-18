package ru.komus.etrnprobe;

import java.util.*;

/**
 * Multi-package phone-only signing coordinator.
 *
 * Pure domain code: no Android, Saby or EPGU transport implementation.
 * Designed for Saby Prepare -> external Goskey packages -> Saby ExecuteAction.
 */
public final class PhoneBatchEngine {
    public enum Phase { NEW, PREPARING, READY, SUBMITTING, WAITING, APPLYING, COMPLETE, UNKNOWN, BLOCKED }
    public enum PackageStatus { NEW, SUBMIT_STARTED, WAITING, SIGNED, REFUSED, UNKNOWN }

    public static final class Document {
        public final String id, revision, stage, action, principal;
        public Document(String id,String revision,String stage,String action,String principal){
            this.id=req(id);this.revision=clean(revision);this.stage=req(stage);this.action=req(action);this.principal=req(principal);
        }
        String key(){return id+"|"+revision+"|"+stage+"|"+action+"|"+principal;}
    }

    public static final class PreparedFile {
        public final String document, attachment, fileName, url, hash;
        public PreparedFile(String document,String attachment,String fileName,String url,String hash){
            this.document=req(document);this.attachment=req(attachment);this.fileName=req(fileName);
            this.url=req(url);this.hash=req(hash);
        }
        String key(){return document+"|"+attachment;}
        ExternalSigningBatch.Item signingItem(){return new ExternalSigningBatch.Item(document,attachment,hash);}
    }

    public static final class Signature {
        public final String document, attachment, hash, signer;
        private final byte[] bytes;
        public Signature(String document,String attachment,String hash,String signer,byte[] bytes){
            this.document=req(document);this.attachment=req(attachment);this.hash=req(hash);this.signer=req(signer);
            if(bytes==null||bytes.length==0)throw new IllegalArgumentException("EMPTY_SIGNATURE");
            this.bytes=bytes.clone();
        }
        public byte[] bytes(){return bytes.clone();}
        String key(){return document+"|"+attachment;}
    }

    public static final class PackagePoll {
        public final PackageStatus status;
        public final List<Signature> signatures;
        public PackagePoll(PackageStatus status,List<Signature> signatures){
            this.status=Objects.requireNonNull(status);
            this.signatures=Collections.unmodifiableList(new ArrayList<>(signatures==null?Collections.emptyList():signatures));
        }
    }

    public interface Gateway {
        String principal();
        List<PreparedFile> prepare(Document document) throws Exception;
        String submitPackage(GoskeyIpPackagePlan.Recipient recipient,List<PreparedFile> files) throws Exception;
        PackagePoll pollPackage(String operationId) throws Exception;
        boolean documentUnchanged(Document document,List<PreparedFile> files) throws Exception;
        void execute(Document document,List<Signature> signatures) throws Exception;
        boolean confirmed(Document document,List<PreparedFile> files) throws Exception;
    }

    public interface SignatureVerifier {
        boolean verify(PreparedFile file,Signature signature,String principal) throws Exception;
    }

    public interface Store {
        Snapshot load() throws Exception;
        void save(Snapshot snapshot) throws Exception; // atomic + durable before return
    }

    public static final class PackageState {
        public int index;
        public PackageStatus status=PackageStatus.NEW;
        public String operationId="";
        public final List<String> fileKeys=new ArrayList<>();
        PackageState copy(){
            PackageState x=new PackageState();x.index=index;x.status=status;x.operationId=operationId;x.fileKeys.addAll(fileKeys);return x;
        }
    }

    public static final class Snapshot {
        public Phase phase=Phase.NEW;
        public String plan="",reason="";
        public final List<PreparedFile> prepared=new ArrayList<>();
        public final List<PackageState> packages=new ArrayList<>();
        public final Map<String,Signature> signatures=new LinkedHashMap<>();
        public final Set<String> applying=new LinkedHashSet<>();
        public final Set<String> confirmed=new LinkedHashSet<>();
        Snapshot copy(){
            Snapshot x=new Snapshot();x.phase=phase;x.plan=plan;x.reason=reason;x.prepared.addAll(prepared);
            for(PackageState p:packages)x.packages.add(p.copy());
            x.signatures.putAll(signatures);x.applying.addAll(applying);x.confirmed.addAll(confirmed);return x;
        }
    }

    private final List<Document> documents;
    private final GoskeyIpPackagePlan.Recipient recipient;
    private final Gateway gateway;
    private final SignatureVerifier verifier;
    private final Store store;
    private Snapshot state;

    public PhoneBatchEngine(List<Document> documents,GoskeyIpPackagePlan.Recipient recipient,
                            Gateway gateway,SignatureVerifier verifier,Store store)throws Exception{
        if(documents==null||documents.size()<2||documents.size()>50)throw new IllegalArgumentException("BATCH_SIZE_2_TO_50");
        this.documents=Collections.unmodifiableList(new ArrayList<>(documents));
        this.recipient=Objects.requireNonNull(recipient);
        this.gateway=Objects.requireNonNull(gateway);this.verifier=Objects.requireNonNull(verifier);this.store=Objects.requireNonNull(store);
        Set<String> ids=new HashSet<>();List<String> keys=new ArrayList<>();String principal=documents.get(0).principal;
        for(Document d:documents){
            if(!ids.add(d.id))throw new IllegalArgumentException("DUPLICATE_DOCUMENT");
            if(!principal.equals(d.principal))throw new IllegalArgumentException("MIXED_PRINCIPALS");
            keys.add(d.key());
        }
        Collections.sort(keys);String plan=String.join("\n",keys);
        Snapshot saved=store.load();state=saved==null?new Snapshot():saved.copy();
        if(!state.plan.isEmpty()&&!state.plan.equals(plan))throw new IllegalStateException("PLAN_CHANGED");
        state.plan=plan;
    }

    public synchronized Snapshot snapshot(){return state.copy();}
    private void save()throws Exception{store.save(state.copy());}
    private void principal(){if(!documents.get(0).principal.equals(gateway.principal()))throw new IllegalStateException("ACCOUNT_CHANGED");}
    private void block(String reason)throws Exception{state.phase=Phase.BLOCKED;state.reason=reason;save();}
    private void uncertain(String reason)throws Exception{state.phase=Phase.UNKNOWN;state.reason=reason;save();}

    public synchronized void prepare(boolean consent)throws Exception{
        if(!consent)throw new IllegalStateException("CONSENT_REQUIRED");
        if(state.phase!=Phase.NEW)throw new IllegalStateException("PREPARE_NOT_REPEATABLE");
        principal();state.phase=Phase.PREPARING;save();
        try{
            Set<String> attachmentIds=new HashSet<>();
            for(Document d:documents){
                principal();List<PreparedFile> files=gateway.prepare(d);
                if(files==null||files.isEmpty()){block("NO_SIGNABLE_FILES");return;}
                for(PreparedFile f:files){
                    if(!d.id.equals(f.document)||!attachmentIds.add(f.attachment)){block("ATTACHMENT_OWNERSHIP_OR_DUPLICATE");return;}
                    state.prepared.add(f);
                }
                save();
            }
            List<ExternalSigningBatch.Item> items=new ArrayList<>();
            for(PreparedFile f:state.prepared)items.add(f.signingItem());
            List<GoskeyIpPackagePlan.Package> plan=GoskeyIpPackagePlan.plan(recipient,items);
            Map<String,PreparedFile> byKey=filesByKey();
            for(GoskeyIpPackagePlan.Package part:plan){
                PackageState ps=new PackageState();ps.index=part.index;
                for(ExternalSigningBatch.Item i:part.documents){
                    String key=i.key();
                    if(!byKey.containsKey(key)){block("PACKAGE_FILE_MAPPING_ERROR");return;}
                    ps.fileKeys.add(key);
                }
                state.packages.add(ps);
            }
            state.phase=Phase.READY;save();
        }catch(Exception e){uncertain("PREPARE_OUTCOME_UNKNOWN");throw e;}
    }

    /**
     * Submits all not-yet-submitted packages sequentially.
     * Before every external side effect SUBMIT_STARTED is durably stored.
     * If the submit result is lost, that package becomes UNKNOWN and is never blindly retried.
     */
    public synchronized void submit(boolean consent)throws Exception{
        if(!consent)throw new IllegalStateException("CONSENT_REQUIRED");
        if(state.phase!=Phase.READY&&state.phase!=Phase.SUBMITTING)throw new IllegalStateException("SUBMIT_NOT_ALLOWED");
        principal();
        for(Document d:documents)if(!gateway.documentUnchanged(d,files(d))){block("DOCUMENT_CHANGED_BEFORE_SIGNING");return;}
        state.phase=Phase.SUBMITTING;save();
        Map<String,PreparedFile> byKey=filesByKey();
        for(PackageState p:state.packages){
            if(p.status==PackageStatus.WAITING||p.status==PackageStatus.SIGNED)continue;
            if(p.status==PackageStatus.SUBMIT_STARTED||p.status==PackageStatus.UNKNOWN){
                uncertain("PACKAGE_SUBMIT_RECONCILIATION_REQUIRED");return;
            }
            p.status=PackageStatus.SUBMIT_STARTED;save();
            List<PreparedFile> payload=new ArrayList<>();for(String k:p.fileKeys)payload.add(byKey.get(k));
            try{
                p.operationId=req(gateway.submitPackage(recipient,Collections.unmodifiableList(payload)));
                p.status=PackageStatus.WAITING;save();
            }catch(Exception e){
                p.status=PackageStatus.UNKNOWN;uncertain("PACKAGE_SUBMIT_OUTCOME_UNKNOWN");throw e;
            }
        }
        state.phase=Phase.WAITING;save();
    }

    public synchronized void pollAndApply()throws Exception{
        if(state.phase!=Phase.WAITING&&state.phase!=Phase.APPLYING)throw new IllegalStateException("NO_ACTIVE_SIGNING");
        principal();
        Map<String,PreparedFile> byKey=filesByKey();
        for(PackageState p:state.packages){
            if(p.status==PackageStatus.SIGNED)continue;
            if(p.status!=PackageStatus.WAITING){uncertain("PACKAGE_NOT_POLLABLE");return;}
            PackagePoll poll=gateway.pollPackage(req(p.operationId));
            if(poll.status==PackageStatus.WAITING)return;
            if(poll.status==PackageStatus.REFUSED){block("SIGNING_REFUSED");return;}
            if(poll.status!=PackageStatus.SIGNED){uncertain("UNRECOGNIZED_SIGNING_STATUS");return;}
            Set<String> expected=new HashSet<>(p.fileKeys);Set<String> received=new HashSet<>();
            for(Signature s:poll.signatures){
                if(!expected.contains(s.key())||!received.add(s.key())){block("EXTRA_FOREIGN_OR_DUPLICATE_SIGNATURE");return;}
                PreparedFile f=byKey.get(s.key());
                if(f==null||!f.hash.equals(s.hash)||!documents.get(0).principal.equals(s.signer)
                        ||!verifier.verify(f,s,documents.get(0).principal)){
                    block("SIGNATURE_VERIFICATION_FAILED");return;
                }
                state.signatures.put(s.key(),s);
            }
            if(!received.equals(expected)){block("INCOMPLETE_SIGNATURE_PACKAGE");return;}
            p.status=PackageStatus.SIGNED;save();
        }
        if(state.signatures.size()!=state.prepared.size()){block("INCOMPLETE_SIGNATURE_SET");return;}

        state.phase=Phase.APPLYING;save();
        for(Document d:documents){
            principal();
            if(state.confirmed.contains(d.id))continue;
            if(state.applying.contains(d.id)){
                if(gateway.confirmed(d,files(d))){state.confirmed.add(d.id);save();continue;}
                uncertain("EXECUTE_RECONCILIATION_REQUIRED");return;
            }
            if(!gateway.documentUnchanged(d,files(d))){block("DOCUMENT_CHANGED_BEFORE_EXECUTE");return;}
            List<Signature> sigs=new ArrayList<>();
            for(PreparedFile f:files(d)){
                Signature s=state.signatures.get(f.key());if(s==null){block("MISSING_SIGNATURE_FOR_DOCUMENT");return;}sigs.add(s);
            }
            state.applying.add(d.id);save();
            try{gateway.execute(d,Collections.unmodifiableList(sigs));}
            catch(Exception e){uncertain("EXECUTE_OUTCOME_UNKNOWN");throw e;}
            if(!gateway.confirmed(d,files(d))){uncertain("READBACK_NOT_CONFIRMED");return;}
            state.confirmed.add(d.id);save();
        }
        state.phase=Phase.COMPLETE;state.reason="";save();
    }

    /** Only resolves already-attempted ExecuteAction calls. It never resubmits Госключ jobs. */
    public synchronized void reconcileExecute()throws Exception{
        principal();
        if(state.phase!=Phase.UNKNOWN&&state.phase!=Phase.APPLYING)throw new IllegalStateException("NO_RECONCILIATION");
        if(state.applying.isEmpty())throw new IllegalStateException("NO_EXECUTE_TO_RECONCILE");
        for(Document d:documents)if(state.applying.contains(d.id)&&!state.confirmed.contains(d.id)){
            if(!gateway.confirmed(d,files(d))){uncertain("EXECUTE_RECONCILIATION_REQUIRED");return;}
            state.confirmed.add(d.id);save();
        }
        state.reason="";
        state.phase=state.confirmed.size()==documents.size()?Phase.COMPLETE:Phase.APPLYING;save();
    }

    private Map<String,PreparedFile> filesByKey(){
        Map<String,PreparedFile> m=new LinkedHashMap<>();for(PreparedFile f:state.prepared)m.put(f.key(),f);return m;
    }
    private List<PreparedFile> files(Document d){
        List<PreparedFile> x=new ArrayList<>();for(PreparedFile f:state.prepared)if(f.document.equals(d.id))x.add(f);
        return Collections.unmodifiableList(x);
    }
    private static String clean(String s){return s==null?"":s.trim();}
    private static String req(String s){String v=clean(s);if(v.isEmpty())throw new IllegalArgumentException("EMPTY_REQUIRED_VALUE");return v;}
}
