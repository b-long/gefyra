from typing import List, Tuple
from gefyra.utils import exec_command_pod
import kubernetes as k8s

from gefyra.bridge.abstract import AbstractGefyraBridgeProvider
from gefyra.configuration import OperatorConfiguration

app = k8s.client.AppsV1Api()
core_v1_api = k8s.client.CoreV1Api()
custom_object_api = k8s.client.CustomObjectsApi()

CARRIER_CONFIGURE_COMMAND_BASE = ["/bin/busybox", "sh", "setroute.sh"]
CARRIER_CONFIGURE_PROBE_COMMAND_BASE = ["/bin/busybox", "sh", "setprobe.sh"]
CARRIER_RSYNC_COMMAND_BASE = ["/bin/busybox", "sh", "syncdirs.sh"]


class Carrier(AbstractGefyraBridgeProvider):
    def __init__(
        self,
        configuration: OperatorConfiguration,
        target_namespace: str,
        target_pod: str,
        target_container: str,
        logger,
    ) -> None:
        self.configuration = configuration
        self.namespace = target_namespace
        self.pod = target_pod
        self.container = target_container
        self.logger = logger

    def install(self, parameters: dict = {}):
        self._patch_pod_with_carrier(handle_probes=parameters.get("handleProbes", True))

    def installed(self) -> bool:
        pod = core_v1_api.read_namespaced_pod(name=self.pod, namespace=self.namespace)
        for container in pod.spec.containers:
            if container.name == self.container:
                if (
                    container.image
                    == f"{self.configuration.CARRIER_IMAGE}:{self.configuration.CARRIER_IMAGE_TAG}"
                ):
                    return True
        else:
            return False

    def ready(self) -> bool:
        installed = self.installed()
        if installed:
            pod = core_v1_api.read_namespaced_pod(
                name=self.pod, namespace=self.namespace
            )
            return all(
                status.ready for status in pod.status.container_statuses
            ) and any(
                f"{self.configuration.CARRIER_IMAGE}:{self.configuration.CARRIER_IMAGE_TAG}"
                in status.image
                for status in pod.status.container_statuses
            )
        else:
            return False

    def uninstall(self) -> bool:
        raise NotImplementedError

    def add_destination(self, destination: str, parameters: dict = {}):
        ip = destination.split(":")[0]
        port = destination.split(":")[1]
        self._configure_carrier(ip, int(port))

    def remove_destination(self, destination: str):
        pass

    def destination_exists(self, destination: str) -> bool:
        raise NotImplementedError

    def validate(self, brige_request: dict):
        raise NotImplementedError

    def _patch_pod_with_carrier(
        self,
        handle_probes: bool,
    ) -> Tuple[bool, k8s.client.V1Pod]:
        """
        Install Gefyra Carrier to the target Pod
        :param pod_name: the name of the Pod to be patched with Carrier
        :param handle_probes: See if Gefyra can handle probes of this Pod
        :return: True if the patch was successful else False
        """

        pod = core_v1_api.read_namespaced_pod(name=self.pod, namespace=self.namespace)

        for container in pod.spec.containers:
            if container.name == self.container:
                if handle_probes:
                    # check if these probes are all supported
                    if not all(
                        map(
                            self._check_probe_compatibility,
                            self._get_all_probes(container),
                        )
                    ):
                        self.logger.error(
                            "Not all of the probes to be handled are currently supported by Gefyra"
                        )
                        return False, pod
                if (
                    container.image
                    == f"{self.configuration.CARRIER_IMAGE}:{self.configuration.CARRIER_IMAGE_TAG}"
                ):
                    # this pod/container is already running Carrier
                    self.logger.info(
                        f"The container {self.container} in Pod {self.pod} is already running Carrier"
                    )
                    return True, pod
                # self._store_pod_original_config(container, ireq_object)
                container.image = f"{self.configuration.CARRIER_IMAGE}:{self.configuration.CARRIER_IMAGE_TAG}"
                break
        else:
            raise RuntimeError(
                f"Could not found container {self.container} in Pod {self.pod}"
            )
        self.logger.info(
            f"Now patching Pod {self.pod}; container {self.container} with Carrier"
        )
        core_v1_api.patch_namespaced_pod(
            name=self.pod, namespace=self.namespace, body=pod
        )

    def _get_all_probes(
        self, container: k8s.client.V1Container
    ) -> List[k8s.client.V1Probe]:
        probes = []
        if container.startup_probe:
            probes.append(container.startup_probe)
        if container.readiness_probe:
            probes.append(container.readiness_probe)
        if container.liveness_probe:
            probes.append(container.liveness_probe)
        return probes

    def _check_probe_compatibility(self, probe: k8s.client.V1Probe) -> bool:
        """
        Check if this type of probe is compatible with Gefyra Carrier
        :param probe: instance of k8s.client.V1Probe
        :return: bool if this is compatible
        """
        if probe is None:
            return True
        elif probe._exec:
            # exec is not supported
            return False
        elif probe.tcp_socket:
            # tcp sockets are not yet supported
            return False
        elif probe.http_get:
            return True
        else:
            return True

    def _store_pod_original_config(
        self, container: k8s.client.V1Container, ireq_object: object
    ) -> None:
        """
        Store the original configuration of that Container in order to restore it once the intercept request is ended
        :param container: V1Container of the Pod in question
        :param ireq_object: the InterceptRequest object
        :return: None
        """
        config = {
            "originalConfig": {
                "image": container.image,
                "command": container.command,
                "args": container.args,
            }
        }
        custom_object_api.patch_namespaced_custom_object(
            name=ireq_object.metadata.name,
            namespace=ireq_object.metadata.namespace,
            body=config,
            group="gefyra.dev",
            plural="gefyrabridges",
            version="v1",
        )

    def _configure_carrier(
        self,
        container_port: int,
        destination_ip: str,
        destination_port: int,
    ):
        if not self.ready():
            raise RuntimeError(
                f"Not able to configure Carrier in Pod {self.pod}. See error above."
            )
        try:
            command = CARRIER_CONFIGURE_COMMAND_BASE + [
                f"{container_port}",
                f"{destination_ip}:{destination_port}",
            ]
            exec_command_pod(
                core_v1_api, self.pod, self.namespace, self.container, command
            )
            # if sync_down_directories:
            #     logger.info(f"Setting directories in Carrier {pod_name} to be down synced")
            #     rsync_cmd = (
            #         CARRIER_RSYNC_COMMAND_BASE
            #         + [f"{pod_name}/{container_name}"]
            #         + sync_down_directories
            #     )
            #     exec_command_pod(
            #         api_instance, pod_name, namespace, container_name, rsync_cmd
            #     )
        except Exception as e:
            self.logger.error(e)
            return
        self.logger.info(f"Carrier configured in {self.pod}")


class CarrierBuilder:
    def __init__(self):
        self._instances = {}

    def __call__(
        self,
        configuration: OperatorConfiguration,
        target_namespace: str,
        target_pod: str,
        target_container: str,
        logger,
        **_ignored,
    ):
        instance = Carrier(
            configuration=configuration,
            target_namespace=target_namespace,
            target_pod=target_pod,
            target_container=target_container,
            logger=logger,
        )
        return instance
