import copy
import shutil

import pytest

from kidschannel.config import load_config


@pytest.fixture(scope="session")
def base_cfg():
    return load_config()


@pytest.fixture
def cfg(base_cfg):
    return copy.deepcopy(base_cfg)


requires_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg não instalado")
