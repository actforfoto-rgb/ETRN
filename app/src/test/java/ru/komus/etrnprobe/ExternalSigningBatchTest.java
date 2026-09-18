package ru.komus.etrnprobe;

import org.junit.Test;
import java.util.*;
import static org.junit.Assert.*;

public class ExternalSigningBatchTest {
    private static ExternalSigningBatch.Item item(int i) {
        return new ExternalSigningBatch.Item("doc-" + i, "att-" + i, "hash-" + i);
    }

    private static ExternalSigningBatch.SignedItem signed(ExternalSigningBatch.Item i) {
        return new ExternalSigningBatch.SignedItem(i.correlationId, i.hash, new byte[]{1,2,3});
    }

    @Test public void fiftyFilesChunkIntoThreePackagesAtTwenty() {
        List<ExternalSigningBatch.Item> items=new ArrayList<>();
        for(int i=0;i<50;i++) items.add(item(i));
        List<List<ExternalSigningBatch.Item>> chunks=ExternalSigningBatch.chunk(items,20);
        assertEquals(3,chunks.size());
        assertEquals(20,chunks.get(0).size());
        assertEquals(20,chunks.get(1).size());
        assertEquals(10,chunks.get(2).size());
    }

    @Test public void limitIsConfigurableAndNotHardcodedToGoskeyAssumption() {
        List<ExternalSigningBatch.Item> items=new ArrayList<>();
        for(int i=0;i<50;i++) items.add(item(i));
        assertEquals(4,ExternalSigningBatch.chunk(items,15).size());
        assertEquals(2,ExternalSigningBatch.chunk(items,25).size());
    }

    @Test public void shuffledReturnedSignaturesMapToOriginalAttachments() {
        List<ExternalSigningBatch.Item> items=Arrays.asList(item(0),item(1),item(2));
        List<ExternalSigningBatch.SignedItem> returned=Arrays.asList(
            signed(items.get(2)),signed(items.get(0)),signed(items.get(1)));
        Map<String,ExternalSigningBatch.SignedItem> mapped=ExternalSigningBatch.reconcile(items,returned);
        assertArrayEquals(new byte[]{1,2,3},mapped.get("doc-0|att-0").signature());
        assertEquals(3,mapped.size());
    }

    @Test public void missingSignatureBlocksWholeReconciliation() {
        List<ExternalSigningBatch.Item> items=Arrays.asList(item(0),item(1));
        assertThrows(IllegalStateException.class,
            ()->ExternalSigningBatch.reconcile(items,Collections.singletonList(signed(items.get(0)))));
    }

    @Test public void extraSignatureBlocksWholeReconciliation() {
        List<ExternalSigningBatch.Item> items=Collections.singletonList(item(0));
        ExternalSigningBatch.Item foreign=item(9);
        assertThrows(IllegalStateException.class,
            ()->ExternalSigningBatch.reconcile(items,Arrays.asList(signed(items.get(0)),signed(foreign))));
    }

    @Test public void duplicateSignatureBlocksWholeReconciliation() {
        ExternalSigningBatch.Item x=item(0);
        assertThrows(IllegalStateException.class,
            ()->ExternalSigningBatch.reconcile(Collections.singletonList(x),Arrays.asList(signed(x),signed(x))));
    }

    @Test public void changedHashBlocksSignatureUse() {
        ExternalSigningBatch.Item x=item(0);
        ExternalSigningBatch.SignedItem bad=new ExternalSigningBatch.SignedItem(x.correlationId,"other-hash",new byte[]{4});
        assertThrows(IllegalStateException.class,
            ()->ExternalSigningBatch.reconcile(Collections.singletonList(x),Collections.singletonList(bad)));
    }

    @Test public void duplicateAttachmentIsRejectedBeforeNetworkPackaging() {
        ExternalSigningBatch.Item a=new ExternalSigningBatch.Item("doc","att","h1");
        ExternalSigningBatch.Item b=new ExternalSigningBatch.Item("doc","att","h2");
        assertThrows(IllegalArgumentException.class,()->ExternalSigningBatch.chunk(Arrays.asList(a,b),20));
    }

    @Test public void correlationDoesNotDependOnListOrder() {
        ExternalSigningBatch.Item a=new ExternalSigningBatch.Item("docA","attA","hashA");
        ExternalSigningBatch.Item b=new ExternalSigningBatch.Item("docB","attB","hashB");
        assertNotEquals(a.correlationId,b.correlationId);
        Map<String,ExternalSigningBatch.SignedItem> mapped=ExternalSigningBatch.reconcile(
            Arrays.asList(a,b),Arrays.asList(signed(b),signed(a)));
        assertNotNull(mapped.get(a.key()));
        assertNotNull(mapped.get(b.key()));
    }

    @Test public void returnedSignatureBytesAreDefensiveCopies() {
        ExternalSigningBatch.Item x=item(0);
        ExternalSigningBatch.SignedItem s=signed(x);
        byte[] first=s.signature();
        first[0]=99;
        assertEquals(1,s.signature()[0]);
    }
}
