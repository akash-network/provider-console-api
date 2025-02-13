from fastapi import UploadFile
import json
import io
from typing import Dict, Any

from application.config.config import Config
from application.model.machine_input import ControlMachineInput, WorkerNodeInput
from application.utils.ssh_utils import (
    get_ssh_client,
    connect_to_worker_node,
    run_ssh_command,
)
from application.utils.logger import log


class PersistentStorageService:
    # Command constants
    KUBECTL_NODES_CMD = """kubectl get nodes -o json | jq '[.items[] | {name: .metadata.name, internal_ip: (.status.addresses[] | select(.type=="InternalIP") | .address)}]' | jq -c ."""
    STORAGE_INFO_CMD = "lsblk -e 7 -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,ROTA -bJ"
    SSH_KEY_CMD = "cat ~/.ssh/id_ed25519"
    MIN_DRIVE_SIZE = 64424509440  # 60 gibibyte to bytes

    def get_unformatted_drives(
        self, control_machine_input: ControlMachineInput
    ) -> Dict[str, Any]:
        """
        Get list of nodes and their unformatted drives using SSH.

        Args:
            control_machine_input: Input parameters for control machine connection

        Returns:
            Dict containing storage information for each node
        """
        storage_info = {}

        try:
            control_ssh_client = self._get_ssh_client(control_machine_input)

            # Get nodes information
            stdout, _ = run_ssh_command(control_ssh_client, self.KUBECTL_NODES_CMD)
            nodes = json.loads(stdout)

            # Get control node storage info
            stdout, _ = run_ssh_command(
                control_ssh_client, self.STORAGE_INFO_CMD, check_exit_status=True
            )
            filtered_storage = self._filter_unformatted_drives(json.loads(stdout))
            if filtered_storage:
                storage_info["node1"] = filtered_storage

            # Process worker nodes
            self._process_worker_nodes(control_ssh_client, nodes[1:], storage_info)

            return storage_info

        except Exception as e:
            log.error(f"Failed to get unformatted drives: {str(e)}")
            raise

    def _process_worker_nodes(
        self, control_ssh_client, nodes: list, storage_info: dict
    ) -> None:
        """Process each worker node to get storage information."""
        for node in nodes:
            worker_ssh_client = None
            try:
                worker_ssh_client = self._get_worker_ssh_client(
                    control_ssh_client, node
                )
                stdout, _ = run_ssh_command(
                    worker_ssh_client, self.STORAGE_INFO_CMD, check_exit_status=True
                )
                filtered_storage = self._filter_unformatted_drives(json.loads(stdout))
                if filtered_storage:
                    storage_info[node["name"]] = filtered_storage

            except Exception as e:
                log.error(
                    f"Failed to connect to {node['name']} at {node['internal_ip']}: {str(e)}"
                )

            finally:
                if worker_ssh_client:
                    worker_ssh_client.close()

    def _get_worker_ssh_client(self, control_ssh_client, node: dict):
        """Create SSH client for worker node using key from control node."""
        worker_keyfile_content, _ = run_ssh_command(
            control_ssh_client, self.SSH_KEY_CMD, check_exit_status=True
        )
        worker_keyfile = UploadFile(
            filename="keyfile", file=io.BytesIO(worker_keyfile_content.encode())
        )

        worker_input = WorkerNodeInput(
            hostname=node["internal_ip"],
            username="root",
            port=22,
            keyfile=worker_keyfile,
        )
        return self._connect_to_worker_node(control_ssh_client, worker_input)

    def _get_ssh_client(self, input: ControlMachineInput):
        """Get SSH client for control machine."""
        return get_ssh_client(input)

    def _connect_to_worker_node(
        self, control_ssh_client, worker_input: WorkerNodeInput
    ):
        """Connect to worker node through control node."""
        return connect_to_worker_node(control_ssh_client, worker_input)

    def _filter_unformatted_drives(self, storage_data: dict) -> dict:
        filtered_devices = {
            "blockdevices": [
                {
                    **device,
                    "storage_type": (
                        "nvme"
                        if "nvme" in device["name"]
                        else "ssd" if device["rota"] == 0 else "hdd"
                    ),
                }
                for device in storage_data["blockdevices"]
                if (
                    "children" not in device
                    and device["fstype"] is None
                    and device["type"] == "disk"
                    and device["size"] > self.MIN_DRIVE_SIZE
                    and device["mountpoint"] is None
                )
            ]
        }
        return filtered_devices if filtered_devices["blockdevices"] else None

    def _add_rook_helm_repo(self, ssh_client, task_id: str):
        """Add Rook-Ceph Helm repository to the cluster."""
        try:
            run_ssh_command(
                ssh_client,
                "helm repo add rook-release https://charts.rook.io/release",
                check_exit_status=True,
                task_id=task_id,
            )
            run_ssh_command(
                ssh_client, "helm repo update", check_exit_status=True, task_id=task_id
            )
            log.info("Rook-Ceph Helm repository added successfully")
        except Exception as e:
            log.error(f"Failed to add Rook-Ceph Helm repository: {str(e)}")
            raise

    def _install_rook_operator(self, ssh_client, task_id: str):
        """Install Rook-Ceph operator using Helm."""
        try:
            cmd = f"helm install --create-namespace -n rook-ceph rook-ceph rook-release/rook-ceph --version {Config.ROOK_CEPH_VERSION}"
            run_ssh_command(ssh_client, cmd, check_exit_status=True, task_id=task_id)
            log.info("Rook-Ceph operator installed successfully")
        except Exception as e:
            log.error(f"Failed to install Rook-Ceph operator: {str(e)}")
            raise

    def _setup_rook_ceph_values(self, ssh_client, storage_info: dict, task_id: str):
        log.info("Setting up Rook-Ceph cluster values...")

        nodes = storage_info["nodes"]
        is_single_node = len(nodes) == 1

        # Generate nodes configuration dynamically
        nodes_config = []
        for node_data in nodes:
            node_config = {
                "name": node_data["node"],
                "devices": [
                    {"name": f"/dev/{drive['device']}"} for drive in node_data["drives"]
                ],
                "config": {},  # Empty config as per the desired structure
            }
            nodes_config.append(node_config)

        # Convert nodes config to YAML-style string
        nodes_yaml = "\n".join(
            [
                f"    - name: \"{node['name']}\"\n"
                f"      devices:\n"
                + "\n".join(
                    [f"        - name: {device['name']}" for device in node["devices"]]
                )
                + "\n      config:"
                for node in nodes_config
            ]
        )

        # Set configuration based on number of nodes
        values_content = f"""operatorNamespace: rook-ceph

configOverride: |
  [global]
  osd_pool_default_pg_autoscale_mode = on
  osd_pool_default_size = {1 if is_single_node else 3}
  osd_pool_default_min_size = {1 if is_single_node else 2}

cephClusterSpec:
  mon:
    count: {1 if is_single_node else 3}
  mgr:
    count: {1 if is_single_node else 2}

  storage:
    useAllNodes: false
    useAllDevices: false
    config:
      osdsPerDevice: "{1 if is_single_node else 2}"
    nodes:
{nodes_yaml}

cephBlockPools:
  - name: akash-deployments
    spec:
      failureDomain: host
      replicated:
        size: {1 if is_single_node else 3}
      parameters:
        min_size: "{1 if is_single_node else 2}"
        bulk: "true"
    storageClass:
      enabled: true
      name: {storage_info['storage_class']}
      isDefault: true
      reclaimPolicy: Delete
      allowVolumeExpansion: true
      parameters:
        imageFormat: "2"
        imageFeatures: layering
        csi.storage.k8s.io/provisioner-secret-name: rook-csi-rbd-provisioner
        csi.storage.k8s.io/provisioner-secret-namespace: rook-ceph
        csi.storage.k8s.io/controller-expand-secret-name: rook-csi-rbd-provisioner
        csi.storage.k8s.io/controller-expand-secret-namespace: rook-ceph
        csi.storage.k8s.io/node-stage-secret-name: rook-csi-rbd-node
        csi.storage.k8s.io/node-stage-secret-namespace: rook-ceph
        csi.storage.k8s.io/fstype: ext4

cephFileSystems:
cephObjectStores:

toolbox:
  enabled: true"""

        # Check if file exists
        check_command = "rm -rf ~/provider/rook-ceph-cluster.values.yml"
        run_ssh_command(ssh_client, check_command, task_id=task_id)

        log.info("Creating Rook-Ceph cluster values file...")
        create_command = f"cat > ~/provider/rook-ceph-cluster.values.yml << EOF\n{values_content}\nEOF"
        run_ssh_command(ssh_client, create_command, task_id=task_id)
        log.info("Rook-Ceph cluster values file created successfully.")

    def _install_rook_cluster(self, ssh_client, task_id: str):
        """Install Rook-Ceph cluster using Helm."""
        try:

            cmd = f"""helm install --create-namespace -n rook-ceph rook-ceph-cluster \
                --set operatorNamespace=rook-ceph rook-release/rook-ceph-cluster \
                --version {Config.ROOK_CEPH_VERSION} -f ~/provider/rook-ceph-cluster.values.yml"""
            run_ssh_command(ssh_client, cmd, check_exit_status=True, task_id=task_id)
            log.info("Rook-Ceph cluster installed successfully")
        except Exception as e:
            log.error(f"Failed to install Rook-Ceph cluster: {str(e)}")
            raise

    def _configure_storage_class(self, ssh_client, storage_info: dict, task_id: str):
        """Configure storage class for Akash."""
        try:
            # Label the storage class for Akash integration
            label_cmd = (
                f"kubectl label sc {storage_info['storage_class']} akash.network=true"
            )
            run_ssh_command(
                ssh_client, label_cmd, check_exit_status=True, task_id=task_id
            )
            log.info(
                f"StorageClass {storage_info['storage_class']} labeled for Akash integration"
            )

            # Label the node with storage capabilities
            node_label_cmd = f"""kubectl label node node1 \
                akash.network/capabilities.storage.class.{storage_info['storage_class']}=1 \
                akash.network/capabilities.storage.class.default=1 \
                --overwrite"""
            run_ssh_command(
                ssh_client, node_label_cmd, check_exit_status=True, task_id=task_id
            )
            log.info("Node labeled with storage capabilities")

            # Update the inventory operator with the new storage class
            update_cmd = f"""helm upgrade inventory-operator akash/akash-inventory-operator -n akash-services \
                --set inventoryConfig.cluster_storage[0]=default,inventoryConfig.cluster_storage[1]={storage_info['storage_class']},inventoryConfig.cluster_storage[2]=ram"""
            run_ssh_command(
                ssh_client, update_cmd, check_exit_status=True, task_id=task_id
            )

            log.info(
                f"Inventory operator updated to use storage class {storage_info['storage_class']}"
            )
        except Exception as e:
            log.error(f"Failed to configure storage class: {str(e)}")
            raise
