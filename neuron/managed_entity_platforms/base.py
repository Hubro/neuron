from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from neuron import api
from neuron.util import filter_keyword_args


@dataclass
class ValueChange[T = Any]:
    from_value: T
    to_value: T


# TODO: Allow managed entities to be created at runtime
class ManagedEntity(ABC):
    """Base class for entities created and managed by Neuron"""

    unique_id: str
    domain: str
    name: str
    friendly_name: str | None
    icon: str | None

    _value: Any  # Depends on the entity type

    def __init__(
        self,
        unique_id: str,
        initial_value: Any = None,
        friendly_name: str | None = None,
        icon: str | None = None,
    ):
        assert unique_id.count(".") == 1, "unique_id must contain 1 period symbol"

        self.unique_id = unique_id
        self.domain, self.name = unique_id.split(".")
        self.friendly_name = friendly_name
        self.icon = icon

        if x := api.get_neuron().state.managed_entity_states.get(unique_id):
            api.get_automation_logger().info(
                "Setting up managed entity %r with value from persistent state %r",
                unique_id,
                x.value,
            )
            self._value = x.value
        else:
            api.get_automation_logger().info(
                "Setting up managed entity %r with initial value (%r)",
                unique_id,
                initial_value,
            )
            self._value = initial_value

        api.register_managed_entity(self)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.unique_id!r}>"

    @abstractmethod
    async def _on_change(self, change: ValueChange):
        raise NotImplementedError("Subclasses should override this method")

    @property
    def value(self) -> Any:
        return self._value

    async def _set_value(self, value: Any, *, suppress_handler: bool = False):
        """Sets the value of this managed entity and transmits it to the integration

        If suppress_handler is True, the _on_change handler won't be called.
        This is used when the automation is disabled, allowing the managed
        entities to keep working and have the correct value when the
        automation is resumed.
        """

        old_value = self._value

        if value == old_value:
            return

        self._value = value

        # TODO: Attributes

        await api.get_neuron().managed_entities.set_value(self.unique_id, value)

        if not suppress_handler:
            await self._on_change(
                **filter_keyword_args(
                    self._on_change,
                    {
                        "change": ValueChange(
                            from_value=old_value,
                            to_value=value,
                        ),
                        "log": api.get_automation_logger(),
                    },
                )
            )
