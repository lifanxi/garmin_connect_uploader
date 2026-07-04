from __future__ import annotations

import ctypes


APP_USER_MODEL_ID = "LiFanxi.GarminConnectUploader"


try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
except Exception:
    pass
