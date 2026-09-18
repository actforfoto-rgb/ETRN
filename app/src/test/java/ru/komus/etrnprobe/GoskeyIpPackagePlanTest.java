package ru.komus.etrnprobe;

import org.junit.Test;
import java.util.*;
import static org.junit.Assert.*;

public class GoskeyIpPackagePlanTest {
    static ExternalSigningBatch.Item item(int i){
        return new ExternalSigningBatch.Item("doc-"+i,"att-"+i,"hash-"+i);
    }
    static List<ExternalSigningBatch.Item> items(int n){
        List<ExternalSigningBatch.Item> x=new ArrayList<>();
        for(int i=0;i<n;i++)x.add(item(i));
        return x;
    }

    @Test public void fiftyEtrnBecomesTwentyTwentyTen(){
        GoskeyIpPackagePlan.Recipient r=new GoskeyIpPackagePlan.Recipient(
            "123456789012345","123-456-789 00","");
        List<GoskeyIpPackagePlan.Package> p=GoskeyIpPackagePlan.plan(r,items(50));
        assertEquals("60025907",GoskeyIpPackagePlan.SERVICE_CODE);
        assertEquals(3,p.size());
        assertEquals(20,p.get(0).documents.size());
        assertEquals(20,p.get(1).documents.size());
        assertEquals(10,p.get(2).documents.size());
    }

    @Test public void twentyNeedsOneRequest(){
        GoskeyIpPackagePlan.Recipient r=new GoskeyIpPackagePlan.Recipient(
            "123456789012345","","123456789");
        assertEquals(1,GoskeyIpPackagePlan.plan(r,items(20)).size());
    }

    @Test public void twentyOneNeedsTwoRequests(){
        GoskeyIpPackagePlan.Recipient r=new GoskeyIpPackagePlan.Recipient(
            "123456789012345","","123456789");
        assertEquals(2,GoskeyIpPackagePlan.plan(r,items(21)).size());
    }

    @Test public void ogrnipMustBeFifteenDigits(){
        assertThrows(IllegalArgumentException.class,
            ()->new GoskeyIpPackagePlan.Recipient("1234567890123","123-456-789 00",""));
    }

    @Test public void exactlyOneIdentitySelectorIsRequired(){
        assertThrows(IllegalArgumentException.class,
            ()->new GoskeyIpPackagePlan.Recipient("123456789012345","",""));
        assertThrows(IllegalArgumentException.class,
            ()->new GoskeyIpPackagePlan.Recipient("123456789012345","123-456-789 00","123"));
    }

    @Test public void snilsFormatIsFailClosed(){
        assertThrows(IllegalArgumentException.class,
            ()->new GoskeyIpPackagePlan.Recipient("123456789012345","12345678900",""));
    }

    @Test public void packageOrderPreservesDeterministicCorrelation(){
        GoskeyIpPackagePlan.Recipient r=new GoskeyIpPackagePlan.Recipient(
            "123456789012345","","oid-1");
        List<GoskeyIpPackagePlan.Package> p=GoskeyIpPackagePlan.plan(r,items(25));
        assertEquals("doc-0",p.get(0).documents.get(0).documentId);
        assertEquals("doc-19",p.get(0).documents.get(19).documentId);
        assertEquals("doc-20",p.get(1).documents.get(0).documentId);
    }
}
