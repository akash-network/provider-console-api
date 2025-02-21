import base64
import json
import asyncio
from typing import Dict, Tuple, List, Optional

import requests
from fastapi import status
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

from application.exception.application_error import ApplicationError
from application.model.machine_input import ControlMachineInput, WorkerNodeInput
from application.utils.general import generate_random_string
from application.utils.ssh_utils import (
    get_ssh_client,
    run_ssh_command,
    connect_to_worker_node,
)
from application.utils.logger import log
from application.config.config import Config


class ClusterNodeService:
    def __init__(self):
        self.gpu_data_url = Config.GPU_DATA_URL

    async def verify_control_machine_connection(
        self, input: ControlMachineInput
    ) -> Dict:
        def ssh_operations():
            with self._get_ssh_client(input) as ssh_client:
                self._check_sudo_rights(ssh_client)
                system_info = self._gather_system_info(ssh_client)
                key_id, public_key = self._generate_and_store_key_pair(ssh_client)

                system_info["public_key"] = base64.b64encode(public_key).decode("utf-8")
                system_info["key_id"] = key_id
                return system_info
        
        system_info = await asyncio.to_thread(ssh_operations)
        log.info(f"Control machine verification result: {system_info}")
        return {"system_info": system_info}

    async def verify_worker_connection(
        self, control_input: ControlMachineInput, worker_input: WorkerNodeInput
    ) -> Dict:
        def ssh_operations():
            with self._get_ssh_client(control_input) as control_ssh_client:
                with self._connect_to_worker_node(
                    control_ssh_client, worker_input
                ) as worker_ssh_client:
                    system_info = self._gather_system_info(worker_ssh_client)
                    self._setup_ssh_keys(control_ssh_client, worker_ssh_client)
                    system_info["has_sudo"] = self._check_sudo_rights(worker_ssh_client)
                    return system_info

        system_info = await asyncio.to_thread(ssh_operations)
        log.info("Completed gathering worker node information")
        return {"system_info": system_info}

    def _get_ssh_client(self, input):
        return get_ssh_client(input)

    def _connect_to_worker_node(self, control_ssh_client, worker_input):
        return connect_to_worker_node(control_ssh_client, worker_input)

    def _gather_system_info(self, ssh_client) -> Dict:
        script = self._get_system_info_script()
        stdout, _ = run_ssh_command(ssh_client, script)

        try:
            system_info = json.loads(stdout)
            system_info["storage"] = self._process_storage_data(
                system_info.pop("storage_data")
            )
            self._add_gpu_info(system_info, ssh_client)
            self._enrich_gpu_data(system_info)
            return system_info
        except json.JSONDecodeError as e:
            raise self._create_application_error(
                "PARSE_001", f"Failed to parse system information: {str(e)}"
            )

    def _get_system_info_script(self) -> str:
        return """#!/bin/bash
set -e
cpu_info=$(lscpu | grep '^CPU(s):' | awk '{print $2}')
memory_total=$(free -h | grep Mem | awk '{print $2}')
gpu_count=$(lspci -nn | grep -Ei 'vga|3d' | sed -nE 's/.*\[(10de:[0-9a-f]+)\].*/\1/p' | wc -l)
public_ip=$(curl -s ifconfig.me)
private_ip=$(ip -4 -o a | while read -r line; do set -- $line; if echo "$4" | grep -qE '^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.)'; then echo "${4%/*}"; break; fi; done)
os_info=$(cat /etc/os-release | grep PRETTY_NAME | awk -F= '{print $2}' | sed 's/"//g')
storage_data=$(lsblk -e 7 -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT -bJ)

cat << EOF
{
  "cpus": "$cpu_info",
  "memory": "$memory_total",
  "gpus": "$gpu_count",
  "public_ip": "$public_ip",
  "private_ip": "$private_ip",
  "os": "$os_info",
  "storage_data": $storage_data
}
EOF"""

    def _check_sudo_rights(self, ssh_client) -> bool:
        try:
            stdout, stderr = run_ssh_command(
                ssh_client, "sudo -n true", check_exit_status=False
            )
            return stderr == ""
        except Exception:
            return False

    def _process_storage_data(self, storage_data: Dict) -> List[Dict]:
        return [
            self._process_device(device)
            for device in storage_data["blockdevices"]
            if self._should_include_device(device)
        ]

    def _should_include_device(self, device: Dict) -> bool:
        return (
            int(device["size"]) >= 1_000_000_000
            and device["type"] == "disk"
            and not device["name"].startswith(("loop", "rbd"))
        )

    def _process_device(self, device: Dict) -> Dict:
        processed = {
            "name": device["name"],
            "size": device["size"],
            "type": device["type"],
            "fstype": device.get("fstype"),
            "mountpoint": device.get("mountpoint"),
        }

        if "children" in device:
            children = [
                self._process_device(child)
                for child in device["children"]
                if int(child["size"]) >= 1_000_000_000
            ]
            if children:
                processed["children"] = children

        return processed

    def _add_gpu_info(self, system_info: Dict, ssh_client) -> None:
        lspci_command = """lspci -nn | grep -Ei 'vga|3d' | awk '/\\[(10de|1002):[0-9a-f]+\\]/ {print gensub(/.*\\[(10de:[0-9a-f]+)\\].*/, "\\\\1", "g")}' | sort | uniq"""
        stdout, stderr = run_ssh_command(
            ssh_client, lspci_command, check_exit_status=False
        )

        if stdout:
            log.info(f"lspci gpu command output: {stdout}")
            system_info["gpu_type"] = stdout
        else:
            log.error(f"Error executing lspci command: {stderr}")

    def _enrich_gpu_data(self, system_info: Dict) -> None:
        gpu_info = self._initialize_gpu_info(system_info)
        gpu_type = system_info.pop("gpu_type", None)

        if not gpu_type or gpu_type in ["multiple", ""]:
            system_info["gpu"] = gpu_info
            return

        try:
            gpu_data = self._fetch_gpu_data()
            vendor_id, device_id = gpu_type.split(":")
            vendor_key = self._get_vendor_key(vendor_id)

            if vendor_key:
                self._update_gpu_info(
                    gpu_info, gpu_data, vendor_id, device_id, vendor_key
                )
            else:
                log.warning(f"Unsupported GPU vendor ID: {vendor_id}")
        except Exception as e:
            log.error(f"Error processing GPU data: {str(e)}")

        system_info["gpu"] = gpu_info

    def _initialize_gpu_info(self, system_info: Dict) -> Dict:
        return {
            "count": int(system_info.pop("gpus", 0)),
            "vendor": None,
            "name": None,
            "memory_size": None,
            "interface": None,
        }

    def _fetch_gpu_data(self) -> Dict:
        response = requests.get(self.gpu_data_url)
        response.raise_for_status()
        return response.json()

    def _get_vendor_key(self, vendor_id: str) -> Optional[str]:
        return (
            "amd" if vendor_id == "1002" else "nvidia" if vendor_id == "10de" else None
        )

    def _update_gpu_info(
        self,
        gpu_info: Dict,
        gpu_data: Dict,
        vendor_id: str,
        device_id: str,
        vendor_key: str,
    ) -> None:
        devices = gpu_data.get(vendor_id, {}).get("devices", {})
        matching_device = devices.get(device_id)

        if matching_device:
            gpu_info["vendor"] = vendor_key.capitalize()
            gpu_info["name"] = matching_device.get("name", "Unknown GPU")
            gpu_info["memory_size"] = matching_device.get("memory_size", "Unknown RAM")
            gpu_info["interface"] = matching_device.get(
                "interface", "Unknown Architecture"
            )
        else:
            log.warning(f"GPU device ID {device_id} not found in the data")

    def _generate_and_store_key_pair(self, ssh_client) -> Tuple[str, bytes]:
        key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        key_id = generate_random_string()
        public_key = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )

        self._store_key_pair(ssh_client, key_id, pem, public_key)
        return key_id, public_key

    def _store_key_pair(
        self, ssh_client, key_id: str, pem: bytes, public_key: bytes
    ) -> None:
        ssh_dir = "~/.ssh"
        private_key_path = f"{ssh_dir}/{key_id}"
        public_key_path = f"{private_key_path}.pub"

        run_ssh_command(ssh_client, f"mkdir -p {ssh_dir}")
        run_ssh_command(ssh_client, f"echo '{pem.decode()}' > {private_key_path}")
        run_ssh_command(ssh_client, f"chmod 600 {private_key_path}")
        run_ssh_command(ssh_client, f"echo '{public_key.decode()}' > {public_key_path}")

    def _setup_ssh_keys(self, control_ssh_client, worker_ssh_client) -> None:
        # Check if ed25519 key exists on control machine
        check_key_cmd = "test -f ~/.ssh/id_ed25519 && echo 'exists' || echo 'not found'"
        stdout, _ = run_ssh_command(control_ssh_client, check_key_cmd)

        if "not found" in stdout:
            log.info("Generating new ed25519 key pair on control machine")
            run_ssh_command(
                control_ssh_client, "ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ''"
            )

        # Ensure authorized_keys file exists and has correct permissions
        run_ssh_command(worker_ssh_client, "mkdir -p ~/.ssh && chmod 700 ~/.ssh")

        # Get public key from control machine and add it to worker's authorized_keys
        stdout, _ = run_ssh_command(control_ssh_client, "cat ~/.ssh/id_ed25519.pub")
        run_ssh_command(
            worker_ssh_client,
            f"echo '{stdout.strip()}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys",
        )
        log.info(
            "Added control machine's ed25519 public key to worker's authorized_keys"
        )

    def _create_application_error(
        self, error_code: str, message: str
    ) -> ApplicationError:
        return ApplicationError(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code=error_code,
            payload={"message": message},
        )

    def _handle_error(self, node_type: str, error: Exception) -> None:
        log.error(f"Unexpected error during {node_type} verification: {str(error)}")
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code=f"VER_{'001' if node_type == 'control machine' else '002'}",
            payload={
                "message": f"Unexpected error during {node_type} verification: {str(error)}"
            },
        )
