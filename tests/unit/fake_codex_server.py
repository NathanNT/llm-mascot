"""A stand-in for `codex app-server`: speaks just enough of its JSON-lines protocol for the tests."""
import json
import sys

threads = 0
for line in sys.stdin:
    message = json.loads(line)
    method, request_id, params = message.get("method"), message.get("id"), message.get("params", {})
    if method == "initialize":
        reply = {"id": request_id, "result": {"userAgent": "fake"}}
    elif method == "thread/start":
        threads += 1
        sys.stderr.write(json.dumps(params) + "\n")
        reply = {"id": request_id, "result": {"thread": {"id": f"t{threads}"}}}
        print(json.dumps({"method": "thread/status/changed", "params": {"threadId": f"t{threads}"}}), flush=True)
    elif method == "turn/start":
        print(json.dumps({"id": request_id, "result": {}}), flush=True)
        text = params["input"][0]["text"]
        if "FAIL" in text:
            done = {"threadId": params["threadId"], "turn": {"status": "failed", "error": {"message": "boom"}, "items": []}}
        else:
            done = {"threadId": params["threadId"], "turn": {"status": "completed", "items": [
                {"type": "reasoning"}, {"type": "agentMessage", "text": f'  "{text.upper()}"  '}]}}
        print(json.dumps({"method": "turn/completed", "params": done}), flush=True)
        continue
    else:
        continue
    print(json.dumps(reply), flush=True)
