import os
from pathlib import Path

os.environ["IPTV_LOCAL_CONFIG_DIR"] = str(Path(__file__).resolve().parents[1] / "fixtures" / "local")
