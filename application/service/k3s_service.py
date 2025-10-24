import time
from fastapi import status
import json
import io

from fastapi import UploadFile

from application.exception.application_error import ApplicationError
from application.model.machine_input import ControlMachineInput, WorkerNodeInput
from application.utils.logger import log
from application.utils.ssh_utils import (
    get_ssh_client,
    run_ssh_command,
    connect_to_worker_node,
)


class K3sService:

    INTERNAL_IP_CMD = r"""ip -4 -o a | while read -r line; do set -- $line; if echo "$4" | grep -qE '^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.)'; then echo "${4%/*}"; break; fi; done"""
    SSH_KEY_CMD = "cat ~/.ssh/id_ed25519"

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
                f"--disable={disable_components} --flannel-backend=none --disable-network-policy --cluster-init"
            )

            internal_ip, _ = run_ssh_command(
                ssh_client, self.INTERNAL_IP_CMD, task_id=task_id
            )
            internal_ip = internal_ip.strip()

            install_exec += f" --node-ip={internal_ip} --advertise-address={internal_ip} --kube-scheduler-arg=config=/var/lib/rancher/k3s/server/etc/scheduler-config.yaml"
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

            scheduler_config = """
cat > /var/lib/rancher/k3s/server/etc/scheduler-config.yaml << EOF
apiVersion: kubescheduler.config.k8s.io/v1
kind: KubeSchedulerConfiguration
clientConnection:
  kubeconfig: "/var/lib/rancher/k3s/server/cred/scheduler.kubeconfig"
leaderElection:
  leaderElect: true
profiles:
- schedulerName: default-scheduler
  plugins:
    score:
      enabled:
      - name: NodeResourcesFit
  pluginConfig:
  - name: NodeResourcesFit
    args:
      scoringStrategy:
        type: MostAllocated
        resources:
        - name: nvidia.com/gpu
          weight: 10
        - name: memory
          weight: 1
        - name: cpu
          weight: 1
        - name: ephemeral-storage
          weight: 1
EOF
"""
            run_ssh_command(
                ssh_client, "mkdir -p /var/lib/rancher/k3s/server/etc", task_id=task_id
            )
            run_ssh_command(ssh_client, scheduler_config, task_id=task_id)

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
                "curl -O https://raw.githubusercontent.com/projectcalico/calico/v3.30.3/manifests/calico.yaml",
                'yq eval -i \'(select(.kind=="DaemonSet" and .metadata.name=="calico-node").spec.template.spec.containers[] | select(.name=="calico-node").env[] | select(.name=="CALICO_IPV4POOL_VXLAN").value) = "Always"\' calico.yaml',
                'yq eval -i \'(select(.kind=="DaemonSet" and .metadata.name=="calico-node").spec.template.spec.containers[] | select(.name=="calico-node").env[] | select(.name=="CALICO_IPV4POOL_IPIP").value) = "Never"\' calico.yaml',
                'yq eval-all \'(select(.kind == "DaemonSet" and .metadata.name == "calico-node").spec.template.spec.containers[] | select(.name == "calico-node").env) += [{"name":"IP_AUTODETECTION_METHOD","value":"kubernetes-internal-ip"}, {"name":"FELIX_WIREGUARDENABLED","value":"false"}]\' -i calico.yaml',
                'yq eval -i \'(select(.kind=="DaemonSet" and .metadata.name=="calico-node").spec.template.spec.containers[] | select(.name=="calico-node").readinessProbe.exec.command) = ["/bin/calico-node","-felix-ready"]\' calico.yaml',
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
            log.info(f"Updating kubeconfig file to use internal IP address...")

            kubeconfig_path = "/etc/rancher/k3s/k3s.yaml"

            internal_ip, _ = run_ssh_command(
                ssh_client, self.INTERNAL_IP_CMD, task_id=task_id
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
    server: https://{internal_ip}:6443
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

            run_ssh_command(ssh_client, "chmod 600 ~/.kube/config", task_id=task_id)

            # Set correct permissions for the config file
            run_ssh_command(
                ssh_client,
                "sudo chown $(id -u):$(id -g) ~/.kube/config",
                task_id=task_id,
            )
            run_ssh_command(ssh_client, "chmod 644 ~/.kube/config", task_id=task_id)

            log.info("Copied k3s.yaml to ~/.kube/config with correct permissions.")

            log.info("kubeconfig file updated to use internal IP address.")
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
                control_ssh_client, self.INTERNAL_IP_CMD, task_id=task_id
            )

            # Connect to the worker node through the control node
            worker_ssh_client = connect_to_worker_node(control_ssh_client, node_input)
            try:
                # Get the internal IP of the new control node
                internal_ip, _ = run_ssh_command(
                    worker_ssh_client, self.INTERNAL_IP_CMD, task_id=task_id
                )
                internal_ip = internal_ip.strip()

                # Set up the installation command
                install_exec = f"--disable=traefik --flannel-backend=none --disable-network-policy --node-ip={internal_ip} --node-name {node_name} --kube-scheduler-arg=config=/var/lib/rancher/k3s/server/etc/scheduler-config.yaml"

                if node_input.hostname:
                    install_exec += f" --tls-san={node_input.hostname}"

                install_command = f"""curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server {install_exec}" K3S_URL="https://{master_ip}:6443" K3S_TOKEN="{token}" sh -"""

                scheduler_config = """
cat > /var/lib/rancher/k3s/server/etc/scheduler-config.yaml << EOF
apiVersion: kubescheduler.config.k8s.io/v1
kind: KubeSchedulerConfiguration
clientConnection:
  kubeconfig: "/var/lib/rancher/k3s/server/cred/scheduler.kubeconfig"
leaderElection:
  leaderElect: true
profiles:
- schedulerName: default-scheduler
  plugins:
    score:
      enabled:
      - name: NodeResourcesFit
  pluginConfig:
  - name: NodeResourcesFit
    args:
      scoringStrategy:
        type: MostAllocated
        resources:
        - name: nvidia.com/gpu
          weight: 10
        - name: memory
          weight: 1
        - name: cpu
          weight: 1
        - name: ephemeral-storage
          weight: 1
EOF
"""
                run_ssh_command(
                    worker_ssh_client,
                    "mkdir -p /var/lib/rancher/k3s/server/etc",
                    task_id=task_id,
                )
                run_ssh_command(worker_ssh_client, scheduler_config, task_id=task_id)

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
                control_ssh_client, self.INTERNAL_IP_CMD, task_id=task_id
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

    def _remove_node(
        self,
        ssh_client,
        node_internal_ip: str,
        node_name: str,
        node_type: str,
        task_id: str,
    ):
        log.info(f"Removing {node_type} node {node_name} from the cluster")
        try:
            # Connect to the worker node through the control node
            worker_ssh_client = self._get_worker_ssh_client(
                ssh_client, node_internal_ip
            )
            try:
                # Remove the worker node from the cluster
                drain_command = f"kubectl drain {node_name} --ignore-daemonsets --delete-emptydir-data --force"
                run_ssh_command(ssh_client, drain_command, task_id=task_id)

                # Remove the worker node from the cluster
                delete_command = f"kubectl delete node {node_name}"
                run_ssh_command(ssh_client, delete_command, task_id=task_id)

                if node_type == "control_plane_node":
                    uninstall_command = f"/usr/local/bin/k3s-uninstall.sh"
                else:
                    uninstall_command = f"/usr/local/bin/k3s-agent-uninstall.sh"

                run_ssh_command(worker_ssh_client, uninstall_command, task_id=task_id)

                log.info(f"Worker node {node_name} uninstalled from the cluster")
                return {"message": "Worker node removed from the cluster successfully"}

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
        gpu_name: str,
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
            self._install_nvidia_drivers(
                ssh_connection, control_input, gpu_name, task_id
            )
            self._install_nvidia_container_runtime(
                ssh_connection, control_input, task_id
            )
            self._configure_nvidia_runtime(ssh_connection, control_input, task_id)

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

    def _get_ubuntu_version(self, ssh_client, task_id: str) -> str:
        """Get Ubuntu version from the system"""
        stdout, _ = run_ssh_command(
            ssh_client, "lsb_release -rs | grep -oE '[0-9]+\.[0-9]+'", task_id=task_id
        )
        return stdout.strip()

    def _install_nvidia_drivers(
        self,
        ssh_client,
        control_input: ControlMachineInput,
        gpu_name: str,
        task_id: str,
    ):
        log.info(f"Installing NVIDIA drivers on {control_input.hostname}")
        time.sleep(5)

        # Get Ubuntu version
        ubuntu_version = self._get_ubuntu_version(ssh_client, task_id)
        ubuntu_codename = f"ubuntu{ubuntu_version.replace('.','')}"

        # Install NVIDIA drivers
        nvidia_570_commands = [
            f"wget https://developer.download.nvidia.com/compute/cuda/repos/{ubuntu_codename}/x86_64/3bf863cc.pub",
            "apt-key add 3bf863cc.pub",
            f"echo 'deb https://developer.download.nvidia.com/compute/cuda/repos/{ubuntu_codename}/x86_64/ /' | tee /etc/apt/sources.list.d/nvidia-official-repo.list",
            "apt update",
            "apt-get install build-essential dkms linux-headers-$(uname -r) -y",
            "apt-get install nvidia-driver-570 -y",
        ]

        nvidia_5090_commands = [
            "apt install linux-headers-$(uname -r) -y",
            f"wget https://developer.download.nvidia.com/compute/cuda/repos/{ubuntu_codename}/x86_64/cuda-keyring_1.1-1_all.deb",
            "dpkg -i cuda-keyring_1.1-1_all.deb",
            "apt update",
            "apt install nvidia-open -y",
            "nvidia-smi",
        ]

        if gpu_name and gpu_name == "rtx5090":
            commands = nvidia_5090_commands
        else:
            commands = nvidia_570_commands

        for cmd in commands:
            run_ssh_command(ssh_client, cmd, task_id=task_id)

        log.info(f"NVIDIA drivers installed successfully on {control_input.hostname}")

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
        self,
        ssh_client,
        control_input: ControlMachineInput,
        node_type: str,
        task_id: str,
    ):
        log.info(f"Initiating reboot for node {control_input.hostname}")

        # Get appropriate SSH connection based on node type
        ssh_connection = (
            connect_to_worker_node(ssh_client, control_input)
            if node_type == "worker_node"
            else ssh_client
        )

        try:
            # Initiate reboot and wait for node to go down
            with ssh_connection:
                run_ssh_command(ssh_connection, "reboot", task_id=task_id)
                time.sleep(60)  # Wait for node to go down

            # Wait for node to come back online (max 5 minutes)
            max_attempts = 30
            retry_interval = 10

            for attempt in range(max_attempts):
                try:
                    # Get new SSH connection
                    new_connection = (
                        connect_to_worker_node(ssh_client, control_input)
                        if node_type == "worker_node"
                        else get_ssh_client(control_input)
                    )

                    # Verify node is up
                    with new_connection as client:
                        run_ssh_command(client, "uptime", task_id=task_id)
                        log.info(f"Node {control_input.hostname} is back online")
                        return

                except Exception:
                    if attempt < max_attempts - 1:
                        log.info(
                            f"Waiting for node {control_input.hostname} to come back online... "
                            f"(attempt {attempt + 1}/{max_attempts})"
                        )
                        time.sleep(retry_interval)
                    else:
                        raise Exception(
                            f"Node {control_input.hostname} did not come back online after reboot"
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

    def list_nodes(self, ssh_client):
        try:
            log.info("Listing nodes")
            # Get detailed node information in JSON format
            command = """kubectl get nodes -o json | jq '{
                    nodes: [.items[] | {
                        name: .metadata.name,
                        status: (
                        (.status.conditions // [] | map(select(.type == "Ready")) | .[0]?.status) // "Unknown"
                        ),
                        roles: (
                        [.metadata.labels | to_entries[]
                            | select(.key | startswith("node-role.kubernetes.io/"))
                            | .key
                            | sub("node-role.kubernetes.io/"; "")
                        ] | join(",")
                        ),
                        age: .metadata.creationTimestamp,
                        version: .status.nodeInfo.kubeletVersion,
                        internalIP: (
                        (.status.addresses[]? | select(.type == "InternalIP") | .address) // "N/A"
                        ),
                        externalIP: (
                        (.status.addresses[]? | select(.type == "ExternalIP") | .address) // "N/A"
                        ),
                        osImage: .status.nodeInfo.osImage,
                        kernelVersion: .status.nodeInfo.kernelVersion,
                        containerRuntime: .status.nodeInfo.containerRuntimeVersion
                    }]}'"""
            stdout, stderr = run_ssh_command(ssh_client, command)
            stdout = json.loads(stdout)
            return stdout
        except Exception as e:
            log.error(f"Error listing nodes: {str(e)}")
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="K3S_015",
                payload={
                    "error": "Node Listing Failed",
                    "message": f"Error listing nodes: {str(e)}",
                },
            )

    def _get_worker_ssh_client(self, control_ssh_client, node_internal_ip: str):
        """Create SSH client for worker node using key from control node."""
        worker_keyfile_content, _ = run_ssh_command(
            control_ssh_client, self.SSH_KEY_CMD, check_exit_status=True
        )
        worker_keyfile = UploadFile(
            filename="keyfile", file=io.BytesIO(worker_keyfile_content.encode())
        )

        worker_input = WorkerNodeInput(
            hostname=node_internal_ip,
            username="root",
            port=22,
            keyfile=worker_keyfile,
        )
        return self._connect_to_worker_node(control_ssh_client, worker_input)

    def _connect_to_worker_node(
        self, control_ssh_client, worker_input: WorkerNodeInput
    ):
        """Connect to worker node through control node."""
        return connect_to_worker_node(control_ssh_client, worker_input)

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
