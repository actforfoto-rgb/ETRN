package ru.komus.etrnprobe;

import org.json.JSONObject;
import java.util.*;

/**
 * Server-side orchestration core for one direct EPGU/Goskey 60025907 request.
 *
 * This class is intentionally transport- and key-provider-neutral. A protected backend
 * supplies the ESIA access token, initiator CAdES signer and HTTP adapter.
 */
public final class GoskeyDirectBridgeCore {
    public enum Phase { NEW, SUBMIT_STARTED, WAITING, COMPLETE, REFUSED, UNKNOWN, BLOCKED }
    public enum RemoteStatus { WAITING, SIGNED, REFUSED, ERROR, UNKNOWN }

    public interface TokenProvider {
        String bearerToken() throws Exception;
    }

    public interface Epgu {
        long pushDirect(String bearer, JSONObject meta, byte[] signedZip) throws Exception;
        RemoteResult poll(String bearer,long orderId) throws Exception;
    }

    public interface Store {
        Snapshot load() throws Exception;
        void save(Snapshot state) throws Exception;
    }

    public static final class RemoteResult {
        public final RemoteStatus status;
        /** Key is original business document mnemonic; value is detached Goskey signature bytes. */
        public final Map<String,byte[]> signaturesByMnemonic;
        public final String reason;

        public RemoteResult(RemoteStatus status,Map<String,byte[]> signaturesByMnemonic,String reason){
            this.status=Objects.requireNonNull(status);
            Map<String,byte[]> copy=new LinkedHashMap<>();
            if(signaturesByMnemonic!=null)for(Map.Entry<String,byte[]> e:signaturesByMnemonic.entrySet()){
                String k=required(e.getKey());
                byte[] v=e.getValue();
                if(v==null||v.length==0)throw new IllegalArgumentException("EMPTY_RETURNED_SIGNATURE");
                if(copy.put(k,v.clone())!=null)throw new IllegalArgumentException("DUPLICATE_RETURNED_MNEMONIC");
            }
            this.signaturesByMnemonic=Collections.unmodifiableMap(copy);
            this.reason=reason==null?"":reason;
        }

        public byte[] signature(String mnemonic){
            byte[] b=signaturesByMnemonic.get(mnemonic);
            return b==null?null:b.clone();
        }
    }

    public static final class Snapshot {
        public Phase phase=Phase.NEW;
        public long orderId;
        public String reason="";
        public final Map<String,String> mnemonicToCorrelation=new LinkedHashMap<>();
        public final Map<String,byte[]> signaturesByCorrelation=new LinkedHashMap<>();

        Snapshot copy(){
            Snapshot x=new Snapshot();x.phase=phase;x.orderId=orderId;x.reason=reason;
            x.mnemonicToCorrelation.putAll(mnemonicToCorrelation);
            for(Map.Entry<String,byte[]> e:signaturesByCorrelation.entrySet())
                x.signaturesByCorrelation.put(e.getKey(),e.getValue().clone());
            return x;
        }
    }

    private final TokenProvider tokens;
    private final Epgu epgu;
    private final Store store;
    private Snapshot state;

    public GoskeyDirectBridgeCore(TokenProvider tokens,Epgu epgu,Store store)throws Exception{
        this.tokens=Objects.requireNonNull(tokens);this.epgu=Objects.requireNonNull(epgu);this.store=Objects.requireNonNull(store);
        Snapshot saved=store.load();state=saved==null?new Snapshot():saved.copy();
    }

    public synchronized Snapshot snapshot(){return state.copy();}

    /**
     * Builds and signs the complete EPGU archive locally first. Therefore archive-size and
     * contract errors are known before SUBMIT_STARTED is persisted.
     */
    public synchronized void submit(
            Epgu60025907TransportContract.Meta meta,
            Goskey60025907Contract.Request request,
            java.time.OffsetDateTime nowMoscow,
            List<Goskey60025907Contract.BusinessFile> files,
            Goskey60025907Contract.CadesSigner signer) throws Exception {

        if(state.phase!=Phase.NEW)throw new IllegalStateException("SUBMIT_NOT_REPEATABLE");
        Goskey60025907Contract.SignedArchive archive=
                Goskey60025907Contract.buildSignedArchive(request,nowMoscow,files,signer);
        byte[] zip=archive.bytes();

        // Initial production lane deliberately avoids the contradictory chunked examples.
        Epgu60025907TransportContract.Plan plan=
                Epgu60025907TransportContract.plan(zip,false);
        if(plan.mode!=Epgu60025907TransportContract.Mode.DIRECT_PUSH){
            state.phase=Phase.BLOCKED;state.reason="PACKAGE_TOO_LARGE_FOR_DIRECT_PUSH_REPACK_REQUIRED";
            state.mnemonicToCorrelation.putAll(archive.mnemonicToCorrelation);
            store.save(state.copy());
            return;
        }

        state.mnemonicToCorrelation.putAll(archive.mnemonicToCorrelation);
        state.phase=Phase.SUBMIT_STARTED;state.reason="";
        store.save(state.copy()); // must be durable before network
        try{
            String bearer=required(tokens.bearerToken());
            long id=epgu.pushDirect(bearer,meta.json(),zip);
            if(id<=0)throw new IllegalStateException("INVALID_ORDER_ID");
            state.orderId=id;state.phase=Phase.WAITING;
            store.save(state.copy());
        }catch(Exception e){
            state.phase=Phase.UNKNOWN;state.reason="DIRECT_PUSH_OUTCOME_UNKNOWN";
            store.save(state.copy());
            throw e;
        }
    }

    public synchronized void poll() throws Exception {
        if(state.phase!=Phase.WAITING)throw new IllegalStateException("NOT_WAITING");
        String bearer=required(tokens.bearerToken());
        RemoteResult r=epgu.poll(bearer,state.orderId);
        if(r.status==RemoteStatus.WAITING)return;
        if(r.status==RemoteStatus.REFUSED){
            state.phase=Phase.REFUSED;state.reason=r.reason;store.save(state.copy());return;
        }
        if(r.status==RemoteStatus.ERROR){
            state.phase=Phase.BLOCKED;state.reason=emptyTo(r.reason,"GOSKEY_REMOTE_ERROR");store.save(state.copy());return;
        }
        if(r.status!=RemoteStatus.SIGNED){
            state.phase=Phase.UNKNOWN;state.reason="UNRECOGNIZED_REMOTE_STATUS";store.save(state.copy());return;
        }

        Set<String> expected=new LinkedHashSet<>(state.mnemonicToCorrelation.keySet());
        Set<String> actual=new LinkedHashSet<>(r.signaturesByMnemonic.keySet());
        if(!actual.equals(expected)){
            state.phase=Phase.BLOCKED;state.reason="INCOMPLETE_OR_FOREIGN_SIGNATURE_SET";store.save(state.copy());return;
        }
        Set<String> correlations=new HashSet<>();
        for(String mnemonic:expected){
            String correlation=state.mnemonicToCorrelation.get(mnemonic);
            if(!correlations.add(correlation)){
                state.phase=Phase.BLOCKED;state.reason="DUPLICATE_CORRELATION_MAPPING";store.save(state.copy());return;
            }
            state.signaturesByCorrelation.put(correlation,r.signature(mnemonic));
        }
        state.phase=Phase.COMPLETE;state.reason="";
        store.save(state.copy());
    }

    public synchronized byte[] signatureByCorrelation(String correlation){
        if(state.phase!=Phase.COMPLETE)throw new IllegalStateException("NOT_COMPLETE");
        byte[] b=state.signaturesByCorrelation.get(required(correlation));
        if(b==null)throw new IllegalArgumentException("CORRELATION_NOT_FOUND");
        return b.clone();
    }

    private static String emptyTo(String s,String fallback){return s==null||s.trim().isEmpty()?fallback:s.trim();}
    private static String required(String s){
        if(s==null||s.trim().isEmpty())throw new IllegalArgumentException("EMPTY_REQUIRED_VALUE");
        return s.trim();
    }
}
