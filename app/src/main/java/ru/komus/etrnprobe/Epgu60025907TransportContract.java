package ru.komus.etrnprobe;

import org.json.JSONObject;
import java.util.*;

/**
 * Pure transport contract for API EPGU v1.14 + service 60025907.
 * No HTTP client, Bearer token, API-Key or private keys live here.
 */
public final class Epgu60025907TransportContract {
    public static final String ORDER = "/api/gusmev/order";
    public static final String PUSH = "/api/gusmev/push";
    public static final String PUSH_CHUNKED = "/api/gusmev/push/chunked";
    public static final String ORDER_DETAILS_PREFIX = "/api/gusmev/order/";
    public static final String DOWNLOAD_PREFIX = "/api/gusmev/files/download/";

    public static final long MAX_DIRECT_ZIP_BYTES = 50_000_000L;
    public static final int MIN_NON_LAST_CHUNK_BYTES = 5_000_000;
    public static final int MAX_CHUNK_BYTES = 50_000_000;
    public static final int DEFAULT_CHUNK_BYTES = 5_000_000;
    public static final int MAX_CHUNK_SERIES_SECONDS = 300;

    public enum Mode { DIRECT_PUSH, RESERVE_THEN_CHUNKED }

    public static final class Meta {
        public final String region;
        public Meta(String region) { this.region = required(region); }
        public JSONObject json() throws Exception {
            return new JSONObject()
                    .put("region", region)
                    .put("serviceCode", Goskey60025907Contract.SERVICE_CODE)
                    .put("targetCode", Goskey60025907Contract.TARGET_CODE);
        }
    }

    public static final class Chunk {
        /** Normative v1.14 rule: 0..n-1. */
        public final int index;
        public final int count;
        private final byte[] bytes;
        Chunk(int index, int count, byte[] bytes) {
            this.index=index; this.count=count; this.bytes=bytes.clone();
        }
        public byte[] bytes(){return bytes.clone();}
        public boolean first(){return index==0;}
        public boolean last(){return index==count-1;}
    }

    public static final class Plan {
        public final Mode mode;
        public final String endpoint;
        public final boolean reserveOrderFirst;
        public final List<Chunk> chunks;
        Plan(Mode mode,String endpoint,boolean reserveOrderFirst,List<Chunk> chunks){
            this.mode=mode;this.endpoint=endpoint;this.reserveOrderFirst=reserveOrderFirst;
            this.chunks=Collections.unmodifiableList(new ArrayList<>(chunks));
        }
    }

    /**
     * Direct push is allowed only when orderId is not needed in req.xml and ZIP <= 50,000,000 bytes.
     * Service 60025907 does not put EPGU orderId into its SignRequest, but the flag remains explicit.
     */
    public static Plan plan(byte[] zipArchive, boolean requiresOrderIdInsidePayload) {
        if(zipArchive==null||zipArchive.length==0)throw new IllegalArgumentException("EMPTY_ARCHIVE");
        if(!requiresOrderIdInsidePayload && zipArchive.length<=MAX_DIRECT_ZIP_BYTES) {
            return new Plan(Mode.DIRECT_PUSH,PUSH,false,Collections.emptyList());
        }
        return new Plan(Mode.RESERVE_THEN_CHUNKED,PUSH_CHUNKED,true,
                split(zipArchive,DEFAULT_CHUNK_BYTES));
    }

    /**
     * Uses 5,000,000-byte chunks: every non-last part satisfies the primary v1.14 minimum,
     * and the final part may be smaller. Numbering follows the normative rule Chunk=i, i=0..n-1.
     */
    public static List<Chunk> split(byte[] archive,int chunkBytes) {
        if(archive==null||archive.length==0)throw new IllegalArgumentException("EMPTY_ARCHIVE");
        if(chunkBytes<MIN_NON_LAST_CHUNK_BYTES||chunkBytes>MAX_CHUNK_BYTES)
            throw new IllegalArgumentException("CHUNK_SIZE_5M_TO_50M");
        int count=(archive.length+chunkBytes-1)/chunkBytes;
        List<Chunk> out=new ArrayList<>();
        for(int i=0;i<count;i++){
            int start=i*chunkBytes;
            int end=Math.min(archive.length,start+chunkBytes);
            byte[] part=Arrays.copyOfRange(archive,start,end);
            if(i<count-1 && part.length<MIN_NON_LAST_CHUNK_BYTES)
                throw new IllegalStateException("NON_LAST_CHUNK_BELOW_5M");
            if(part.length>MAX_CHUNK_BYTES)throw new IllegalStateException("CHUNK_OVER_50M");
            out.add(new Chunk(i,count,part));
        }
        return Collections.unmodifiableList(out);
    }

    public static String orderDetails(long orderId) {
        if(orderId<=0)throw new IllegalArgumentException("ORDER_ID_POSITIVE");
        return ORDER_DETAILS_PREFIX+orderId;
    }

    public static String download(long currentStatusHistoryId,int objectType,String mnemonic) {
        if(currentStatusHistoryId<=0)throw new IllegalArgumentException("STATUS_HISTORY_ID_POSITIVE");
        if(objectType<=0)throw new IllegalArgumentException("OBJECT_TYPE_POSITIVE");
        return DOWNLOAD_PREFIX+currentStatusHistoryId+"/"+objectType+
                "?mnemonic="+urlComponent(required(mnemonic))+
                "&eserviceCode="+Goskey60025907Contract.SERVICE_CODE;
    }

    private static String urlComponent(String s){
        try{return java.net.URLEncoder.encode(s,"UTF-8").replace("+","%20");}
        catch(Exception e){throw new IllegalStateException(e);}
    }
    private static String required(String s){
        if(s==null||s.trim().isEmpty())throw new IllegalArgumentException("EMPTY_REQUIRED_VALUE");
        return s.trim();
    }
}
