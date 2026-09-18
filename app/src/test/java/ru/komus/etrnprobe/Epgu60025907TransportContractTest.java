package ru.komus.etrnprobe;

import org.json.JSONObject;
import org.junit.Test;
import java.util.*;
import static org.junit.Assert.*;

public class Epgu60025907TransportContractTest {
    @Test public void metaUsesExactServiceAndTargetCodes(){
        JSONObject m=new Epgu60025907TransportContract.Meta("77000000000").json();
        assertEquals("77000000000",m.getString("region"));
        assertEquals("60025907",m.getString("serviceCode"));
        assertEquals("-60025907",m.getString("targetCode"));
    }

    @Test public void exactlyFiftyMillionBytesUseDirectPush(){
        byte[] a=new byte[50_000_000];
        Epgu60025907TransportContract.Plan p=Epgu60025907TransportContract.plan(a,false);
        assertEquals(Epgu60025907TransportContract.Mode.DIRECT_PUSH,p.mode);
        assertEquals("/api/gusmev/push",p.endpoint);
        assertFalse(p.reserveOrderFirst);
        assertTrue(p.chunks.isEmpty());
    }

    @Test public void fiftyMillionAndOneRequiresOrderThenChunked(){
        byte[] a=new byte[50_000_001];
        Epgu60025907TransportContract.Plan p=Epgu60025907TransportContract.plan(a,false);
        assertEquals(Epgu60025907TransportContract.Mode.RESERVE_THEN_CHUNKED,p.mode);
        assertEquals("/api/gusmev/push/chunked",p.endpoint);
        assertTrue(p.reserveOrderFirst);
        assertEquals(11,p.chunks.size());
        assertEquals(0,p.chunks.get(0).index);
        assertEquals(10,p.chunks.get(10).index);
        assertEquals(11,p.chunks.get(10).count);
        assertEquals(1,p.chunks.get(10).bytes().length);
    }

    @Test public void orderIdRequirementForcesChunkedEvenForSmallArchive(){
        Epgu60025907TransportContract.Plan p=
            Epgu60025907TransportContract.plan(new byte[20],true);
        assertEquals(Epgu60025907TransportContract.Mode.RESERVE_THEN_CHUNKED,p.mode);
        assertEquals(1,p.chunks.size());
        assertTrue(p.chunks.get(0).first());
        assertTrue(p.chunks.get(0).last());
        assertEquals(0,p.chunks.get(0).index);
    }

    @Test public void normativeChunkNumberingIsZeroBased(){
        List<Epgu60025907TransportContract.Chunk> p=
            Epgu60025907TransportContract.split(new byte[10_000_001],5_000_000);
        assertEquals(3,p.size());
        assertEquals(0,p.get(0).index);
        assertEquals(1,p.get(1).index);
        assertEquals(2,p.get(2).index);
        assertEquals(3,p.get(0).count);
        assertEquals(5_000_000,p.get(0).bytes().length);
        assertEquals(5_000_000,p.get(1).bytes().length);
        assertEquals(1,p.get(2).bytes().length);
    }

    @Test public void nonLastChunkSizeMustBeAtLeastFiveMillion(){
        assertThrows(IllegalArgumentException.class,
            ()->Epgu60025907TransportContract.split(new byte[10_000_000],4_999_999));
    }

    @Test public void chunkCannotExceedFiftyMillion(){
        assertThrows(IllegalArgumentException.class,
            ()->Epgu60025907TransportContract.split(new byte[10],50_000_001));
    }

    @Test public void orderDetailsUsesPostPath(){
        assertEquals("/api/gusmev/order/42",Epgu60025907TransportContract.orderDetails(42));
    }

    @Test public void resultDownloadUsesStatusHistoryObjectTypeMnemonicAndService(){
        String u=Epgu60025907TransportContract.download(99,3,"result 1.sig");
        assertEquals("/api/gusmev/files/download/99/3?mnemonic=result%201.sig&eserviceCode=60025907",u);
    }

    @Test public void documentedSeriesDeadlineIsFiveMinutes(){
        assertEquals(300,Epgu60025907TransportContract.MAX_CHUNK_SERIES_SECONDS);
    }
}
