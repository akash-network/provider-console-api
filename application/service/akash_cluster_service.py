from uuid import uuid4
from application.service.k3s_service import K3sService
from application.service.provider_service import ProviderService
from application.service.persistent_storage_service import PersistentStorageService
from application.model.provider_build_input import ProviderBuildInput
from application.service.task_manager import TaskManager, Task
from application.utils.logger import log
from application.data.wallet_addresses import store_wallet_action_mapping
from application.utils.ssh_utils import get_ssh_client
from application.config.config import Config


class AkashClusterService:
    def __init__(self):
        self.k3s_service = K3sService()
        self.provider_service = ProviderService()
        self.persistent_storage_service = PersistentStorageService()
        self.task_manager = TaskManager()

    async def create_akash_cluster(
        self,
        action_id: str,
        provider_build_input: ProviderBuildInput,
        wallet_address: str,
    ):
        log.info(f"Starting Akash cluster creation for action {action_id}")

        try:
            ssh_client = get_ssh_client(provider_build_input.nodes[0])
            try:
                k3s_tasks = self._create_k3s_tasks(
                    provider_build_input.nodes, ssh_client
                )
                provider_tasks = self._create_provider_tasks(
                    provider_build_input, wallet_address, ssh_client
                )
                self.task_manager.create_action(
                    action_id,
                    "Build Cluster",
                    k3s_tasks + provider_tasks,
                )
                store_wallet_action_mapping(wallet_address, action_id)
                await self.task_manager.run_action(action_id)
                log.info(f"Akash cluster creation completed for action {action_id}")
            finally:
                ssh_client.close()
        except Exception as e:
            log.error(
                f"Error during Akash cluster creation for action {action_id}: {str(e)}"
            )
            raise

    def _create_k3s_tasks(self, nodes, ssh_client):
        control_nodes = []
        worker_nodes = []

        if len(nodes) == 1:
            control_nodes = nodes
        elif len(nodes) < 5:
            control_nodes = [nodes[0]]
            worker_nodes = nodes[1:]
        else:
            control_nodes = nodes[:3]
            worker_nodes = nodes[3:]

        k3s_tasks = []

        # Tasks for the first control node (main control node)
        main_control_node = control_nodes[0]
        k3s_tasks.extend(
            [
                Task(
                    str(uuid4()),
                    "initialize_k3s_control",
                    "Initialize K3s on main control node",
                    self.k3s_service._initialize_k3s_control,
                    ssh_client,
                    main_control_node,
                ),
                Task(
                    str(uuid4()),
                    "update_dependencies",
                    "Update system and install dependencies",
                    self.k3s_service._update_and_install_dependencies,
                    ssh_client,
                ),
                Task(
                    str(uuid4()),
                    "install_calico",
                    "Install Calico CNI",
                    self.k3s_service._install_calico_cni,
                    ssh_client,
                ),
                Task(
                    str(uuid4()),
                    "update_kubeconfig",
                    "Update kubeconfig with external IP",
                    self.k3s_service._update_kubeconfig,
                    ssh_client,
                    main_control_node.hostname,
                ),
                Task(
                    str(uuid4()),
                    "update_coredns_config",
                    "Update CoreDNS configuration",
                    self.k3s_service._update_coredns_config,
                    ssh_client,
                ),
                Task(
                    str(uuid4()),
                    "create_and_label_namespaces",
                    "Create and label Kubernetes namespaces",
                    self.k3s_service._create_and_label_namespaces,
                    ssh_client,
                ),
            ]
        )

        # Tasks for additional control nodes
        for index, node in enumerate(control_nodes[1:], start=2):
            node_name = f"node{index}"
            k3s_tasks.append(
                Task(
                    str(uuid4()),
                    f"join_control_node_{node.hostname}",
                    f"Join control node {node.hostname} to the cluster",
                    self.k3s_service._join_control_node,
                    ssh_client,
                    node,
                    node_name,
                )
            )

        # Tasks for worker nodes
        for index, node in enumerate(worker_nodes, start=len(control_nodes) + 1):
            node_name = f"node{index}"
            k3s_tasks.append(
                Task(
                    str(uuid4()),
                    f"join_worker_node_{node.hostname}",
                    f"Join worker node {node.hostname} to the cluster",
                    self.k3s_service._join_worker_node,
                    ssh_client,
                    node,
                    node_name,
                )
            )

        # GPU driver installation for all nodes
        for i, node in enumerate(nodes):
            if node.install_gpu_drivers:
                node_type = "main_node" if i == 0 else "worker_node"
                k3s_tasks.append(
                    Task(
                        str(uuid4()),
                        f"install_gpu_drivers_{node.hostname}",
                        f"Install GPU drivers and toolkit on {node.hostname}",
                        self.k3s_service._install_gpu_drivers_and_toolkit,
                        ssh_client,
                        node,
                        node_type,
                    )
                )

        # k3s_tasks.extend(
        #     [
        #         Task(
        #             str(uuid4()),
        #             "check_akash_node_status",
        #             "Check Akash node status",
        #             self.k3s_service._check_akash_node_status,
        #             ssh_client,
        #         )
        #     ]
        # )

        return k3s_tasks

    def _create_provider_tasks(
        self, provider_build_input: ProviderBuildInput, wallet_address: str, ssh_client
    ):
        chain_id = Config.CHAIN_ID
        provider_version = Config.PROVIDER_SERVICES_VERSION.replace("v", "")
        node_version = Config.AKASH_VERSION.replace("v", "")
        key_password = provider_build_input.wallet.key_id
        domain = provider_build_input.provider.config.domain
        organization = provider_build_input.provider.config.organization
        attributes = provider_build_input.provider.attributes
        pricing = provider_build_input.provider.pricing
        email = provider_build_input.provider.config.email

        # Initialize an empty list to store nodes that require GPU driver installation
        install_gpu_driver_nodes = []

        # Loop through all nodes in provider_build_input
        for index, node in enumerate(provider_build_input.nodes):
            if node.install_gpu_drivers:
                node_name = f"node{index + 1}"
                install_gpu_driver_nodes.append(node_name)

        provider_tasks = [
            Task(
                str(uuid4()),
                "install_helm",
                "Install Helm",
                self.provider_service._install_helm,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "setup_helm_repos",
                "Set up Helm repositories",
                self.provider_service._setup_helm_repos,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "install_akash_services",
                "Install Akash services",
                self.provider_service._install_akash_services,
                ssh_client,
                chain_id,
                provider_version,
                node_version,
            ),
            Task(
                str(uuid4()),
                "prepare_provider_config",
                "Prepare provider configuration",
                self.provider_service._prepare_provider_config,
                ssh_client,
                wallet_address,
                key_password,
                domain,
                chain_id,
                attributes,
                organization,
                pricing,
                email,
            ),
            Task(
                str(uuid4()),
                "install_akash_crds",
                "Install Akash CRDs",
                self.provider_service._install_akash_crds,
                ssh_client,
                provider_version,
            ),
            Task(
                str(uuid4()),
                "install_akash_provider_service",
                "Install Akash provider service",
                self.provider_service._install_akash_provider,
                ssh_client,
                provider_version,
            ),
            Task(
                str(uuid4()),
                "install_nginx_ingress",
                "Install NGINX Ingress",
                self.provider_service._install_nginx_ingress,
                ssh_client,
            ),
        ]

        if install_gpu_driver_nodes and len(install_gpu_driver_nodes) > 0:
            provider_tasks.append(
                Task(
                    str(uuid4()),
                    "configure_gpu_support",
                    "Configure GPU support",
                    self.provider_service._configure_gpu_support,
                    ssh_client,
                    install_gpu_driver_nodes,
                )
            )

        return provider_tasks

    async def update_provider_attributes(
        self, action_id, control_machine, attributes, wallet_address
    ):
        ssh_client = get_ssh_client(control_machine)
        task = Task(
            str(uuid4()),
            "update_provider_attributes",
            "Update provider attributes",
            self.provider_service.update_provider_attributes,
            ssh_client,
            attributes,
        )
        self.task_manager.create_action(action_id, "Update Provider Attributes", [task])
        store_wallet_action_mapping(wallet_address, action_id)
        await self.task_manager.run_action(action_id)
        log.info(f"Provider attributes update completed for action {action_id}")

    async def update_provider_pricing(
        self, action_id, control_machine, pricing, wallet_address
    ):
        ssh_client = get_ssh_client(control_machine)
        task = Task(
            str(uuid4()),
            "update_provider_pricing",
            "Update provider pricing",
            self.provider_service.update_provider_pricing,
            ssh_client,
            pricing,
        )
        self.task_manager.create_action(action_id, "Update Provider Pricing", [task])
        store_wallet_action_mapping(wallet_address, action_id)
        await self.task_manager.run_action(action_id)

    async def update_provider_domain(
        self, action_id, control_machine, domain, wallet_address
    ):
        ssh_client = get_ssh_client(control_machine)
        task = Task(
            str(uuid4()),
            "update_provider_domain",
            "Update provider domain",
            self.provider_service.update_provider_domain,
            ssh_client,
            domain,
        )
        self.task_manager.create_action(action_id, "Update Provider Domain", [task])
        store_wallet_action_mapping(wallet_address, action_id)
        await self.task_manager.run_action(action_id)
        log.info(f"Provider domain update completed for action {action_id}")

    async def create_persistent_storage(
        self, action_id, control_machine, storage_info, wallet_address
    ):
        log.info(f"Starting persistent storage creation for action {action_id}")

        try:
            ssh_client = get_ssh_client(control_machine)
            try:
                log.info(
                    f"Getting unformatted drives for control machine {control_machine.hostname}"
                )
                persistent_storage_tasks = self._create_persistent_storage_tasks(
                    ssh_client, storage_info
                )

                self.task_manager.create_action(
                    action_id,
                    "Create Persistent Storage",
                    persistent_storage_tasks,
                )

                store_wallet_action_mapping(wallet_address, action_id)
                await self.task_manager.run_action(action_id)
            finally:
                ssh_client.close()
        except Exception as e:
            log.error(
                f"Error during persistent storage creation for action {action_id}: {str(e)}"
            )
            raise

    def _create_persistent_storage_tasks(self, ssh_client, storage_info):
        persistent_storage_tasks = [
            Task(
                str(uuid4()),
                "add_rook_helm_repo",
                "Add Rook-Ceph Helm repository",
                self.persistent_storage_service._add_rook_helm_repo,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "install_rook_operator",
                "Install Rook-Ceph operator",
                self.persistent_storage_service._install_rook_operator,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "setup_rook_ceph_values",
                "Setup Rook-Ceph cluster values",
                self.persistent_storage_service._setup_rook_ceph_values,
                ssh_client,
                storage_info,
            ),
            Task(
                str(uuid4()),
                "install_rook_cluster",
                "Install Rook-Ceph cluster",
                self.persistent_storage_service._install_rook_cluster,
                ssh_client,
            ),
            Task(
                str(uuid4()),
                "configure_storage_class",
                "Configure and label StorageClass for Akash",
                self.persistent_storage_service._configure_storage_class,
                ssh_client,
                storage_info,
            ),
        ]
        return persistent_storage_tasks

    def get_action_status(self, action_id: str):
        return self.task_manager.get_action_status(action_id)
