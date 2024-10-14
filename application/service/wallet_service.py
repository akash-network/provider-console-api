import time
from fastapi import status
from paramiko import SSHException, ChannelFile

from application.config.config import Config
from application.exception.application_error import ApplicationError
from application.model.machine_input import ControlMachineInput
from application.model.provider_build_input import Wallet
from application.utils.logger import log
from application.utils.ssh_utils import get_ssh_client, run_ssh_command


class WalletService:
    def __init__(self):
        self.ssh_client = None

    def import_wallet(self, control_input: ControlMachineInput, wallet: Wallet) -> dict:
        try:
            with self._get_ssh_connection(control_input):
                mnemonic = self._decrypt_wallet_mnemonic(wallet)
                self._install_and_verify_provider_services()
                self._import_wallet_with_mnemonic(mnemonic, wallet.key_id)
                self._export_and_store_key(wallet.key_id)

            log.info(f"Wallet imported successfully for key_id: {wallet.key_id}")
            return {"success": True, "message": "Wallet imported successfully"}

        except ApplicationError:
            raise
        except Exception as e:
            self._handle_import_error(e)

    def _get_ssh_connection(self, control_input):
        class SSHClientContextManager:
            def __init__(self, service):
                self.service = service

            def __enter__(self):
                self.service.ssh_client = get_ssh_client(control_input)
                return self.service.ssh_client

            def __exit__(self, exc_type, exc_val, exc_tb):
                if self.service.ssh_client:
                    self.service.ssh_client.close()
                self.service.ssh_client = None

        return SSHClientContextManager(self)

    def _decrypt_wallet_mnemonic(self, wallet: Wallet) -> str:
        private_key_path = f"~/.ssh/{wallet.key_id}"
        temp_encrypted_file = f"/tmp/encrypted_phrase_{wallet.key_id}"
        temp_decrypted_file = f"/tmp/decrypted_phrase_{wallet.key_id}"

        try:
            run_ssh_command(
                self.ssh_client,
                f"echo '{wallet.wallet_phrase}' | base64 -d > {temp_encrypted_file}",
            )
            decrypt_command = f"openssl pkeyutl -decrypt -inkey {private_key_path} -passin pass:{wallet.key_id} -in {temp_encrypted_file} -out {temp_decrypted_file}"
            _, stderr_output = run_ssh_command(self.ssh_client, decrypt_command)

            if stderr_output:
                raise ApplicationError(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_code="WAL_004",
                    payload={
                        "error": "Wallet Decryption Error",
                        "message": "Failed to decrypt wallet mnemonic",
                    },
                )

            decrypted_phrase, _ = run_ssh_command(
                self.ssh_client, f"cat {temp_decrypted_file}"
            )
            return decrypted_phrase.strip()
        finally:
            self._cleanup_temp_files(temp_encrypted_file, temp_decrypted_file)

    def _cleanup_temp_files(self, *files):
        cleanup_command = f"rm -f {' '.join(files)}"
        try:
            run_ssh_command(self.ssh_client, cleanup_command)
            log.info("Temporary files cleaned up successfully")
        except Exception as e:
            log.warning(f"Error cleaning up temporary files: {str(e)}")

    def _import_wallet_with_mnemonic(self, mnemonic: str, key_id: str) -> None:
        try:
            run_ssh_command(self.ssh_client, "rm -rf ~/.akash/keyring-file")
            log.info("Removed existing keyring folder")

            command = f"~/bin/provider-services keys add provider --recover --keyring-backend {Config.KEYRING_BACKEND}"
            stdin, stdout, stderr = self.ssh_client.exec_command(command, get_pty=True)

            prompts_and_responses = {
                "Enter your bip39 mnemonic": f"{mnemonic}\n",
                "Enter keyring passphrase:": f"{key_id}\n",
                "Re-enter keyring passphrase:": f"{key_id}\n",
                "override the existing name": "y\n",
            }

            self._handle_prompts(stdin, stdout, prompts_and_responses)

            if stdout.channel.recv_exit_status() != 0:
                raise ApplicationError(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_code="WAL_007",
                    payload={
                        "error": "Wallet Import Error",
                        "message": f"Failed to import wallet: {stderr.read().decode('utf-8').strip()}",
                    },
                )

            log.info("Wallet imported successfully using mnemonic")

        except SSHException as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="WAL_008",
                payload={
                    "error": "Wallet Import Error",
                    "message": f"SSH error during wallet import: {str(e)}",
                },
            )
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="WAL_009",
                payload={
                    "error": "Wallet Import Error",
                    "message": f"Error during wallet import: {str(e)}",
                },
            )

    def _handle_prompts(
        self, stdin: ChannelFile, stdout: ChannelFile, prompts_and_responses: dict
    ) -> None:
        timeout = 60
        start_time = time.time()

        while prompts_and_responses and (time.time() - start_time < timeout):
            if stdout.channel.recv_ready():
                output = stdout.channel.recv(1024).decode("utf-8")
                log.debug(f"Received output: {output}")

                for prompt, response in list(prompts_and_responses.items()):
                    if prompt in output:
                        log.info(f"Responding to prompt: {prompt}")
                        stdin.write(response)
                        stdin.flush()
                        del prompts_and_responses[prompt]
                        break

            if stdout.channel.exit_status_ready():
                break

            time.sleep(0.1)

        if prompts_and_responses:
            log.warning(
                f"Some prompts were not handled: {', '.join(prompts_and_responses.keys())}"
            )

        if time.time() - start_time >= timeout:
            raise ApplicationError(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                error_code="WAL_011",
                payload={
                    "error": "Wallet Import Timeout",
                    "message": "Timed out while handling prompts during wallet import",
                },
            )

    def _export_and_store_key(self, key_id: str) -> None:
        try:
            export_command = f"~/bin/provider-services keys export provider --keyring-backend {Config.KEYRING_BACKEND}"
            stdin, stdout, stderr = self.ssh_client.exec_command(
                export_command, get_pty=True
            )

            prompts_and_responses = {
                "Enter passphrase to encrypt the exported key:": f"{key_id}\n",
                "Enter keyring passphrase:": f"{key_id}\n",
            }

            self._handle_prompts(stdin, stdout, prompts_and_responses)

            exported_key = stdout.read().decode("utf-8").strip()

            if not exported_key:
                raise ApplicationError(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_code="WAL_010",
                    payload={
                        "error": "Key Export Error",
                        "message": "Failed to export the key",
                    },
                )

            run_ssh_command(self.ssh_client, "rm -f ~/key.pem")
            store_command = f"cat > ~/key.pem << EOF\n{exported_key}\nEOF"
            run_ssh_command(self.ssh_client, store_command)
            log.info("Key exported and stored successfully in ~/key.pem")

        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="WAL_011",
                payload={
                    "error": "Key Export and Storage Error",
                    "message": f"Error during key export and storage: {str(e)}",
                },
            )

    def _install_and_verify_provider_services(self) -> None:
        try:
            log.info("Installing provider-services...")

            commands = [
                "apt-get install -y unzip",
                f"curl https://raw.githubusercontent.com/akash-network/provider/main/install.sh | bash -s -- {Config.PROVIDER_SERVICES_VERSION}",
            ]

            for command in commands:
                run_ssh_command(self.ssh_client, command)

            log.info("Validating provider-services installation...")
            _, version_output = run_ssh_command(
                self.ssh_client, "~/bin/provider-services version"
            )

            if not version_output.strip().startswith("v"):
                raise ApplicationError(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_code="WAL_005",
                    payload={
                        "error": "Provider Services Installation Error",
                        "message": "Provider-services installation failed or not accessible in the PATH.",
                    },
                )

            log.info(
                f"Provider-services is successfully installed. Version: {version_output.strip()}"
            )
        except Exception as e:
            raise ApplicationError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="WAL_006",
                payload={
                    "error": "Provider Services Installation Error",
                    "message": f"Error during provider-services installation: {str(e)}",
                },
            )

    def _handle_import_error(self, e):
        error_message = (
            str(e.payload["message"]) if isinstance(e, ApplicationError) else str(e)
        )
        log.error(f"Error importing wallet: {error_message}")
        raise ApplicationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="WAL_003",
            payload={
                "error": "Wallet Import Error",
                "message": f"Error importing wallet: {error_message}",
            },
        )
