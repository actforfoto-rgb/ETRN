package ru.komus.etrnprobe;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * Primary-contract model for Госключ service 60025907.
 *
 * No network and no private key storage. A production bridge supplies a CAdES
 * signer backed by its protected server-side key.
 */
public final class Goskey60025907Contract {
    public static final String SERVICE_CODE="60025907";
    public static final String TARGET_CODE="-60025907";
    public static final String NAMESPACE="urn://mpkey.gosuslugi.ru/sign_document_ukep_legalperson/1.0.0";
    public static final int MAX_DOCUMENTS=20;
    public static final long MAX_DOCUMENT_BYTES=100_000_000L;
    public static final int MAX_FILE_NAME=50;

    private static final Set<String> EXT=Collections.unmodifiableSet(new HashSet<>(
            Arrays.asList(".pdf",".tif",".tiff",".jpg",".jpeg",".xml",".txt")));
    private static final Pattern SAFE_NAME=Pattern.compile("^[A-Za-zА-Яа-яЁё0-9_ .,’]+$");
    private static final Pattern IP_INN=Pattern.compile("^[0-9]{12}$");

    private Goskey60025907Contract(){}

    public interface CadesSigner {
        /** Must return detached PKCS#7 CAdES-BES or CAdES-T. */
        byte[] signDetached(byte[] content) throws Exception;
    }

    public static final class Request {
        public final GoskeyIpPackagePlan.Recipient recipient;
        public final OffsetDateTime signExpiration;
        public final String description,orgName,orgInn,backlink;
        public Request(GoskeyIpPackagePlan.Recipient recipient,OffsetDateTime signExpiration,
                       String description,String orgName,String orgInn,String backlink){
            this.recipient=Objects.requireNonNull(recipient);
            this.signExpiration=Objects.requireNonNull(signExpiration);
            this.description=req(description);
            this.orgName=req(orgName);
            this.orgInn=req(orgInn);
            this.backlink=clean(backlink);
            if(this.description.length()>250)throw new IllegalArgumentException("DESCRIPTION_TOO_LONG");
            if(this.orgName.length()>250)throw new IllegalArgumentException("ORG_NAME_TOO_LONG");
            if(!IP_INN.matcher(this.orgInn).matches())throw new IllegalArgumentException("IP_INN_12_DIGITS_REQUIRED");
            if(this.backlink.length()>250)throw new IllegalArgumentException("BACKLINK_TOO_LONG");
        }
    }

    public static final class BusinessFile {
        public final String name,correlationId;
        private final byte[] content;
        public BusinessFile(String name,byte[] content,String correlationId){
            this.name=req(name);this.correlationId=req(correlationId);
            if(content==null||content.length==0)throw new IllegalArgumentException("EMPTY_DOCUMENT");
            this.content=content.clone();
            validateName(this.name);
        }
        public byte[] content(){return content.clone();}
    }

    public static final class SignedArchive {
        private final byte[] zip;
        public final Map<String,String> mnemonicToCorrelation;
        SignedArchive(byte[] zip,Map<String,String> map){
            this.zip=zip.clone();
            this.mnemonicToCorrelation=Collections.unmodifiableMap(new LinkedHashMap<>(map));
        }
        public byte[] bytes(){return zip.clone();}
    }

    public static String safeXmlName(int oneBasedIndex){
        if(oneBasedIndex<1||oneBasedIndex>9999)throw new IllegalArgumentException("INDEX_RANGE");
        return String.format(Locale.ROOT,"etrn_%04d.xml",oneBasedIndex);
    }

    public static byte[] buildReqXml(Request r,OffsetDateTime nowMoscow){
        Objects.requireNonNull(r);Objects.requireNonNull(nowMoscow);
        ZoneOffset msk=ZoneOffset.ofHours(3);
        if(!nowMoscow.getOffset().equals(msk)||!r.signExpiration.getOffset().equals(msk))
            throw new IllegalArgumentException("MOSCOW_OFFSET_REQUIRED");
        Duration d=Duration.between(nowMoscow,r.signExpiration);
        if(d.isZero()||d.isNegative()||d.compareTo(Duration.ofHours(24))>0)
            throw new IllegalArgumentException("SIGN_EXPIRATION_0_TO_24H");
        StringBuilder x=new StringBuilder(1024);
        x.append("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
        x.append("<SignRequest xmlns=\"").append(NAMESPACE)
         .append("\" xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"")
         .append(" xsi:schemaLocation=\"").append(NAMESPACE).append(" schema.xsd\">");
        x.append("<RFOrg>");
        if(!r.recipient.snils.isEmpty())x.append("<Snils>").append(xml(r.recipient.snils)).append("</Snils>");
        else x.append("<OID>").append(xml(r.recipient.oid)).append("</OID>");
        x.append("<OGRN>").append(xml(r.recipient.ogrnip)).append("</OGRN>");
        x.append("</RFOrg>");
        if(!r.backlink.isEmpty())x.append("<Backlink>").append(xml(r.backlink)).append("</Backlink>");
        x.append("<SignExpiration>").append(r.signExpiration.format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)).append("</SignExpiration>");
        x.append("<Description>").append(xml(r.description)).append("</Description>");
        attribute(x,"orgName",r.orgName);
        attribute(x,"orgINN",r.orgInn);
        x.append("</SignRequest>");
        return x.toString().getBytes(StandardCharsets.UTF_8);
    }

    public static SignedArchive buildSignedArchive(Request request,OffsetDateTime nowMoscow,
                                                   List<BusinessFile> documents,CadesSigner signer)throws Exception{
        Objects.requireNonNull(signer);
        if(documents==null||documents.isEmpty()||documents.size()>MAX_DOCUMENTS)
            throw new IllegalArgumentException("DOCUMENT_COUNT_1_TO_20");
        long total=0;Set<String> names=new HashSet<>();Set<String> correlations=new HashSet<>();
        for(BusinessFile f:documents){
            String lower=f.name.toLowerCase(Locale.ROOT);
            if(!names.add(lower))throw new IllegalArgumentException("DUPLICATE_FILE_NAME");
            if(!correlations.add(f.correlationId))throw new IllegalArgumentException("DUPLICATE_CORRELATION");
            total=Math.addExact(total,f.content.length);
        }
        if(total>MAX_DOCUMENT_BYTES)throw new IllegalArgumentException("DOCUMENTS_OVER_100MB");

        byte[] req=buildReqXml(request,nowMoscow);
        byte[] reqSig=nonEmpty(signer.signDetached(req));
        Map<String,String> map=new LinkedHashMap<>();
        ByteArrayOutputStream buffer=new ByteArrayOutputStream();
        try(ZipOutputStream zip=new ZipOutputStream(buffer,StandardCharsets.UTF_8)){
            put(zip,"req.xml",req);put(zip,"req.xml.sig",reqSig);
            for(BusinessFile f:documents){
                byte[] sig=nonEmpty(signer.signDetached(f.content));
                put(zip,f.name,f.content);put(zip,f.name+".sig",sig);
                map.put(f.name,f.correlationId);
            }
        }
        return new SignedArchive(buffer.toByteArray(),map);
    }

    private static void validateName(String n){
        if(n.length()>MAX_FILE_NAME)throw new IllegalArgumentException("FILE_NAME_OVER_50");
        if(n.contains("/")||n.contains("\\"))throw new IllegalArgumentException("PATH_NOT_ALLOWED");
        if(!SAFE_NAME.matcher(n).matches())throw new IllegalArgumentException("FILE_NAME_CHARSET");
        String lower=n.toLowerCase(Locale.ROOT);String ext="";
        for(String e:EXT)if(lower.endsWith(e)){ext=e;break;}
        if(ext.isEmpty())throw new IllegalArgumentException("FILE_EXTENSION_NOT_ALLOWED");
    }
    private static void attribute(StringBuilder x,String name,String value){
        x.append("<Attribute><AttributeName>").append(xml(name)).append("</AttributeName><AttributeValue>")
         .append(xml(value)).append("</AttributeValue></Attribute>");
    }
    private static void put(ZipOutputStream z,String name,byte[] b)throws Exception{
        ZipEntry e=new ZipEntry(name);e.setTime(0L);z.putNextEntry(e);z.write(b);z.closeEntry();
    }
    private static byte[] nonEmpty(byte[] b){
        if(b==null||b.length==0)throw new IllegalArgumentException("EMPTY_CADES_SIGNATURE");return b.clone();
    }
    private static String xml(String s){return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\"","&quot;").replace("'","&apos;");}
    private static String clean(String s){return s==null?"":s.trim();}
    private static String req(String s){String v=clean(s);if(v.isEmpty())throw new IllegalArgumentException("EMPTY_REQUIRED_VALUE");return v;}
}
