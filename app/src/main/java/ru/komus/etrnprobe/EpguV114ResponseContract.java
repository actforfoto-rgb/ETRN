package ru.komus.etrnprobe;

import org.json.JSONArray;
import org.json.JSONObject;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Strict parser for the API EPGU v1.14 response shapes used by the Goskey bridge. */
public final class EpguV114ResponseContract {
    private EpguV114ResponseContract(){}

    public static final class FileRef {
        public final String fileName,link,objectType;
        FileRef(String fileName,String link,String objectType){
            this.fileName=required(fileName);this.link=required(link);this.objectType=required(objectType);
        }
        public String downloadPath(long currentStatusHistoryId){
            return Epgu60025907TransportContract.download(
                    currentStatusHistoryId,1,fileName).replace("/1?","/"+encodePathSegment(objectType)+"?");
        }
    }

    public static final class OrderDetails {
        public final long orderId;
        public final Long currentStatusHistoryId;
        public final Integer statusId;
        public final String statusName;
        public final boolean finalStatus,closed;
        public final List<FileRef> responseFiles;
        OrderDetails(long orderId,Long history,Integer statusId,String statusName,
                     boolean finalStatus,boolean closed,List<FileRef> files){
            this.orderId=orderId;this.currentStatusHistoryId=history;this.statusId=statusId;
            this.statusName=statusName==null?"":statusName;this.finalStatus=finalStatus;this.closed=closed;
            this.responseFiles=Collections.unmodifiableList(new ArrayList<>(files));
        }
    }

    public static long parsePushOrderId(JSONObject response){
        Objects.requireNonNull(response,"response");
        return positiveLong(response.opt("orderId"),"orderId");
    }

    public static OrderDetails parseOrder(JSONObject response){
        Objects.requireNonNull(response,"response");
        JSONObject order=decodeOrder(response);
        long orderId=positiveLong(first(order.opt("id"),order.opt("orderId"),response.opt("orderId")),"orderId");

        Object svc=first(order.opt("eserviceId"),order.opt("serviceCode"),response.opt("serviceCode"));
        if(svc!=null && svc!=JSONObject.NULL && !Goskey60025907Contract.SERVICE_CODE.equals(String.valueOf(svc).trim()))
            throw new IllegalArgumentException("UNEXPECTED_SERVICE_CODE");

        JSONObject current=order.optJSONObject("currentStatusHistory");
        Long history=optionalPositiveLong(first(order.opt("currentStatusHistoryId"),
                current==null?null:current.opt("id")),"currentStatusHistoryId");
        Integer status=optionalPositiveInt(first(order.opt("orderStatusId"),
                current==null?null:current.opt("statusId")),"orderStatusId");
        String statusName=clean(order.optString("orderStatusName",""));
        if(statusName.isEmpty()&&current!=null)statusName=clean(current.optString("title",""));
        boolean finalStatus=current!=null&&current.optBoolean("finalStatus",false);
        boolean closed=order.optBoolean("closed",false);

        List<FileRef> files=new ArrayList<>();
        Object raw=order.opt("orderResponseFiles");
        if(raw!=null&&raw!=JSONObject.NULL){
            if(!(raw instanceof JSONArray))throw new IllegalArgumentException("RESPONSE_FILES_NOT_ARRAY");
            JSONArray a=(JSONArray)raw;
            Set<String> names=new HashSet<>();
            for(int i=0;i<a.length();i++){
                Object item=a.opt(i);
                if(!(item instanceof JSONObject))throw new IllegalArgumentException("RESPONSE_FILE_NOT_OBJECT");
                JSONObject f=(JSONObject)item;
                String name=clean(f.optString("fileName","")),link=clean(f.optString("link",""));
                if(name.isEmpty()||link.isEmpty())continue;
                if(!names.add(name))throw new IllegalArgumentException("DUPLICATE_RESPONSE_FILENAME");
                files.add(new FileRef(name,link,objectType(link)));
            }
        }
        return new OrderDetails(orderId,history,status,statusName,finalStatus,closed,files);
    }

    private static JSONObject decodeOrder(JSONObject response){
        Object nested=response.opt("order");
        if(nested==null||nested==JSONObject.NULL)return new JSONObject(response.toString());
        if(nested instanceof JSONObject)return new JSONObject(nested.toString());
        if(nested instanceof String){
            String s=((String)nested).trim();
            if(s.isEmpty())throw new IllegalArgumentException("EMPTY_ORDER");
            try{return new JSONObject(s);}catch(Exception e){throw new IllegalArgumentException("ORDER_NOT_JSON_OBJECT",e);}
        }
        throw new IllegalArgumentException("ORDER_WRONG_TYPE");
    }

    static String objectType(String link){
        String v=required(link);
        try{
            URI u=URI.create(v);
            if("terrabyte".equalsIgnoreCase(u.getScheme())){
                String path=u.getRawPath();if(path==null)throw new Exception();
                String[] p=path.replaceAll("^/+|/+$","").split("/");
                if(p.length<2)throw new Exception();
                return required(URLDecoder.decode(p[p.length-1],StandardCharsets.UTF_8.name()));
            }
        }catch(Exception ignored){}
        int q=v.indexOf('?');if(q>=0)v=v.substring(0,q);
        while(v.endsWith("/"))v=v.substring(0,v.length()-1);
        int slash=v.lastIndexOf('/');
        return required(slash>=0?v.substring(slash+1):v);
    }

    private static String encodePathSegment(String s){
        try{return java.net.URLEncoder.encode(required(s),"UTF-8").replace("+","%20").replace("%2F","%252F");}
        catch(Exception e){throw new IllegalStateException(e);}
    }
    private static Object first(Object... xs){for(Object x:xs)if(x!=null&&x!=JSONObject.NULL)return x;return null;}
    private static long positiveLong(Object x,String field){
        Long v=optionalPositiveLong(x,field);if(v==null)throw new IllegalArgumentException(field+"_MISSING");return v;
    }
    private static Long optionalPositiveLong(Object x,String field){
        if(x==null||x==JSONObject.NULL)return null;
        long v;
        try{
            if(x instanceof Number){
                double d=((Number)x).doubleValue();v=((Number)x).longValue();
                if(!Double.isFinite(d)||d!=v)throw new Exception();
            }else{
                String s=String.valueOf(x).trim();if(!s.matches("[0-9]+"))throw new Exception();v=Long.parseLong(s);
            }
        }catch(Exception e){throw new IllegalArgumentException(field+"_INVALID");}
        if(v<=0)throw new IllegalArgumentException(field+"_NOT_POSITIVE");return v;
    }
    private static Integer optionalPositiveInt(Object x,String field){
        Long v=optionalPositiveLong(x,field);if(v==null)return null;
        if(v>Integer.MAX_VALUE)throw new IllegalArgumentException(field+"_TOO_LARGE");return v.intValue();
    }
    private static String clean(String s){return s==null?"":s.trim();}
    private static String required(String s){String v=clean(s);if(v.isEmpty())throw new IllegalArgumentException("EMPTY_REQUIRED_VALUE");return v;}
}
