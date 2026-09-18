package ru.komus.etrnprobe;

import java.util.*;
import java.util.regex.Pattern;

/**
 * No-network representation of the published Goskey legal-entity/IP signing contract.
 * This is a laboratory gate only: it does not authenticate to EPGU or submit requests.
 */
public final class GoskeyIpPackagePlan {
    public static final String SERVICE_CODE = "60025907";
    public static final int MAX_DOCUMENTS_PER_REQUEST = 20;

    private static final Pattern OGRNIP = Pattern.compile("^[0-9]{15}$");
    private static final Pattern SNILS = Pattern.compile("^[0-9]{3}-[0-9]{3}-[0-9]{3} [0-9]{2}$");
    private static final Pattern OID = Pattern.compile("^[^\\p{Cntrl}]{1,20}$");

    private GoskeyIpPackagePlan() {}

    public static final class Recipient {
        public final String ogrnip;
        public final String snils;
        public final String oid;

        public Recipient(String ogrnip, String snils, String oid) {
            this.ogrnip = required(ogrnip);
            this.snils = clean(snils);
            this.oid = clean(oid);
            if (!OGRNIP.matcher(this.ogrnip).matches()) throw new IllegalArgumentException("OGRNIP_15_DIGITS_REQUIRED");
            boolean hasSnils=!this.snils.isEmpty(), hasOid=!this.oid.isEmpty();
            if (hasSnils == hasOid) throw new IllegalArgumentException("EXACTLY_ONE_SNILS_OR_OID_REQUIRED");
            if (hasSnils && !SNILS.matcher(this.snils).matches()) throw new IllegalArgumentException("INVALID_SNILS_FORMAT");
            if (hasOid && !OID.matcher(this.oid).matches()) throw new IllegalArgumentException("INVALID_ESIA_OID");
        }
    }

    public static final class Package {
        public final int index;
        public final List<ExternalSigningBatch.Item> documents;

        Package(int index, List<ExternalSigningBatch.Item> documents) {
            this.index=index;
            this.documents=Collections.unmodifiableList(new ArrayList<>(documents));
        }
    }

    public static List<Package> plan(Recipient recipient, List<ExternalSigningBatch.Item> documents) {
        Objects.requireNonNull(recipient, "recipient");
        List<List<ExternalSigningBatch.Item>> chunks=
                ExternalSigningBatch.chunk(documents, MAX_DOCUMENTS_PER_REQUEST);
        List<Package> out=new ArrayList<>();
        for(int i=0;i<chunks.size();i++) out.add(new Package(i+1,chunks.get(i)));
        return Collections.unmodifiableList(out);
    }

    private static String clean(String s){return s==null?"":s.trim();}
    private static String required(String s){
        String v=clean(s);
        if(v.isEmpty()) throw new IllegalArgumentException("EMPTY_REQUIRED_VALUE");
        return v;
    }
}
