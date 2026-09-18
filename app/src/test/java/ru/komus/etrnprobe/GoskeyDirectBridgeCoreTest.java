package ru.komus.etrnprobe;

import org.json.JSONObject;
import org.junit.Test;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.*;
import java.util.*;
import static org.junit.Assert.*;

public class GoskeyDirectBridgeCoreTest {
    static final OffsetDateTime NOW=OffsetDateTime.of(2026,9,18,18,0,0,0,ZoneOffset.ofHours(3));

    static class Mem implements GoskeyDirectBridgeCore.Store {
        GoskeyDirectBridgeCore.Snapshot x; boolean fail;
        public GoskeyDirectBridgeCore.Snapshot load(){return x==null?null:x.copy();}
        public void save(GoskeyDirectBridgeCore.Snapshot s)throws Exception{if(fail)throw new IOException("disk");x=s.copy();}
    }
    static class Fake implements GoskeyDirectBridgeCore.Epgu {
        int pushes,polls; boolean failPush; long id=44;
        GoskeyDirectBridgeCore.RemoteResult result=
            new GoskeyDirectBridgeCore.RemoteResult(GoskeyDirectBridgeCore.RemoteStatus.WAITING,Collections.emptyMap(),"");
        public long pushDirect(String bearer,JSONObject meta,byte[] zip)throws Exception{
            pushes++;assertEquals("TOKEN",bearer);assertEquals("60025907",meta.getString("serviceCode"));
            assertTrue(zip.length>0);if(failPush)throw new IOException("timeout");return id;
        }
        public GoskeyDirectBridgeCore.RemoteResult poll(String bearer,long orderId){polls++;assertEquals(44,orderId);return result;}
    }
    static Goskey60025907Contract.Request req(){
        return new Goskey60025907Contract.Request(
            new GoskeyIpPackagePlan.Recipient("123456789012345","","oid1"),
            NOW.plusHours(2),"ЭТрН","ИП Иванов","123456789012","");
    }
    static List<Goskey60025907Contract.BusinessFile> files(){
        return Arrays.asList(
            new Goskey60025907Contract.BusinessFile("etrn_0001.xml","A".getBytes(StandardCharsets.UTF_8),"c1"),
            new Goskey60025907Contract.BusinessFile("etrn_0002.xml","B".getBytes(StandardCharsets.UTF_8),"c2")
        );
    }
    static GoskeyDirectBridgeCore core(Fake f,Mem m)throws Exception{
        return new GoskeyDirectBridgeCore(()->"TOKEN",f,m);
    }

    @Test public void directSubmissionStoresIntentBeforeNetworkAndOrderAfterResponse()throws Exception{
        Fake f=new Fake();Mem m=new Mem();GoskeyDirectBridgeCore c=core(f,m);
        c.submit(new Epgu60025907TransportContract.Meta("45000000000"),req(),NOW,files(),b->new byte[]{9});
        assertEquals(1,f.pushes);
        assertEquals(GoskeyDirectBridgeCore.Phase.WAITING,c.snapshot().phase);
        assertEquals(44,c.snapshot().orderId);
        assertEquals(2,c.snapshot().mnemonicToCorrelation.size());
    }

    @Test public void lostPushResponseIsUnknownAndNeverRepeatable()throws Exception{
        Fake f=new Fake();f.failPush=true;Mem m=new Mem();GoskeyDirectBridgeCore c=core(f,m);
        assertThrows(IOException.class,()->c.submit(
            new Epgu60025907TransportContract.Meta("45"),req(),NOW,files(),b->new byte[]{9}));
        assertEquals(GoskeyDirectBridgeCore.Phase.UNKNOWN,c.snapshot().phase);
        assertEquals(1,f.pushes);
        GoskeyDirectBridgeCore restarted=core(f,m);
        assertThrows(IllegalStateException.class,()->restarted.submit(
            new Epgu60025907TransportContract.Meta("45"),req(),NOW,files(),b->new byte[]{9}));
        assertEquals(1,f.pushes);
    }

    @Test public void waitingDoesNothing()throws Exception{
        Fake f=new Fake();Mem m=new Mem();GoskeyDirectBridgeCore c=core(f,m);
        c.submit(new Epgu60025907TransportContract.Meta("45"),req(),NOW,files(),b->new byte[]{9});
        c.poll();
        assertEquals(GoskeyDirectBridgeCore.Phase.WAITING,c.snapshot().phase);
    }

    @Test public void completeResultMapsMnemonicBackToCorrelation()throws Exception{
        Fake f=new Fake();Mem m=new Mem();GoskeyDirectBridgeCore c=core(f,m);
        c.submit(new Epgu60025907TransportContract.Meta("45"),req(),NOW,files(),b->new byte[]{9});
        Map<String,byte[]> sigs=new LinkedHashMap<>();
        sigs.put("etrn_0002.xml",new byte[]{2});
        sigs.put("etrn_0001.xml",new byte[]{1});
        f.result=new GoskeyDirectBridgeCore.RemoteResult(GoskeyDirectBridgeCore.RemoteStatus.SIGNED,sigs,"");
        c.poll();
        assertEquals(GoskeyDirectBridgeCore.Phase.COMPLETE,c.snapshot().phase);
        assertArrayEquals(new byte[]{1},c.signatureByCorrelation("c1"));
        assertArrayEquals(new byte[]{2},c.signatureByCorrelation("c2"));
    }

    @Test public void missingOrForeignSignatureBlocksPackage()throws Exception{
        Fake f=new Fake();Mem m=new Mem();GoskeyDirectBridgeCore c=core(f,m);
        c.submit(new Epgu60025907TransportContract.Meta("45"),req(),NOW,files(),b->new byte[]{9});
        f.result=new GoskeyDirectBridgeCore.RemoteResult(
            GoskeyDirectBridgeCore.RemoteStatus.SIGNED,
            Collections.singletonMap("etrn_0001.xml",new byte[]{1}),"");
        c.poll();
        assertEquals(GoskeyDirectBridgeCore.Phase.BLOCKED,c.snapshot().phase);
    }

    @Test public void refusalIsTerminalWithoutSignatures()throws Exception{
        Fake f=new Fake();Mem m=new Mem();GoskeyDirectBridgeCore c=core(f,m);
        c.submit(new Epgu60025907TransportContract.Meta("45"),req(),NOW,files(),b->new byte[]{9});
        f.result=new GoskeyDirectBridgeCore.RemoteResult(
            GoskeyDirectBridgeCore.RemoteStatus.REFUSED,Collections.emptyMap(),"user refused");
        c.poll();
        assertEquals(GoskeyDirectBridgeCore.Phase.REFUSED,c.snapshot().phase);
        assertEquals("user refused",c.snapshot().reason);
    }

    @Test public void storageFailureBeforePushPreventsNetwork()throws Exception{
        Fake f=new Fake();Mem m=new Mem();m.fail=true;GoskeyDirectBridgeCore c=core(f,m);
        assertThrows(IOException.class,()->c.submit(
            new Epgu60025907TransportContract.Meta("45"),req(),NOW,files(),b->new byte[]{9}));
        assertEquals(0,f.pushes);
    }

    @Test public void signatureBytesAreDefensiveCopies()throws Exception{
        Map<String,byte[]> m=new LinkedHashMap<>();m.put("etrn_0001.xml",new byte[]{7});
        GoskeyDirectBridgeCore.RemoteResult r=new GoskeyDirectBridgeCore.RemoteResult(
            GoskeyDirectBridgeCore.RemoteStatus.SIGNED,m,"");
        byte[] b=r.signature("etrn_0001.xml");b[0]=1;
        assertEquals(7,r.signature("etrn_0001.xml")[0]);
    }
}
