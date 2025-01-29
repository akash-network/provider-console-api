import time
from fastapi import status

from application.exception.application_error import ApplicationError
from application.model.machine_input import ControlMachineInput, WorkerNodeInput
from application.utils.logger import log
from application.utils.ssh_utils import (
    get_ssh_client,
    run_ssh_command,
    connect_to_worker_node,
)


class K3sService:
    def check_existing_installations(self, control_input: ControlMachineInput):
        log.info(f"Checking for existing installations on {control_input.hostname}")
        try:
            with get_ssh_client(control_input) as ssh_client:
                self._check_kubectl(ssh_client, control_input.hostname)
                self._check_kubelet(ssh_client, control_input.hostname)
                log.info(
                    f"No existing Kubernetes installations found on {control_input.hostname}"
                )
                return {
                    "message": "No existing Kubernetes installations found. Ready to proceed with K3s installation."
                }
        except ApplicationError:
            raise
        except Exception as e:
            self._handle_unexpected_error(e, "installation check")

    def _check_kubectl(self, ssh_client, hostname):
        if self._check_command_exists(ssh_client, "kubectl"):
            log.warning(f"kubectl found on {hostname}")
            raise ApplicationError(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="K3S_001",
                payload={
                    "error": "Existing Installation",
                    "message": "kubectl is already installed on the machine. K3s installation cannot proceed.",
                },
            )

    def _check_kubelet(self, ssh_client, hostname):
        if self._check_command_exists(ssh_client, "kubelet"):
            log.warning(f"kubelet found on {hostname}")
            raise ApplicationError(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="K3S_002",
                payload={
                    "error": "Existing Installation",
                    "message": "Kubernetes is already installed on the machine. K3s installation cannot proceed.",
                },
            )

    def _check_command_exists(self, ssh_client, command: str) -> bool:
        log.debug(f"Checking if command exists: {command}")
        stdout, _ = run_ssh_command(
            ssh_client, f"which {command}", check_exit_status=False
        )
        return bool(stdout.strip())

    def _initialize_k3s_control(
        self, ssh_client, control_input: ControlMachineInput, task_id: str
    ):
        log.info(
            f"Starting K3s initialization on control node {control_input.hostname}"
        )
        try:
            external_ip = control_input.hostname
            disable_components = "traefik"
            install_exec = (
                f"--disable={disable_components} --flannel-backend=none --cluster-init"
            )

            internal_ip, _ = run_ssh_command(
                ssh_client, "hostname -I | awk '{print $1}'", task_id=task_id
            )
            internal_ip = internal_ip.strip()

            install_exec += f" --node-ip={internal_ip}"
            log.info(f"Setting node IP to {internal_ip}")

            if external_ip:
                install_exec += f" --node-external-ip={external_ip}"
                log.info(f"Setting external IP to {external_ip}")

            install_exec += f" --tls-san={internal_ip}"
            if external_ip:
                install_exec += f" --tls-san={external_ip}"
            log.info(
                f"Adding IPs to TLS SAN: {internal_ip}{', ' + external_ip if external_ip else ''}"
            )

            time.sleep(5)

            install_command = f"curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='{install_exec} --node-name node1' sh -"

            log.info(f"Executing K3s initialization command: {install_command}")
            run_ssh_command(ssh_client, install_command, task_id=task_id)

            log.info(
                f"K3s initialization completed, waiting for it to be ready on {control_input.hostname}"
            )
            self._wait_for_k3s_ready(ssh_client, task_id=task_id)

            log.info(
                f"K3s initialization and readiness check completed successfully on {control_input.hostname}"
            )
            return {"message": "K3s initialization completed successfully and is ready"}

        except ApplicationError:
            raise
        except Exception as e:
            self._handle_unexpected_error(e, "K3s initialization")

    def _wait_for_k3s_ready(
        self,
        ssh_client,
        timeout: int = 300,
        check_interval: int = 10,
        task_id: str = None,
    ):
        log.info(
            f"Waiting for K3s to be ready (timeout: {timeout}s, check interval: {check_interval}s)"
        )
        start_time = time.time()
        time.sleep(5)
        while time.time() - start_time < timeout:
            stdout, _ = run_ssh_command(
                ssh_client,
                "kubectl get nodes",
                check_exit_status=False,
                task_id=task_id,
            )
            if "Ready" in stdout:
                log.info("K3s is ready")
                return
            log.debug(
                f"K3s not ready yet, waiting {check_interval} seconds before next check"
            )
            time.sleep(check_interval)

        log.error(f"K3s did not become ready within {timeout} seconds")
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="K3S_009",
            payload={
                "error": "K3s Not Ready",
                "message": f"K3s did not become ready within {timeout} seconds",
            },
        )

    def _update_and_install_dependencies(self, ssh_client, task_id: str):
        try:
            log.info("Updating system and installing dependencies")
            run_ssh_command(ssh_client, "apt-get update", task_id=task_id)
            run_ssh_command(
                ssh_client,
                "DEBIAN_FRONTEND=noninteractive apt-get upgrade -qy",
                task_id=task_id,
            )
            time.sleep(5)
            run_ssh_command(
                ssh_client,
                "DEBIAN_FRONTEND=noninteractive apt-get install git wget unzip curl alsa-utils jq lvm2 -qy",
                task_id=task_id,
            )
            log.info("System update and dependency installation completed successfully")

            log.info("Installing yq...")
            run_ssh_command(
                ssh_client,
                "curl -L https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -o /usr/bin/yq && chmod +x /usr/bin/yq",
                task_id=task_id,
            )
            log.info("yq installation completed successfully")
        except Exception as e:
            log.error(
                f"Error during system update and dependency installation: {str(e)}"
            )
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="K3S_010",
                payload={
                    "error": "Dependency Installation Failed",
                    "message": f"Error during system update and dependency installation: {str(e)}",
                },
            )

    def _install_calico_cni(self, ssh_client, task_id: str):
        try:
            log.info("Installing Calico CNI...")
            commands = [
                "curl -O https://raw.githubusercontent.com/projectcalico/calico/refs/tags/v3.28.2/manifests/calico.yaml",
                'yq eval-all \'(select(.kind == "DaemonSet" and .metadata.name == "calico-node").spec.template.spec.containers[] | select(.name == "calico-node").env) += {"name": "IP_AUTODETECTION_METHOD", "value": "kubernetes-internal-ip"}\' -i calico.yaml',
                "kubectl apply -f calico.yaml",
            ]
            time.sleep(5)
            for command in commands:
                run_ssh_command(ssh_client, command, task_id=task_id)
            log.info("Calico CNI installation completed successfully")
        except Exception as e:
            self._handle_unexpected_error(e, "Calico CNI installation")

    def _update_kubeconfig(self, ssh_client, external_ip: str, task_id: str):
        try:
            log.info(
                f"Updating kubeconfig file to use both internal and external IP addresses..."
            )

            kubeconfig_path = "/etc/rancher/k3s/k3s.yaml"

            internal_ip, _ = run_ssh_command(
                ssh_client, "hostname -I | awk '{print $1}'", task_id=task_id
            )
            internal_ip = internal_ip.strip()

            time.sleep(5)
            ca_data, _ = run_ssh_command(
                ssh_client,
                "kubectl config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}'",
                task_id=task_id,
            )
            ca_data = ca_data.strip()

            client_cert_data, _ = run_ssh_command(
                ssh_client,
                "kubectl config view --raw -o jsonpath='{.users[0].user.client-certificate-data}'",
                task_id=task_id,
            )
            client_cert_data = client_cert_data.strip()

            client_key_data, _ = run_ssh_command(
                ssh_client,
                "kubectl config view --raw -o jsonpath='{.users[0].user.client-key-data}'",
                task_id=task_id,
            )
            client_key_data = client_key_data.strip()

            new_kubeconfig = f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {ca_data}
    server: https://{external_ip}:6443
  name: k3s-cluster
contexts:
- context:
    cluster: k3s-cluster
    user: default
  name: default
current-context: default
kind: Config
preferences: {{}}
users:
- name: default
  user:
    client-certificate-data: {client_cert_data}
    client-key-data: {client_key_data}
"""

            command = (
                f"sudo tee {kubeconfig_path} > /dev/null << EOL\n{new_kubeconfig}\nEOL"
            )
            run_ssh_command(ssh_client, command, task_id=task_id)

            # Create .kube directory if it doesn't exist
            run_ssh_command(ssh_client, "mkdir -p ~/.kube", task_id=task_id)

            # Remove existing config file if it exists
            run_ssh_command(ssh_client, "rm -f ~/.kube/config", task_id=task_id)

            time.sleep(5)
            # Copy k3s.yaml to ~/.kube/config
            run_ssh_command(
                ssh_client, f"sudo cp {kubeconfig_path} ~/.kube/config", task_id=task_id
            )

            # Set correct permissions for the config file
            run_ssh_command(
                ssh_client,
                "sudo chown $(id -u):$(id -g) ~/.kube/config",
                task_id=task_id,
            )
            run_ssh_command(ssh_client, "chmod 644 ~/.kube/config", task_id=task_id)

            log.info("Copied k3s.yaml to ~/.kube/config with correct permissions.")

            log.info(
                "kubeconfig file updated to use both internal and external IP addresses with a single context."
            )
        except Exception as e:
            log.error(f"Error updating kubeconfig: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="K3S_011",
                payload={
                    "error": "Kubeconfig Update Failed",
                    "message": f"Error updating kubeconfig: {str(e)}",
                },
            )

    def _join_control_node(
        self,
        control_ssh_client,
        node_input: WorkerNodeInput,
        node_name: str,
        task_id: str,
    ):
        log.info(f"Starting K3s installation on control node {node_input.hostname}")
        try:
            # Get K3s token from the main control node
            token, _ = run_ssh_command(
                control_ssh_client,
                "sudo cat /var/lib/rancher/k3s/server/node-token",
                task_id=task_id,
            )

            # Get the main control node's IP address
            master_ip, _ = run_ssh_command(
                control_ssh_client, "hostname -I | awk '{print $1}'", task_id=task_id
            )

            # Connect to the worker node through the control node
            worker_ssh_client = connect_to_worker_node(control_ssh_client, node_input)
            try:
                # Get the internal IP of the new control node
                internal_ip, _ = run_ssh_command(
                    worker_ssh_client, "hostname -I | awk '{print $1}'", task_id=task_id
                )
                internal_ip = internal_ip.strip()

                # Set up the installation command
                install_exec = f"--disable=traefik --flannel-backend=none --node-ip={internal_ip} --node-name {node_name}"

                if node_input.hostname:
                    install_exec += f" --tls-san={node_input.hostname}"

                install_command = f"""curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server {install_exec}" K3S_URL="https://{master_ip}:6443" K3S_TOKEN="{token}" sh -"""

                # Execute the installation command
                stdout, stderr = run_ssh_command(
                    worker_ssh_client, install_command, task_id=task_id
                )

                log.info(
                    f"Control-plane node {node_input.hostname} added to the cluster."
                )
                return {
                    "message": "Control-plane node added to the cluster successfully",
                    "stdout": stdout,
                    "stderr": stderr,
                }
            finally:
                worker_ssh_client.close()
        except ApplicationError:
            raise
        except Exception as e:
            self._handle_unexpected_error(e, "K3s installation on control")

    def _join_worker_node(
        self,
        control_ssh_client,
        node_input: WorkerNodeInput,
        node_name: str,
        task_id: str,
    ):
        log.info(f"Starting K3s installation on worker node {node_input.hostname}")
        try:
            # Get K3s token from the main control node
            token, _ = run_ssh_command(
                control_ssh_client,
                "sudo cat /var/lib/rancher/k3s/server/node-token",
                task_id=task_id,
            )

            # Get the main control node's IP address
            master_ip, _ = run_ssh_command(
                control_ssh_client, "hostname -I | awk '{print $1}'", task_id=task_id
            )

            # Connect to the worker node through the control node
            worker_ssh_client = connect_to_worker_node(control_ssh_client, node_input)
            try:
                # Install K3s on the worker node
                install_command = f"curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='--node-name {node_name}' K3S_URL=https://{master_ip}:6443 K3S_TOKEN={token} sh -"
                stdout, stderr = run_ssh_command(
                    worker_ssh_client, install_command, task_id=task_id
                )

                log.info(
                    f"K3s installation completed successfully on worker node {node_input.hostname}"
                )
                return {
                    "message": "K3s installation completed successfully",
                    "stdout": stdout,
                    "stderr": stderr,
                }
            finally:
                worker_ssh_client.close()
        except ApplicationError:
            raise
        except Exception as e:
            self._handle_unexpected_error(e, "K3s installation on worker")

    def _install_gpu_drivers_and_toolkit(
        self,
        ssh_client,
        control_input: ControlMachineInput,
        node_type: str,
        task_id: str,
    ):
        log.info(
            f"Starting GPU host preparation, driver, and toolkit installation on {control_input.hostname}"
        )

        try:
            ssh_connection = (
                connect_to_worker_node(ssh_client, control_input)
                if node_type == "worker_node"
                else ssh_client
            )
            self._update_system(ssh_connection, control_input, task_id)
            self._install_nvidia_drivers(ssh_connection, control_input, task_id)
            self._install_nvidia_container_runtime(
                ssh_connection, control_input, task_id
            )
            self._configure_nvidia_runtime(ssh_connection, control_input, task_id)
            self._reboot_node(ssh_connection, control_input, task_id)

            log.info(
                f"GPU drivers and toolkit installation completed successfully on {control_input.hostname}"
            )
            return {
                "message": "GPU drivers and toolkit installation completed successfully"
            }
        except ApplicationError:
            raise
        except Exception as e:
            self._handle_unexpected_error(e, "GPU installation")

    # def _check_akash_node_status(self, ssh_client, task_id: str):
    #     log.info("Checking Akash node status")
    #     try:
    #         time.sleep(5)

    #         stdout, _ = run_ssh_command(ssh_client, "kubectl exec -it akash-node-1-0 -n akash-services -c akash-node -- akash status")
    #         log.info(f"Akash node status: {stdout}")
    #         log.info("Akash node status check completed successfully")
    #     except ApplicationError:
    #         raise
    #     except Exception as e:
    #         self._handle_unexpected_error(e, "Akash node status check")

    def _update_system(
        self, ssh_client, control_input: ControlMachineInput, task_id: str
    ):
        log.info(f"Updating system on {control_input.hostname}")
        time.sleep(5)
        run_ssh_command(ssh_client, "apt update", task_id=task_id)
        run_ssh_command(
            ssh_client,
            'DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" dist-upgrade',
            task_id=task_id,
        )
        run_ssh_command(ssh_client, "apt-get autoremove -y", task_id=task_id)

    def _install_nvidia_drivers(
        self, ssh_client, control_input: ControlMachineInput, task_id: str
    ):
        log.info(f"Installing NVIDIA drivers on {control_input.hostname}")
        time.sleep(5)
        run_ssh_command(
            ssh_client, "apt-get install -y ubuntu-drivers-common", task_id=task_id
        )
        run_ssh_command(ssh_client, "ubuntu-drivers devices", task_id=task_id)
        run_ssh_command(ssh_client, "ubuntu-drivers autoinstall", task_id=task_id)

    def _install_nvidia_container_runtime(
        self, ssh_client, control_input: ControlMachineInput, task_id: str
    ):
        log.info(f"Installing NVIDIA container runtime on {control_input.hostname}")
        time.sleep(5)
        commands = [
            "curl -s -L https://nvidia.github.io/libnvidia-container/gpgkey | apt-key add -",
            "curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/libnvidia-container.list | tee /etc/apt/sources.list.d/libnvidia-container.list",
            "apt-get update",
            "DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-container-toolkit nvidia-container-runtime",
        ]
        for cmd in commands:
            run_ssh_command(ssh_client, cmd, task_id=task_id)

    def _update_coredns_config(self, ssh_client, task_id: str):
        log.info("Updating CoreDNS configuration")
        try:
            time.sleep(5)
            run_ssh_command(
                ssh_client,
                "while ! kubectl -n kube-system get cm coredns >/dev/null 2>&1; do echo waiting for the coredns configmap resource ...; sleep 2; done",
                task_id=task_id,
            )

            patch_command = """
            kubectl patch configmap coredns -n kube-system --type merge -p '{"data":{"Corefile":".:53 {\\n        errors\\n        health\\n        ready\\n        kubernetes cluster.local in-addr.arpa ip6.arpa {\\n          pods insecure\\n          fallthrough in-addr.arpa ip6.arpa\\n        }\\n        hosts /etc/coredns/NodeHosts {\\n          ttl 60\\n          reload 15s\\n          fallthrough\\n        }\\n        prometheus :9153\\n        forward . 8.8.8.8 1.1.1.1\\n        cache 30\\n        loop\\n        reload\\n        loadbalance\\n        import /etc/coredns/custom/*.override\\n    }\\n    import /etc/coredns/custom/*.server"}}'
            """
            run_ssh_command(ssh_client, patch_command, task_id=task_id)
            log.info("CoreDNS configuration updated successfully")
        except Exception as e:
            log.error(f"Error updating CoreDNS configuration: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="K3S_012",
                payload={
                    "error": "CoreDNS Update Failed",
                    "message": f"Error updating CoreDNS configuration: {str(e)}",
                },
            )

    def _create_and_label_namespaces(self, ssh_client, task_id: str):
        log.info("Creating and labeling Kubernetes namespaces")
        try:
            namespaces = ["akash-services", "lease"]
            for ns in namespaces:
                check_ns_command = (
                    f"kubectl get ns {ns} > /dev/null 2>&1 || kubectl create ns {ns}"
                )
                run_ssh_command(ssh_client, check_ns_command, task_id=task_id)

            label_commands = [
                "kubectl label ns akash-services akash.network/name=akash-services akash.network=true --overwrite",
                "kubectl label ns lease akash.network=true --overwrite",
            ]
            for cmd in label_commands:
                run_ssh_command(ssh_client, cmd, task_id=task_id)

            log.info("Kubernetes namespaces created and labeled successfully")
        except Exception as e:
            log.error(f"Error creating and labeling Kubernetes namespaces: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="K3S_013",
                payload={
                    "error": "Namespace Creation Failed",
                    "message": f"Error creating and labeling Kubernetes namespaces: {str(e)}",
                },
            )

    def _configure_nvidia_runtime(
        self, ssh_client, control_input: ControlMachineInput, task_id: str
    ):
        log.info(f"Configuring NVIDIA runtime on {control_input.hostname}")
        config_file = "/etc/nvidia-container-runtime/config.toml"
        check_file_cmd = f"[ -f {config_file} ] && echo 'exists' || echo 'not found'"
        stdout, _ = run_ssh_command(ssh_client, check_file_cmd, task_id=task_id)

        if "exists" in stdout:
            run_ssh_command(
                ssh_client,
                f"sed -i 's/#accept-nvidia-visible-devices-as-volume-mounts = false/accept-nvidia-visible-devices-as-volume-mounts = true/' {config_file}",
                task_id=task_id,
            )
            run_ssh_command(
                ssh_client,
                f"sed -i 's/#accept-nvidia-visible-devices-envvar-when-unprivileged = true/accept-nvidia-visible-devices-envvar-when-unprivileged = false/' {config_file}",
                task_id=task_id,
            )
        else:
            log.warning(
                f"NVIDIA runtime configuration file not found on {control_input.hostname}"
            )

    def _reboot_node(
        self, ssh_client, control_input: ControlMachineInput, task_id: str
    ):
        log.info(f"Checking NVIDIA driver status on {control_input.hostname}")
        try:
            # Check NVIDIA driver status
            stdout, stderr = run_ssh_command(
                ssh_client,
                "nvidia-smi --version",
                check_exit_status=False,
                task_id=task_id,
            )

            if (
                "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver"
                in stdout
            ):
                log.info(
                    f"NVIDIA driver not properly loaded on {control_input.hostname}, initiating reboot"
                )
                run_ssh_command(ssh_client, "reboot", task_id=task_id)

                # Wait for the node to go down
                time.sleep(10)

                # Wait for the node to come back online (max 5 minutes)
                max_attempts = 30
                attempt = 0
                while attempt < max_attempts:
                    try:
                        with get_ssh_client(control_input) as new_ssh_client:
                            # Try to run a simple command to verify SSH connectivity
                            run_ssh_command(new_ssh_client, "uptime", task_id=task_id)
                            log.info(f"Node {control_input.hostname} is back online")
                            return
                    except Exception:
                        attempt += 1
                        if attempt < max_attempts:
                            log.info(
                                f"Waiting for node {control_input.hostname} to come back online... (attempt {attempt}/{max_attempts})"
                            )
                            time.sleep(10)

                raise Exception(
                    f"Node {control_input.hostname} did not come back online after reboot"
                )
            else:
                log.info(
                    f"NVIDIA driver is properly loaded on {control_input.hostname}, no reboot needed"
                )

        except Exception as e:
            log.error(
                f"Error during reboot process for node {control_input.hostname}: {str(e)}"
            )
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="K3S_014",
                payload={
                    "error": "Reboot Failed",
                    "message": f"Error during reboot process for node {control_input.hostname}: {str(e)}",
                },
            )

    def _handle_unexpected_error(self, e, operation):
        log.error(f"Unexpected error during {operation}: {str(e)}")
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="K3S_003",
            payload={
                "error": "Unexpected Error",
                "message": f"Unexpected error during {operation}: {str(e)}",
            },
        )
