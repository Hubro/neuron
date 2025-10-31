import asyncio
import logging
from functools import partial
from typing import assert_never

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant

from . import bus
from .const import DOMAIN
from .sensor import NeuronSensor, NeuronStatsSensor
from .switch import AutomationEnabledSwitch
from .util import neuron_data

__all__ = ["DOMAIN", "async_setup_entry", "async_unload_entry"]

LOG = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config: ConfigEntry):
    # config={'created_at': '2025-10-01T22:22:48.588577+00:00', 'data': {'addon': 'Neuron'}, 'discovery_keys': {'hassio': (DiscoveryKey(domain='hassio', key='be7bc7f728b3492b90e10cb61d2ecbe4', version=1),)}, 'disabled_by': None, 'domain': 'neuron', 'entry_id': '01K6GXXY8C4FQAPFTEVPN6NK27', 'minor_version': 0, 'modified_at': '2025-10-01T22:22:48.588591+00:00', 'options': {}, 'pref_disable_new_entities': False, 'pref_disable_polling': False, 'source': 'hassio', 'subentries': [], 'title': 'Neuron', 'unique_id': 'neuron', 'version': 0}
    LOG.info("Setting up Neuron integration")

    await hass.config_entries.async_forward_entry_setups(config, ["switch"])
    await hass.config_entries.async_forward_entry_setups(config, ["sensor"])

    # device_registry = async_get_device_registry(hass)
    # device_registry.async_get_or_create(
    #     config_entry_id=config.entry_id,
    #     configuration_url=None,
    #     connections=set(),
    #     entry_type=None,
    #     hw_version=None,
    #     identifiers={(DOMAIN, "neuron")},
    #     manufacturer=None,
    #     model="Neuron core",
    #     name="Neuron",
    #     suggested_area=None,
    #     sw_version=None,
    #     via_device=None,  # type: ignore
    # )

    data = neuron_data(hass)
    data.cleanup_event_listener = hass.bus.async_listen(
        "neuron",
        partial(_handle_event, hass),
    )

    asyncio.create_task(_solicit_full_update(hass), name="solicit-initial-full-update")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    LOG.info("Unloading Neuron integration entry: %r", entry.as_dict())

    data = neuron_data(hass)

    if data.cleanup_event_listener:
        LOG.info("Cleaning up event listener")
        data.cleanup_event_listener()

    return True


async def _solicit_full_update(hass):
    """Requests a full state update from Neuron"""

    data = neuron_data(hass)

    async def pester_neutron():
        LOG.info("Sending RequestingFullUpdate event")
        hass.bus.async_fire("neuron", bus.RequestingFullUpdate().model_dump())

        await asyncio.sleep(5)

        if not data.entities_created:
            LOG.warning("No response from Neuron! Trying again.")
            asyncio.create_task(pester_neutron(), name="pester-neutron-for-update")

    asyncio.create_task(pester_neutron(), name="pester-neutron-for-update")

    while True:
        await asyncio.sleep(0.25)

        if data.entities_created:
            return


async def _handle_event(hass: HomeAssistant, event: Event):
    LOG.debug("Received event: %r", event.as_dict())

    try:
        message = bus.parse_message(event.data)
    except Exception:
        LOG.exception("Failed to parse message from Neuron")
        return

    if isinstance(message, bus.NeuronIntegrationMessage):
        return  # Ignore our own messages

    match message:
        case bus.FullUpdate():
            LOG.info("Received full state update from Neuron: %r", message)
            create_entities(hass, message)

        case bus.StatsUpdate():
            LOG.info("Received stats update from Neuron: %r", message)
            # Entities handle this update directly

        case bus.AutomationUpdate():
            LOG.info("Received automation update from Neuron: %r", message)
            # Entities handle this update directly

        case other:
            assert_never(other)


def create_entities(hass: HomeAssistant, message: bus.FullUpdate):
    data = neuron_data(hass)
    assert data.add_switch_entities
    assert data.add_sensor_entities

    # FIXME: If anything doesn't match the existing entity, "add_*_entities"
    # will just create a new entity with "_2" appended. In other words, check
    # if the entities exist already somehow, or destroy all entities in the
    # neuron domain before starting.

    data.add_sensor_entities(
        [
            NeuronStatsSensor(
                hass, stat="trigger_subscriptions", state=message.trigger_subscriptions
            ),
            NeuronStatsSensor(
                hass, stat="event_subscriptions", state=message.event_subscriptions
            ),
            NeuronStatsSensor(
                hass, stat="state_subscriptions", state=message.state_subscriptions
            ),
        ]
    )

    for automation in message.automations:
        data.add_switch_entities(
            [
                AutomationEnabledSwitch(hass, automation),
            ]
        )

        data.add_sensor_entities(
            [
                NeuronSensor(
                    hass,
                    automation.name,
                    "trigger_subscriptions",
                    state=automation.trigger_subscriptions,
                ),
                NeuronSensor(
                    hass,
                    automation.name,
                    "event_subscriptions",
                    state=automation.event_subscriptions,
                ),
                NeuronSensor(
                    hass,
                    automation.name,
                    "state_subscriptions",
                    state=automation.state_subscriptions,
                ),
            ]
        )

    data.entities_created = True
