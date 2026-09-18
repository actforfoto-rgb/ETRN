#!/usr/bin/env python3
import frida, json, pathlib, sys, time

package = "ru.tensor.sbis.courier.saby"
script_path = pathlib.Path(sys.argv[1])
out_path = pathlib.Path(sys.argv[2])
messages=[]

def on_message(message, data):
    messages.append(message)
    print(json.dumps(message, ensure_ascii=False), flush=True)

device=frida.get_usb_device(timeout=20)
pid=device.spawn([package])
session=device.attach(pid)
script=session.create_script(script_path.read_text(encoding='utf-8'))
script.on("message", on_message)
script.load()
device.resume(pid)
deadline=time.time()+35
while time.time()<deadline:
    if any(m.get("type")=="send" and (m.get("payload") or {}).get("tag")=="PROBE_DONE" for m in messages):
        break
    time.sleep(.5)
out_path.parent.mkdir(parents=True,exist_ok=True)
out_path.write_text(json.dumps(messages,ensure_ascii=False,indent=2)+"\n",encoding='utf-8')
if not any(m.get("type")=="send" and (m.get("payload") or {}).get("tag")=="PROBE_DONE" for m in messages):
    raise SystemExit("PROBE_DID_NOT_COMPLETE")
