package ru.komus.etrnprobe;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.util.*;

/**
 * Joins PhoneBatchEngine to the direct API EPGU / Goskey 60025907 bridge.
 *
 * The driver key never lives here. CadesSigner is the protected integration-system
 * signer used to submit the Goskey request; returned Goskey signatures are mapped
 * back to the exact Saby document+attachment correlation.
 */
public final class PhoneBatchDirectGoskeyGateway implements PhoneBatchEngine.Gateway {
    public interface Saby {
        String principal();
        List<PhoneBatchEngine.PreparedFile> prepare(PhoneBatchEngine.Document document) throws Exception;
        boolean documentUnchanged(PhoneBatchEngine.Document document,List<PhoneBatchEngine.PreparedFile> files) throws Exception;
        void execute(PhoneBatchEngine.Document document,List<PhoneBatchEngine.Signature> signatures) throws Exception;
        boolean confirmed(PhoneBatchEngine.Document document,List<PhoneBatchEngine.PreparedFile> files) throws Exception;
    }
    public interface PayloadSource {
        byte[] load(PhoneBatchEngine.PreparedFile file) throws Exception;
    }
    public interface Clock {
        OffsetDateTime nowMoscow();
    }
    public interface BridgeStoreFactory {
        GoskeyDirectBridgeCore.Store forPackage(String stablePackageKey) throws Exception;
    }

    private final Saby saby;
    private final PayloadSource payloads;
    private final Clock clock;
    private final BridgeStoreFactory stores;
    private final GoskeyDirectBridgeCore.TokenProvider tokens;
    private final GoskeyDirectBridgeCore.Epgu epgu;
    private final Goskey60025907Contract.CadesSigner initiatorSigner;
    private final Epgu60025907TransportContract.Meta meta;
    private final String senderName, senderInn, backlink;
    private final int expirationMinutes;

    public PhoneBatchDirectGoskeyGateway(
            Saby saby, PayloadSource payloads, Clock clock, BridgeStoreFactory stores,
            GoskeyDirectBridgeCore.TokenProvider tokens, GoskeyDirectBridgeCore.Epgu epgu,
            Goskey60025907Contract.CadesSigner initiatorSigner,
            String region, String senderName, String senderInn, String backlink,
            int expirationMinutes) {
        this.saby=Objects.requireNonNull(saby);
        this.payloads=Objects.requireNonNull(payloads);
        this.clock=Objects.requireNonNull(clock);
        this.stores=Objects.requireNonNull(stores);
        this.tokens=Objects.requireNonNull(tokens);
        this.epgu=Objects.requireNonNull(epgu);
        this.initiatorSigner=Objects.requireNonNull(initiatorSigner);
        this.meta=new Epgu60025907TransportContract.Meta(required(region));
        this.senderName=required(senderName);
        this.senderInn=required(senderInn);
        this.backlink=backlink==null?"":backlink.trim();
        if(expirationMinutes<1||expirationMinutes>24*60)throw new IllegalArgumentException("EXPIRATION_1_TO_1440_MIN");
        this.expirationMinutes=expirationMinutes;
    }

    @Override public String principal(){return saby.principal();}
    @Override public List<PhoneBatchEngine.PreparedFile> prepare(PhoneBatchEngine.Document d)throws Exception{return saby.prepare(d);}
    @Override public boolean documentUnchanged(PhoneBatchEngine.Document d,List<PhoneBatchEngine.PreparedFile> f)throws Exception{return saby.documentUnchanged(d,f);}
    @Override public void execute(PhoneBatchEngine.Document d,List<PhoneBatchEngine.Signature> s)throws Exception{saby.execute(d,s);}
    @Override public boolean confirmed(PhoneBatchEngine.Document d,List<PhoneBatchEngine.PreparedFile> f)throws Exception{return saby.confirmed(d,f);}

    @Override
    public String submitPackage(GoskeyIpPackagePlan.Recipient recipient,
                                List<PhoneBatchEngine.PreparedFile> files) throws Exception {
        if(files==null||files.isEmpty()||files.size()>Goskey60025907Contract.MAX_DOCUMENTS)
            throw new IllegalArgumentException("PACKAGE_SIZE_1_TO_20");
        String key=packageKey(files);
        GoskeyDirectBridgeCore bridge=bridge(key);
        if(bridge.snapshot().phase!=GoskeyDirectBridgeCore.Phase.NEW)
            throw new IllegalStateException("PACKAGE_ALREADY_ATTEMPTED");

        OffsetDateTime now=Objects.requireNonNull(clock.nowMoscow(),"clock");
        Goskey60025907Contract.Request request=new Goskey60025907Contract.Request(
                recipient, now.plusMinutes(expirationMinutes),
                "ЭТрН: пакет из "+files.size()+" документов",senderName,senderInn,backlink);

        List<Goskey60025907Contract.BusinessFile> business=new ArrayList<>();
        for(int i=0;i<files.size();i++){
            PhoneBatchEngine.PreparedFile f=files.get(i);
            ExternalSigningBatch.Item item=f.signingItem();
            byte[] bytes=payloads.load(f);
            business.add(new Goskey60025907Contract.BusinessFile(
                    Goskey60025907Contract.safeXmlName(i+1),bytes,item.correlationId));
        }
        bridge.submit(meta,request,now,Collections.unmodifiableList(business),initiatorSigner);
        GoskeyDirectBridgeCore.Snapshot s=bridge.snapshot();
        if(s.phase!=GoskeyDirectBridgeCore.Phase.WAITING||s.orderId<=0)
            throw new IllegalStateException("GOSKEY_SUBMIT_NOT_WAITING");
        return operationToken(s.orderId,key);
    }

    @Override
    public PhoneBatchEngine.PackagePoll pollPackage(String operationId) throws Exception {
        Token t=parseOperationToken(operationId);
        GoskeyDirectBridgeCore bridge=bridge(t.packageKey);
        GoskeyDirectBridgeCore.Snapshot before=bridge.snapshot();
        if(before.orderId!=t.orderId)throw new IllegalStateException("ORDER_ID_STORE_MISMATCH");
        if(before.phase==GoskeyDirectBridgeCore.Phase.WAITING)bridge.poll();
        GoskeyDirectBridgeCore.Snapshot after=bridge.snapshot();

        if(after.phase==GoskeyDirectBridgeCore.Phase.WAITING)
            return new PhoneBatchEngine.PackagePoll(PhoneBatchEngine.PackageStatus.WAITING,Collections.emptyList());
        if(after.phase==GoskeyDirectBridgeCore.Phase.REFUSED)
            return new PhoneBatchEngine.PackagePoll(PhoneBatchEngine.PackageStatus.REFUSED,Collections.emptyList());
        if(after.phase!=GoskeyDirectBridgeCore.Phase.COMPLETE)
            return new PhoneBatchEngine.PackagePoll(PhoneBatchEngine.PackageStatus.UNKNOWN,Collections.emptyList());

        List<PhoneBatchEngine.Signature> signatures=new ArrayList<>();
        for(String correlation:after.mnemonicToCorrelation.values()){
            Parts p=parseCorrelation(correlation);
            signatures.add(new PhoneBatchEngine.Signature(
                    p.document,p.attachment,p.hash,required(principal()),
                    bridge.signatureByCorrelation(correlation)));
        }
        return new PhoneBatchEngine.PackagePoll(
                PhoneBatchEngine.PackageStatus.SIGNED,
                Collections.unmodifiableList(signatures));
    }

    private GoskeyDirectBridgeCore bridge(String key)throws Exception{
        return new GoskeyDirectBridgeCore(tokens,epgu,stores.forPackage(key));
    }

    static String packageKey(List<PhoneBatchEngine.PreparedFile> files)throws Exception{
        MessageDigest md=MessageDigest.getInstance("SHA-256");
        for(PhoneBatchEngine.PreparedFile f:files){
            add(md,f.document);add(md,f.attachment);add(md,f.hash);
        }
        byte[] d=md.digest();StringBuilder x=new StringBuilder(d.length*2);
        for(byte b:d)x.append(String.format(Locale.ROOT,"%02x",b&0xff));
        return x.toString();
    }
    private static void add(MessageDigest md,String s){
        byte[] b=required(s).getBytes(StandardCharsets.UTF_8);
        md.update(ByteBuffer.allocate(4).putInt(b.length).array());md.update(b);
    }

    private static String operationToken(long orderId,String key){
        if(orderId<=0)throw new IllegalArgumentException("ORDER_ID_POSITIVE");
        return "epgu:"+orderId+":"+required(key);
    }
    static final class Token{final long orderId;final String packageKey;Token(long o,String k){orderId=o;packageKey=k;}}
    static Token parseOperationToken(String value){
        String[] p=required(value).split(":",3);
        if(p.length!=3||!"epgu".equals(p[0]))throw new IllegalArgumentException("INVALID_OPERATION_TOKEN");
        long id;try{id=Long.parseLong(p[1]);}catch(Exception e){throw new IllegalArgumentException("INVALID_OPERATION_TOKEN");}
        if(id<=0||!p[2].matches("[0-9a-f]{64}"))throw new IllegalArgumentException("INVALID_OPERATION_TOKEN");
        return new Token(id,p[2]);
    }

    static final class Parts{
        final String document,attachment,hash;
        Parts(String d,String a,String h){document=d;attachment=a;hash=h;}
    }
    static Parts parseCorrelation(String c){
        String v=required(c);
        int colon=v.indexOf(':');if(colon<1)throw new IllegalArgumentException("BAD_CORRELATION");
        int dl=parsePositive(v.substring(0,colon));
        int ds=colon+1,de=ds+dl;
        if(de>=v.length()||v.charAt(de)!='|')throw new IllegalArgumentException("BAD_CORRELATION");
        String doc=v.substring(ds,de);
        int ac=de+1;
        int colon2=v.indexOf(':',ac);if(colon2<=ac)throw new IllegalArgumentException("BAD_CORRELATION");
        int al=parsePositive(v.substring(ac,colon2));
        int as=colon2+1,ae=as+al;
        if(ae>=v.length()||v.charAt(ae)!='|')throw new IllegalArgumentException("BAD_CORRELATION");
        String att=v.substring(as,ae),hash=v.substring(ae+1);
        if(hash.isEmpty())throw new IllegalArgumentException("BAD_CORRELATION");
        return new Parts(doc,att,hash);
    }
    private static int parsePositive(String s){
        try{int n=Integer.parseInt(s);if(n<1)throw new Exception();return n;}
        catch(Exception e){throw new IllegalArgumentException("BAD_CORRELATION");}
    }
    private static String required(String s){
        if(s==null||s.trim().isEmpty())throw new IllegalArgumentException("EMPTY_REQUIRED_VALUE");
        return s.trim();
    }
}
