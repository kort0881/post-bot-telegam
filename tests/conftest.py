import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Тесты не должны зависеть от реального окружения CI/локальной машины
os.environ.setdefault("DB_FILE", "test.db")


@pytest.fixture()
def db_file(tmp_path):
    return str(tmp_path / "posted.db")
