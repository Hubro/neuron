"""Exercise managed numbers and selects against a temporary local HASS instance."""

import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import requests

PROJECT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parent
ROOT = Path(tempfile.mkdtemp(prefix="neuron-managed-live-"))
CONFIG = ROOT / "hass"
DATA = ROOT / "neuron"
URL = "http://127.0.0.1:18123"
processes = []
logs = []
print(f"Verification directory: {ROOT}", flush=True)


def wait_for(predicate, message, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for process in processes:
            assert process.poll() is None, (
                f"Process {process.args} exited: {process.returncode}"
            )
        try:
            result = predicate()
        except (requests.ConnectionError, requests.Timeout):
            result = None
        if result:
            return result
        time.sleep(0.2)
    raise TimeoutError(message)


def launch(command, log_name, env):
    log = (ROOT / log_name).open("a")
    logs.append(log)
    process = subprocess.Popen(
        command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
    )
    processes.append(process)
    return process


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(25)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    processes.remove(process)


env = {
    key: value
    for key, value in os.environ.items()
    if not key.startswith("NEURON_")
    and key
    not in {
        "HASS_TOKEN",
        "HASS_WEBSOCKET_URI",
        "HOME_ASSISTANT_TOKEN",
        "SUPERVISOR_TOKEN",
        "PYTHONPATH",
        "VERBOSE",
        "TRACE",
        "WS_VERBOSE",
    }
}
env["HOME_ASSISTANT_TOKEN"] = "local-verification-only"
uv = ["uv", "run", "--project", str(PROJECT), "--no-sync"]

with socket.socket() as port_check:
    port_check.bind(("127.0.0.1", 18123))

CONFIG.mkdir()
DATA.mkdir()
shutil.copyfile(FIXTURES / "configuration.yaml", CONFIG / "configuration.yaml")
shutil.copytree(
    PROJECT / "custom_components" / "neuron_integration",
    CONFIG / "custom_components" / "neuron_integration",
    ignore=shutil.ignore_patterns("__pycache__"),
)
package = ROOT / "smoke_package" / "automations"
package.mkdir(parents=True)
shutil.copyfile(FIXTURES / "smoke.py", package / "smoke.py")
options = ROOT / "options.json"
options.write_text(json.dumps({"packages": ["smoke_package"]}))

session = requests.Session()


def request(method, path, **kwargs):
    response = session.request(method, URL + path, timeout=60, **kwargs)
    assert response.ok, f"{method} {path}: {response.status_code} {response.text}"
    return response.json()


def state(entity_id):
    response = session.get(URL + "/api/states/" + entity_id, timeout=10)
    return response.json() if response.ok else {}


def expect(entity_id, expected):
    return wait_for(
        lambda: (
            (value := state(entity_id)) and value["state"] == str(expected) and value
        ),
        f"{entity_id} did not reach {expected!r}; current={state(entity_id)}",
    )


def service(domain, name, **data):
    return request("POST", f"/api/services/{domain}/{name}", json=data)


def set_python(**data):
    request("POST", "/api/events/neuron_smoke_set", json=data)


def saved_values():
    return json.loads((DATA / "persistent-state.json").read_text())[
        "managed_entity_states"
    ]


try:
    hass = launch(
        uv + ["hass", "-c", str(CONFIG), "--skip-pip", "--ignore-os-check"],
        "hass.log",
        env,
    )
    wait_for(lambda: requests.get(URL + "/api/onboarding", timeout=5).ok, "HA startup")
    auth = request(
        "POST",
        "/api/onboarding/users",
        json={
            "name": "Neuron verification",
            "username": "verification",
            "password": secrets.token_urlsafe(24),
            "client_id": URL + "/",
            "language": "en",
        },
    )
    token = request(
        "POST",
        "/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth["auth_code"],
            "client_id": URL + "/",
        },
    )["access_token"]
    session.headers["Authorization"] = "Bearer " + token
    print("Home Assistant started and authenticated", flush=True)

    neuron_env = {
        **env,
        "PYTHONPATH": str(ROOT) + os.pathsep + str(PROJECT),
        "HOME_ASSISTANT_TOKEN": token,
        "HASS_WEBSOCKET_URI": URL.replace("http://", "ws://") + "/api/websocket",
        "NEURON_CONFIG_PATH": str(options),
        "NEURON_DATA_DIR": str(DATA),
        "NEURON_DEV": "1",
    }
    neuron = launch(uv + ["python", "-m", "neuron"], "neuron.log", neuron_env)
    flow = request(
        "POST", "/api/config/config_entries/flow", json={"handler": "neuron"}
    )
    assert flow["type"] == "create_entry", flow
    entry_id = flow["result"]["entry_id"]

    number_state = expect("number.smoke_number", "21.0")
    expect("select.smoke_select", "auto")
    assert number_state["attributes"]["min"] == 5
    assert number_state["attributes"]["max"] == 30
    assert number_state["attributes"]["step"] == 0.5
    assert number_state["attributes"]["mode"] == "slider"
    assert number_state["attributes"]["unit_of_measurement"] == "°C"
    assert number_state["attributes"]["device_class"] == "temperature"
    assert state("select.smoke_select")["attributes"]["options"] == [
        "auto",
        "eco",
        "comfort",
    ]
    assert state("sensor.smoke_measured")["attributes"]["unit_of_measurement"] == "°C"
    print(
        "PASS: registered number/select entities and metadata; renamed sensor unit",
        flush=True,
    )

    service("number", "set_value", entity_id="number.smoke_number", value=23.5)
    service("select", "select_option", entity_id="select.smoke_select", option="eco")
    expect("sensor.smoke_number_observed", "23.5")
    expect("sensor.smoke_select_observed", "eco")
    expect("sensor.smoke_number_callbacks", 1)
    expect("sensor.smoke_select_callbacks", 1)
    assert saved_values()["number.smoke_number"]["value"] == 23.5
    assert saved_values()["select.smoke_select"]["value"] == "eco"
    print(
        "PASS: HA services -> Neuron values, callbacks, and on-disk state", flush=True
    )

    service("number", "set_value", entity_id="number.smoke_number", value=23.5)
    service("select", "select_option", entity_id="select.smoke_select", option="eco")
    time.sleep(0.3)
    assert state("sensor.smoke_number_callbacks")["state"] == "1"
    assert state("sensor.smoke_select_callbacks")["state"] == "1"
    for domain, name, data in [
        ("number", "set_value", {"entity_id": "number.smoke_number", "value": 100}),
        (
            "select",
            "select_option",
            {"entity_id": "select.smoke_select", "option": "invalid"},
        ),
    ]:
        response = session.post(
            URL + f"/api/services/{domain}/{name}", json=data, timeout=10
        )
        assert response.status_code >= 400
    assert saved_values()["number.smoke_number"]["value"] == 23.5
    assert saved_values()["select.smoke_select"]["value"] == "eco"
    print(
        "PASS: unchanged values are no-ops; invalid service inputs are rejected",
        flush=True,
    )

    set_python(number=24.5, select="comfort")
    expect("number.smoke_number", "24.5")
    expect("select.smoke_select", "comfort")
    expect("sensor.smoke_number_callbacks", 2)
    expect("sensor.smoke_select_callbacks", 2)
    print("PASS: Python setters -> HA states and callbacks", flush=True)

    set_python(number=None, select=None)
    expect("number.smoke_number", "unknown")
    expect("select.smoke_select", "unknown")
    set_python(number=25, select="eco")
    expect("number.smoke_number", "25.0")
    expect("select.smoke_select", "eco")
    expect("sensor.smoke_number_callbacks", 4)
    expect("sensor.smoke_select_callbacks", 4)
    print("PASS: unknown states and recovery", flush=True)

    toggle = "switch.neuron_automation_smoke_enabled"
    service("switch", "turn_off", entity_id=toggle)
    expect(toggle, "off")
    time.sleep(0.5)
    service("number", "set_value", entity_id="number.smoke_number", value=26)
    service(
        "select", "select_option", entity_id="select.smoke_select", option="comfort"
    )
    wait_for(
        lambda: saved_values()["number.smoke_number"]["value"] == 26,
        "disabled number saved",
    )
    wait_for(
        lambda: saved_values()["select.smoke_select"]["value"] == "comfort",
        "disabled select saved",
    )
    assert state("sensor.smoke_number_callbacks")["state"] == "4"
    assert state("sensor.smoke_select_callbacks")["state"] == "4"
    service("switch", "turn_on", entity_id=toggle)
    expect(toggle, "on")
    time.sleep(0.5)
    print(
        "PASS: disabled automation persists both values without callbacks", flush=True
    )

    stop(neuron)
    neuron = launch(uv + ["python", "-m", "neuron"], "neuron.log", neuron_env)
    time.sleep(3)
    request("POST", f"/api/config/config_entries/entry/{entry_id}/reload")
    expect("number.smoke_number", "26.0")
    expect("select.smoke_select", "comfort")
    assert state("sensor.smoke_number_callbacks")["state"] == "4"
    assert state("sensor.smoke_select_callbacks")["state"] == "4"
    service("number", "set_value", entity_id="number.smoke_number", value=27)
    service("select", "select_option", entity_id="select.smoke_select", option="auto")
    expect("sensor.smoke_number_observed", "27.0")
    expect("sensor.smoke_select_observed", "auto")
    expect("sensor.smoke_number_callbacks", 5)
    expect("sensor.smoke_select_callbacks", 5)
    print(
        "PASS: Neuron restart, integration reload, restoration, and subsequent commands",
        flush=True,
    )

    all_states = request("GET", "/api/states")
    for prefix in ("number.smoke_number", "select.smoke_select"):
        assert sum(s["entity_id"].startswith(prefix) for s in all_states) == 1, prefix
    print("BOTH MANAGED ENTITIES VERIFIED", flush=True)
finally:
    for process in list(reversed(processes)):
        stop(process)
    session.close()
    for log in logs:
        log.close()
