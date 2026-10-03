---
name: local-hass-verification
description: Use only when the user explicitly requests verification against a local Home Assistant (HASS) instance. Describes running an isolated HASS and Neuron pair, with a working number/select verification script.
---

# Local HASS verification

This workflow verified managed numbers and selects against Home Assistant
2025.12.1 using real processes, REST service calls, and Neuron's WebSocket connection.

## Run the example

From the repository root, with its `uv` development environment installed:

```bash
uv run --no-sync python .opencode/skills/local-hass-verification/scripts/verify.py
```

The runner uses `127.0.0.1:18123` and checks that the port is free. It infers the
checkout path and prints a fresh temporary directory containing logs, configuration,
and persistent state. `TMPDIR` can place these artifacts elsewhere, such as
`/tmp/opencode`. Both processes stop on completion or failure; artifacts remain
available for inspection.

Bundled files:
- `scripts/verify.py`: starts HASS and Neuron, authenticates, registers the
  integration, exercises the entities, and checks the results.
- `scripts/smoke.py`: fixture automation with a number, select, callback-observation
  sensors, counters, and a `neuron_smoke_set` event handler for Python-side changes.
- `scripts/configuration.yaml`: minimal loopback HASS configuration with API,
  WebSocket, onboarding, config-entry, and logging support.

## How the checks work

The runner copies the companion integration into a fresh HASS config directory,
dereferencing its shared `bus.py` symlink. It starts `uv run … hass -c …`, creates
a temporary owner through `/api/onboarding/users`, and exchanges the returned code
at `/auth/token`. That local token and temporary config/data paths are passed to
`python -m neuron`; `/api/config/config_entries/flow` adds the Neuron integration.

HA-to-Neuron changes use `/api/services/number/set_value` and
`/api/services/select/select_option`. Callback sensors and Neuron's
`persistent-state.json` confirm that requests actually reached Neuron: the HA entity
alone can update optimistically. The reverse direction fires `/api/events/neuron_smoke_set`,
whose automation handler calls the Python setters, then polls `/api/states/<entity_id>`.

The example also checks metadata, repeated-value no-ops, invalid inputs, unknown
states, disabled-automation callback suppression, restoration after restarting
Neuron, integration reload, and duplicate entity IDs.

## Useful findings

- `--skip-pip` assumes HASS component dependencies are installed. The first run
  entered recovery mode because `hass_frontend` was missing. Installing the version
  from the installed HASS `components/frontend/manifest.json` fixed it. For the
  tested version, this was
  `uv pip install --python .venv/bin/python home-assistant-frontend==20251203.1`.
  `--no-sync` keeps `uv run` from removing that extra package.
- Inspect `hass.log` and `neuron.log` in the printed run directory when a check
  stalls. Deliberately invalid service calls can produce `ServiceValidationError`
  tracebacks in the HASS log; these were expected rejection cases.

This skill and its scripts are open to expansion and modification. Adapt the fixture
and assertions to the feature being verified, and incorporate useful new verification
strategies as they are discovered. The example's port, flags, versions, and timings
describe a working setup rather than requirements for every environment.
