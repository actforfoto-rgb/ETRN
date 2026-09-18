package ru.komus.etrnprobe;

import org.junit.Test;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.time.*;
import java.util.*;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import static org.junit.Assert.*;

public class Goskey60025907ContractTest {
    private static final OffsetDateTime NOW=OffsetDateTime.of(2026,9,18,18,0,0,0,ZoneOffset.ofHours(3));

    static GoskeyIpPackagePlan.Recipient recipient(){
        return new GoskeyIpPackagePlan.Recipient("123456789012345","123-456-789 00","");
    }
    static Goskey60025907Contract.Request request(){
        return new Goskey60025907Contract.Request(
            recipient(),NOW.plusHours(2),"ЭТрН на подпись","ИП Иванов Иван Иванович",
            "123456789012","https://example.test/done");
    }

    @Test public void reqXmlMatchesPrimarySchemaShape(){
        String x=new String(Goskey60025907Contract.buildReqXml(request(),NOW),StandardCharsets.UTF_8);
        assertTrue(x.startsWith("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"));
        assertTrue(x.contains("<SignRequest xmlns=\"urn://mpkey.gosuslugi.ru/sign_document_ukep_legalperson/1.0.0\""));
        assertTrue(x.contains("<RFOrg><Snils>123-456-789 00</Snils><OGRN>123456789012345</OGRN></RFOrg>"));
        assertTrue(x.contains("<SignExpiration>2026-09-18T20:00:00+03:00</SignExpiration>"));
        assertTrue(x.contains("<AttributeName>orgName</AttributeName>"));
        assertTrue(x.contains("<AttributeName>orgINN</AttributeName>"));
    }

    @Test public void oidMayReplaceSnils(){
        GoskeyIpPackagePlan.Recipient r=new GoskeyIpPackagePlan.Recipient("123456789012345","","123456789");
        Goskey60025907Contract.Request q=new Goskey60025907Contract.Request(
            r,NOW.plusHours(1),"d","ИП Тест","123456789012","");
        String x=new String(Goskey60025907Contract.buildReqXml(q,NOW),StandardCharsets.UTF_8);
        assertTrue(x.contains("<OID>123456789</OID>"));
        assertFalse(x.contains("<Snils>"));
        assertFalse(x.contains("<Backlink>"));
    }

    @Test public void expirationMustBeMoscowAndAtMost24Hours(){
        Goskey60025907Contract.Request tooLate=new Goskey60025907Contract.Request(
            recipient(),NOW.plusHours(25),"d","ИП Тест","123456789012","");
        assertThrows(IllegalArgumentException.class,()->Goskey60025907Contract.buildReqXml(tooLate,NOW));

        OffsetDateTime utc=NOW.withOffsetSameInstant(ZoneOffset.UTC);
        assertThrows(IllegalArgumentException.class,()->Goskey60025907Contract.buildReqXml(request(),utc));
    }

    @Test public void archiveContainsReqAndDetachedSignatureForEveryBusinessFile()throws Exception{
        List<Goskey60025907Contract.BusinessFile> f=Arrays.asList(
            new Goskey60025907Contract.BusinessFile("etrn_0001.xml","A".getBytes(StandardCharsets.UTF_8),"c1"),
            new Goskey60025907Contract.BusinessFile("etrn_0002.xml","B".getBytes(StandardCharsets.UTF_8),"c2")
        );
        Goskey60025907Contract.SignedArchive a=Goskey60025907Contract.buildSignedArchive(
            request(),NOW,f,content->("SIG:"+new String(content,StandardCharsets.UTF_8)).getBytes(StandardCharsets.UTF_8));

        Map<String,byte[]> z=unzip(a.bytes());
        assertEquals(new HashSet<>(Arrays.asList(
            "req.xml","req.xml.sig","etrn_0001.xml","etrn_0001.xml.sig","etrn_0002.xml","etrn_0002.xml.sig"
        )),z.keySet());
        assertEquals("A",new String(z.get("etrn_0001.xml"),StandardCharsets.UTF_8));
        assertEquals("SIG:A",new String(z.get("etrn_0001.xml.sig"),StandardCharsets.UTF_8));
        assertTrue(new String(z.get("req.xml.sig"),StandardCharsets.UTF_8).startsWith("SIG:<?xml"));
        assertEquals("c1",a.mnemonicToCorrelation.get("etrn_0001.xml"));
        assertEquals("c2",a.mnemonicToCorrelation.get("etrn_0002.xml"));
    }

    @Test public void twentyAllowedAndTwentyOneRejected()throws Exception{
        List<Goskey60025907Contract.BusinessFile> f=new ArrayList<>();
        for(int i=1;i<=20;i++)f.add(new Goskey60025907Contract.BusinessFile(
            Goskey60025907Contract.safeXmlName(i),new byte[]{1},"c"+i));
        Goskey60025907Contract.buildSignedArchive(request(),NOW,f,x->new byte[]{9});
        f.add(new Goskey60025907Contract.BusinessFile("etrn_0021.xml",new byte[]{1},"c21"));
        assertThrows(IllegalArgumentException.class,
            ()->Goskey60025907Contract.buildSignedArchive(request(),NOW,f,x->new byte[]{9}));
    }

    @Test public void primaryFilenameRulesAreFailClosed(){
        new Goskey60025907Contract.BusinessFile("Документ 1.xml",new byte[]{1},"c");
        assertThrows(IllegalArgumentException.class,
            ()->new Goskey60025907Contract.BusinessFile("bad-name.xml",new byte[]{1},"c"));
        assertThrows(IllegalArgumentException.class,
            ()->new Goskey60025907Contract.BusinessFile("x.docx",new byte[]{1},"c"));
        assertThrows(IllegalArgumentException.class,
            ()->new Goskey60025907Contract.BusinessFile("../x.xml",new byte[]{1},"c"));
    }

    @Test public void senderInnIsNotConfusedWithRecipientOgrnip(){
        new Goskey60025907Contract.Request(
            recipient(),NOW.plusHours(1),"d","ООО Интегратор","7700000000","");
        new Goskey60025907Contract.Request(
            recipient(),NOW.plusHours(1),"d","ИП Интегратор","123456789012","");
        assertThrows(IllegalArgumentException.class,()->new Goskey60025907Contract.Request(
            recipient(),NOW.plusHours(1),"d","Интегратор","",""));
    }

    @Test public void emptyCadesSignatureFailsBeforeArchiveIsAccepted(){
        assertThrows(IllegalArgumentException.class,()->Goskey60025907Contract.buildSignedArchive(
            request(),NOW,Collections.singletonList(
                new Goskey60025907Contract.BusinessFile("etrn_0001.xml",new byte[]{1},"c")),
            x->new byte[0]));
    }

    @Test public void xmlSpecialCharactersAreEscaped(){
        Goskey60025907Contract.Request q=new Goskey60025907Contract.Request(
            recipient(),NOW.plusHours(1),"A & B < C","ИП \"Тест\"","123456789012","");
        String x=new String(Goskey60025907Contract.buildReqXml(q,NOW),StandardCharsets.UTF_8);
        assertTrue(x.contains("A &amp; B &lt; C"));
        assertTrue(x.contains("ИП &quot;Тест&quot;"));
    }

    private static Map<String,byte[]> unzip(byte[] bytes)throws Exception{
        Map<String,byte[]> out=new LinkedHashMap<>();
        try(ZipInputStream z=new ZipInputStream(new ByteArrayInputStream(bytes),StandardCharsets.UTF_8)){
            for(ZipEntry e;(e=z.getNextEntry())!=null;){
                ByteArrayOutputStream b=new ByteArrayOutputStream();
                byte[] buf=new byte[1024];int n;
                while((n=z.read(buf))!=-1)b.write(buf,0,n);
                out.put(e.getName(),b.toByteArray());
            }
        }
        return out;
    }
}
