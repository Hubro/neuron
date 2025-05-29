"""Neoron's API towards automations"""

_trigger_handlers = []


def on_state_change(entity_id: str):
    """Decorator for registering a state change handler"""

    trigger = {
        "trigger": "state",
        "entity_id": entity_id,
    }

    def decorator(fn):
        _trigger_handlers.append((trigger, fn))
        return fn

    return decorator
