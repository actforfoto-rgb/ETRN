package ru.komus.etrnprobe;

import org.json.JSONObject;
import org.junit.Test;
import java.nio.charset.StandardCharsets;
import java.time.*;
import java.util.*;
import java.util.zip.*;
import java.io.*;
import static org.junit.Assert.*;
import static ru.komus.etrnprobe.PhoneBatchEngine.*;

public class PhoneBatchDirectGoskeyGatewayTest {
    static final OffsetDateTime NOW=OffsetDateTime.of(2026,9,18,22,0,0,0,ZoneOffset.ofHours(3));

    static class BridgeMem implements GoskeyDirectBridgeCore.Store{
        GoskeyDirectBridgeCore.Snapshot x;
        public GoskeyDirectBridgeCore.Snapshot load(){return x==null?null:x.copy();}
        public void save(GoskeyDirectBridgeCore.Snapshot s){x=s.copy();}
    }
    static class Stores implements PhoneBatchDirectGoskeyGateway.BridgeStoreFactory{
        final Map<String,BridgeMem> all=new LinkedHashMap<>();
        public GoskeyDirectBridgeCore.Store forPackage(String key){return all.computeIfAbsent(key,k->new BridgeMem());}
    }
    static class SabyFake implements PhoneBatchDirectGoskeyGateway.Saby{
        final Map<String,byte[]> data=new LinkedHashMap<>();
        final Set<String> done=new LinkedHashSet<>();
        int executes;
        public String principal(){return "driver-ip";}
        public List<PreparedFile> prepare(Document d){
            byte[] b=("<etrn id=\""+d.id+"\"/>").getBytes(StandardCharsets.UTF_8);
            data.put("mem:"+d.id,b);
            return Collections.singletonList(new PreparedFile(d.id,"a-"+d.id,"ignored.xml","mem:"+d.id,"h-"+d.id));
        }
        public boolean documentUnchanged(Document d,List<PreparedFile> f){return true;}
        public void execute(Document d,List<Signature> s){executes++;assertEquals(1,s.size());assertEquals(d.id,s.get(0).document);done.add(d.id);}
        public boolean confirmed(Document d,List<PreparedFile> f){return done.contains(d.id);}
    }
    static class EpguFake implements GoskeyDirectBridgeCore.Epgu{
        long next=100;int pushes,polls;boolean omitLast;
        final Map<Long,List<String>> names=new LinkedHashMap<>();
        final List<Integer> documentCounts=new ArrayList<>();
        public long pushDirect(String bearer,JSONObject meta,byte[] zip)throws Exception{
            assertEquals("BEARER",bearer);
            assertEquals("60025907",meta.getString("serviceCode"));
            assertEquals("-60025907",meta.getString("targetCode"));
            pushes++;List<String> docs=new ArrayList<>();
            try(ZipInputStream z=new ZipInputStream(new ByteArrayInputStream(zip),StandardCharsets.UTF_8)){
                for(ZipEntry e;(e=z.getNextEntry())!=null;){
                    String n=e.getName();
                    if(n.startsWith("etrn_")&&n.endsWith(".xml"))docs.add(n);
                }
            }
            documentCounts.add(docs.size());long id=next++;names.put(id,docs);return id;
        }
        public GoskeyDirectBridgeCore.RemoteResult poll(String bearer,long orderId){
            polls++;List<String> n=names.get(orderId);Map<String,byte[]> s=new LinkedHashMap<>();
            int limit=omitLast?Math.max(0,n.size()-1):n.size();
            for(int i=0;i<limit;i++)s.put(n.get(i),new byte[]{(byte)(i+1)});
            return new GoskeyDirectBridgeCore.RemoteResult(GoskeyDirectBridgeCore.RemoteStatus.SIGNED,s,"");
        }
    }
    static class PhoneMem implements PhoneBatchEngine.Store{
        PhoneBatchEngine.Snapshot x;
        public PhoneBatchEngine.Snapshot load(){return x==null?null:x.copy();}
        public void save(PhoneBatchEngine.Snapshot s){x=s.copy();}
    }
    static List<Document> docs(int n){
        List<Document> x=new ArrayList<>();for(int i=0;i<n;i++)
            x.add(new Document("d"+i,"r"+i,"stage"+i,(i%2==0?"Принят":"Выдан"),"driver-ip"));
        return x;
    }
    static GoskeyIpPackagePlan.Recipient recipient(){return new GoskeyIpPackagePlan.Recipient("123456789012345","","esia-oid");}
    static PhoneBatchDirectGoskeyGateway gateway(SabyFake saby,Stores stores,EpguFake epgu){
        return new PhoneBatchDirectGoskeyGateway(
            saby,
            f->{
                byte[] b=saby.data.get(f.url);
                if(b==null)throw new IllegalStateException("PAYLOAD_NOT_FOUND");
                return b.clone();
            },
            ()->NOW,
            stores,
            ()->"BEARER",
            epgu,
            b->new byte[]{9,9},
            "45000000000",
            "Интеграционная система",
            "123456789012",
            "",
            30);
    }

    @Test public void fiftyEtrnCrossWholeRouteAsThreeGoskeyPackages()throws Exception{
        SabyFake saby=new SabyFake();Stores stores=new Stores();EpguFake epgu=new EpguFake();
        PhoneBatchEngine e=new PhoneBatchEngine(docs(50),recipient(),gateway(saby,stores,epgu),(f,s,p)->true,new PhoneMem());
        e.prepare(true);e.submit(true);
        assertEquals(Arrays.asList(20,20,10),epgu.documentCounts);
        assertEquals(3,epgu.pushes);
        e.pollAndApply();
        assertEquals(Phase.COMPLETE,e.snapshot().phase);
        assertEquals(3,epgu.polls);
        assertEquals(50,saby.executes);
        assertEquals(50,saby.done.size());
    }

    @Test public void processRestartUsesPersistedDirectBridgeStoresWithoutResubmit()throws Exception{
        SabyFake saby=new SabyFake();Stores stores=new Stores();EpguFake epgu=new EpguFake();PhoneMem phone=new PhoneMem();
        PhoneBatchEngine first=new PhoneBatchEngine(docs(21),recipient(),gateway(saby,stores,epgu),(f,s,p)->true,phone);
        first.prepare(true);first.submit(true);
        assertEquals(2,epgu.pushes);
        PhoneBatchEngine restarted=new PhoneBatchEngine(docs(21),recipient(),gateway(saby,stores,epgu),(f,s,p)->true,phone);
        restarted.pollAndApply();
        assertEquals(2,epgu.pushes);
        assertEquals(Phase.COMPLETE,restarted.snapshot().phase);
        assertEquals(21,saby.executes);
    }

    @Test public void incompleteGoskeyPackageNeverExecutesAnySabyDocument()throws Exception{
        SabyFake saby=new SabyFake();Stores stores=new Stores();EpguFake epgu=new EpguFake();epgu.omitLast=true;
        PhoneBatchEngine e=new PhoneBatchEngine(docs(2),recipient(),gateway(saby,stores,epgu),(f,s,p)->true,new PhoneMem());
        e.prepare(true);e.submit(true);e.pollAndApply();
        assertEquals(Phase.UNKNOWN,e.snapshot().phase);
        assertEquals(0,saby.executes);
    }

    @Test public void correlationRoundTripSupportsSeparatorsInsideIds(){
        ExternalSigningBatch.Item i=new ExternalSigningBatch.Item("doc|:x","att:|y","hash|still-hash");
        PhoneBatchDirectGoskeyGateway.Parts p=PhoneBatchDirectGoskeyGateway.parseCorrelation(i.correlationId);
        assertEquals("doc|:x",p.document);
        assertEquals("att:|y",p.attachment);
        assertEquals("hash|still-hash",p.hash);
    }

    @Test public void operationTokenAndPackageKeyAreDeterministic()throws Exception{
        List<PreparedFile> x=Arrays.asList(
            new PreparedFile("d1","a1","x.xml","mem:1","h1"),
            new PreparedFile("d2","a2","x.xml","mem:2","h2"));
        assertEquals(PhoneBatchDirectGoskeyGateway.packageKey(x),PhoneBatchDirectGoskeyGateway.packageKey(x));
        PhoneBatchDirectGoskeyGateway.Token t=PhoneBatchDirectGoskeyGateway.parseOperationToken(
            "epgu:55:"+PhoneBatchDirectGoskeyGateway.packageKey(x));
        assertEquals(55,t.orderId);
    }
}
