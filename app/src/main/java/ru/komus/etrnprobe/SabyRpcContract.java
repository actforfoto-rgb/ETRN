package ru.komus.etrnprobe;

import org.json.JSONObject;

/** JSON-RPC envelope. Upper-case Params is a named argument of crypto methods. */
public final class SabyRpcContract {
    public static final String CREATE = "sabyCryptoOperation.Create";
    public static final String STATUS = "sabyCryptoOperation.GetStatus";
    private SabyRpcContract() {}

    public static JSONObject envelope(String method, JSONObject arguments, long requestId) throws Exception {
        if (method == null || method.trim().isEmpty() || arguments == null) {
            throw new IllegalArgumentException("RPC method and arguments are required");
        }
        JSONObject payload = new JSONObject(arguments.toString());
        if (CREATE.equals(method) || STATUS.equals(method)) {
            if (payload.has("Params")) throw new IllegalArgumentException("RPC contract: double Params wrapper");
            if (CREATE.equals(method) && payload.optJSONObject("Operation") == null) {
                throw new IllegalArgumentException("RPC contract: Operation object is required");
            }
            if (STATUS.equals(method) && !(payload.opt("OperationID") instanceof String)) {
                throw new IllegalArgumentException("RPC contract: OperationID string is required");
            }
            if (STATUS.equals(method) && payload.getString("OperationID").trim().isEmpty()) {
                throw new IllegalArgumentException("RPC contract: OperationID is empty");
            }
            payload = new JSONObject().put("Params", payload);
        }
        return new JSONObject().put("jsonrpc", "2.0").put("method", method)
                .put("params", payload).put("id", requestId);
    }
}
