import logging
import os
from pathlib import Path
import subprocess
from time import sleep
import pytest
from pytest_kubernetes.providers import AClusterManager, select_provider_manager


@pytest.fixture(scope="module")
def k3d():
    k8s: AClusterManager = select_provider_manager("k3d")("gefyra")
    k8s.create()
    k8s.kubectl(["create", "ns", "gefyra"])
    k8s.wait("ns/gefyra", "jsonpath='{.status.phase}'=Active")
    os.environ["KUBECONFIG"] = str(k8s.kubeconfig)
    yield k8s
    k8s.delete()


@pytest.fixture
def operator(k3d):
    from kopf.testing import KopfRunner

    operator = KopfRunner(["run", "-A", "--dev", "main.py"])
    operator.__enter__()
    kopf_logger = logging.getLogger("kopf")
    kopf_logger.setLevel(logging.INFO)
    gefyra_logger = logging.getLogger("gefyra")
    gefyra_logger.setLevel(logging.INFO)

    yield k3d

    try:
        operator.__exit__(None, None, None)
    except:
        pass

@pytest.fixture(scope="session")
def stowaway_image(request):
    name = "stowaway:pytest"
    subprocess.run(
        f"docker build -t {name} -f {(Path(__file__).parent / Path('../../stowaway/Dockerfile')).resolve()}"
        f" {(Path(__file__).parent / Path('../../stowaway/')).resolve()}",
        shell=True,
    )
    request.addfinalizer(lambda: subprocess.run(f"docker rmi {name}", shell=True))
    return name

    