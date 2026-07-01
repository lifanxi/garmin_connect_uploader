from __future__ import annotations

from gcu.app.models import RemoteActivity

GCU_MANUFACTURER = "HOLUX"
GCU_DEVICE_ID = 0x12345678


def is_gcu_activity(activity: RemoteActivity) -> bool:
    return activity.manufacturer == GCU_MANUFACTURER and activity.device_id == GCU_DEVICE_ID
