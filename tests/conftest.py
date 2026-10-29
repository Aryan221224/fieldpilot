import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# Tests must never reach the network or pick up a developer's real keys.
for k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "FP_VISION_BASE_URL", "FP_DRONE"):
    os.environ[k] = ""

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def demo_images():
    import subprocess
    d = ROOT / "examples" / "images"
    if not any(d.glob("*.jpg")):
        subprocess.run([sys.executable, str(ROOT / "examples" / "make_demo_images.py")], check=True)
    out = {}
    for p in sorted(d.glob("*.jpg")):
        out.setdefault(p.name[:2], []).append(p.read_bytes())
    return out


@pytest.fixture(scope="session")
def field():
    from fieldpilot.field import Field
    return Field.load(ROOT / "examples" / "field_demo.json")
