package ru.komus.etrnprobe;
import org.json.JSONObject;
/** Package-private seam. Only the lab build may invoke an injected transport. */
final class LabHooks {
    interface Transport { JSONObject call(String endpoint, JSONObject wire, String session) throws Exception; }
    static volatile Transport transport;
    private LabHooks() {}
}
