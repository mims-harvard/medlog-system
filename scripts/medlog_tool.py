from datetime import datetime, timezone
import uuid
import os
import json
import urllib.request
from datetime import datetime, timezone


class Medlog:
    def __init__(self, system_info=None, api_url=None, api_key=None):
        self.version = "0.0.1"
        self.logs = []
        self.system_info = system_info
        self.run_id = None
        self.target = None
        self.api_url = api_url or os.getenv("MEDLOG_API_URL")
        self.api_key = api_key or os.getenv("MEDLOG_API_KEY")
    
    def create_log(self):
        return {
            "header": {
                "medlog_version": self.version,
                "event_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "system_info": self.system_info 
            },
            "model_instance": {},
            "user_identity": {'process_id': os.getppid()},
            "target_identity": {},
            "inputs": {},
            "internal_artifacts": {},
            "outputs": {},
            "outcomes": {},
            "user_feedback": {}
        }

    def start_run(self):
        self.run_id = str(uuid.uuid4())

    def stop_run(self):
        self.run_id = None

    def set_target(self, value):
        self.target = value

    def _post_event(self, path, payload):
        url = self.api_url.rstrip("/") + path
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5.0):
            pass

    def _base_event(self, log, event_id, parent_event_id=None):
        header = log["header"]
        event = {
            "event_id": event_id,
            "run_id": header["run_id"],
            "timestamp": header["timestamp"],
            "medlog_version": header["medlog_version"],
            "system_metadata": header["system_info"] or {},
        }
        if parent_event_id is None:
            parent_event_id = header.get("parent_event_id")
        if parent_event_id:
            event["parent_event_id"] = parent_event_id
        return event

    def _push_log(self, log):
        event_id = log["header"]["event_id"]
        inference = self._base_event(log, event_id)
        inference.update(
            {
                "model_instance": log["model_instance"],
                "user_identity": log["user_identity"],
                "target_identity": log["target_identity"],
                "inputs": log["inputs"],
            }
        )
        events = [("/event/inference-start", inference)]

        fragments = (
            ("internal_artifacts", "artifact", "/event/internal-artifact"),
            ("outputs", "output", "/event/human-output"),
            ("outcomes", "outcome", "/event/outcome"),
            ("user_feedback", "feedback", "/event/user-feedback"),
        )
        for source_field, payload_field, path in fragments:
            value = log[source_field]
            if value:
                fragment = self._base_event(log, str(uuid.uuid4()), event_id)
                fragment[payload_field] = value
                events.append((path, fragment))

        for path, payload in events:
            try:
                self._post_event(path, payload)
            except Exception as error:
                print(
                    "Warning: Failed to push Medlog event "
                    f"{log['header']['event_id']} to {path}: {error}"
                )

    def _try_push_log(self, log):
        if not self.api_url:
            return
        self._push_log(log)

    def __call__(
        self,
        *,
        model_instance,
        inputs,
        internal_artifacts,
        outputs,
        parent_event_id=None
    ):
        log = self.create_log()
        if self.run_id:
            log['header']['run_id'] = self.run_id
        else:
            log['header']['run_id']  = str(uuid.uuid4())

        if parent_event_id:
            log['header']['parent_event_id'] = parent_event_id

        log['model_instance'] = model_instance

        log['target_identity'] = self.target
        log['inputs'] = inputs
        log['internal_artifacts'] = internal_artifacts
        log['outputs'] = outputs
        self.logs.append(log)
        self._try_push_log(log)

