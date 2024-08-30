import os
import logging
from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.sql import SqlManagementClient
from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.sql.models import Server, Database
from azure.mgmt.compute.models import (
    VirtualMachine, HardwareProfile, StorageProfile, OSDisk, ImageReference,
    OSProfile, NetworkProfile, NetworkInterfaceReference, DiskCreateOptionTypes
)
from azure.mgmt.storage.models import StorageAccountCreateParameters, Sku, Kind
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError, HttpResponseError
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.network.models import (
    NetworkSecurityGroup, SecurityRule, VirtualNetwork, Subnet, PublicIPAddress, NetworkInterface, NetworkInterfaceIPConfiguration
)
import random
import string

# Configure logging
logging.basicConfig(level=logging.INFO, filename='azure_management.log', filemode='w',
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize Azure credentials and clients
credential = DefaultAzureCredential()
subscription_id = os.getenv('AZURE_SUBSCRIPTION_ID') or "your_subscription_id"

if not subscription_id:
    raise ValueError("Azure subscription ID is not set. Please set the AZURE_SUBSCRIPTION_ID environment variable.")

resource_client = ResourceManagementClient(credential, subscription_id)
compute_client = ComputeManagementClient(credential, subscription_id)
sql_client = SqlManagementClient(credential, subscription_id)
storage_client = StorageManagementClient(credential, subscription_id)
network_client = NetworkManagementClient(credential, subscription_id)

# Constants
RESOURCE_GROUP_NAME = 'MyResourceGroup'
LOCATION = 'centralus'
VM_NAME = 'MyVM'
STORAGE_ACCOUNT_NAME = 'mystorageacct'
SQL_SERVER_NAME = 'myserver'
SQL_DB_NAME = 'mydatabase'
VNET_NAME = 'MyVNet'
SUBNET_NAME = 'MySubnet'
IP_NAME = 'MyPublicIP'
NIC_NAME = 'MyVMNIC'

def create_resource_group():
    try:
        # Check if the resource group already exists
        logging.info(f"Checking if resource group '{RESOURCE_GROUP_NAME}' exists...")
        rg_exists = resource_client.resource_groups.check_existence(RESOURCE_GROUP_NAME)
        
        if rg_exists:
            logging.info(f"Resource group '{RESOURCE_GROUP_NAME}' already exists. Using existing resource group.")
        else:
            logging.info("Creating new resource group...")
            rg_result = resource_client.resource_groups.create_or_update(
                RESOURCE_GROUP_NAME,
                {"location": LOCATION}
            )
            logging.info(f"Resource group '{RESOURCE_GROUP_NAME}' created successfully.")
    except Exception as e:
        logging.error(f"Error creating or using resource group: {e}")
        raise

def create_virtual_network():
    logging.info("Creating virtual network and subnet...")
    try:
        vnet_params = VirtualNetwork(
            location=LOCATION,
            address_space={"address_prefixes": ["10.0.0.0/16"]}
        )
        vnet_result = network_client.virtual_networks.begin_create_or_update(
            RESOURCE_GROUP_NAME, VNET_NAME, vnet_params).result()

        subnet_params = Subnet(
            address_prefix="10.0.0.0/24"
        )
        subnet_result = network_client.subnets.begin_create_or_update(
            RESOURCE_GROUP_NAME, VNET_NAME, SUBNET_NAME, subnet_params).result()

        logging.info(f"Virtual network '{VNET_NAME}' and subnet '{SUBNET_NAME}' created successfully.")
    except HttpResponseError as e:
        logging.error(f"Error creating virtual network or subnet: {e.message}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error occurred: {e}")
        raise

def create_public_ip():
    logging.info("Creating public IP address...")
    try:
        ip_params = PublicIPAddress(
            location=LOCATION,
            public_ip_allocation_method="Dynamic"
        )
        ip_result = network_client.public_ip_addresses.begin_create_or_update(
            RESOURCE_GROUP_NAME, IP_NAME, ip_params).result()

        logging.info(f"Public IP address '{IP_NAME}' creation initiated.")

        # Verify the public IP address has been created
        for _ in range(10):
            try:
                ip_info = network_client.public_ip_addresses.get(RESOURCE_GROUP_NAME, IP_NAME)
                logging.info(f"Public IP address '{IP_NAME}' has been created successfully.")
                break
            except ResourceNotFoundError:
                logging.warning(f"Public IP address '{IP_NAME}' not found yet. Retrying...")
                time.sleep(5)
        else:
            raise Exception(f"Public IP address '{IP_NAME}' could not be verified after creation.")

    except HttpResponseError as e:
        logging.error(f"Error creating public IP address: {e.message}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error occurred: {e}")
        raise

def create_network_interface():
    logging.info("Creating network interface...")
    try:
        subnet_info = network_client.subnets.get(
            RESOURCE_GROUP_NAME, VNET_NAME, SUBNET_NAME)

        ip_info = network_client.public_ip_addresses.get(
            RESOURCE_GROUP_NAME, IP_NAME)

        nic_params = NetworkInterface(
            location=LOCATION,
            ip_configurations=[
                NetworkInterfaceIPConfiguration(
                    name=NIC_NAME,
                    subnet=subnet_info,
                    public_ip_address=ip_info,
                    primary=True
                )
            ]
        )
        nic_result = network_client.network_interfaces.begin_create_or_update(
            RESOURCE_GROUP_NAME, NIC_NAME, nic_params).result()

        logging.info(f"Network interface '{NIC_NAME}' created successfully.")
    except Exception as e:
        logging.error(f"Error creating network interface: {e}")
        raise

def get_available_vm_size(location, preferred_size="Standard_D2a_v4"):
    logging.info(f"Checking availability of VM size '{preferred_size}' in location '{location}'...")
    available_sizes = compute_client.virtual_machine_sizes.list(location)
    available_size_names = [size.name for size in available_sizes]
    
    if preferred_size in available_size_names:
        logging.info(f"Preferred VM size '{preferred_size}' is available.")
        return preferred_size
    else:
        logging.warning(f"Preferred VM size '{preferred_size}' is not available. Selecting an alternative size.")
        return available_size_names[0] if available_size_names else None

def create_virtual_machine():
    try:
        create_resource_group()
        create_virtual_network()
        create_public_ip()
        create_network_interface()

        logging.info("Deploying virtual machine...")

        vm_size = get_available_vm_size(LOCATION, "Standard_B1s")
        if not vm_size:
            raise Exception("No available VM sizes found in the specified location.")

        vm_parameters = {
            "location": LOCATION,
            "hardware_profile": HardwareProfile(vm_size=vm_size),
            "storage_profile": StorageProfile(
                image_reference=ImageReference(
                    publisher="Canonical",
                    offer="UbuntuServer",
                    sku="18.04-LTS",
                    version="latest"
                ),
                os_disk=OSDisk(
                    create_option=DiskCreateOptionTypes.from_image,
                    name=f'{VM_NAME}_osdisk'
                )
            ),
            "os_profile": OSProfile(
                computer_name=VM_NAME,
                admin_username="azureuser",
                admin_password="P@ssw0rd1234"
            ),
            "network_profile": NetworkProfile(
                network_interfaces=[
                    NetworkInterfaceReference(
                        id=f'/subscriptions/{subscription_id}/resourceGroups/{RESOURCE_GROUP_NAME}/providers/Microsoft.Network/networkInterfaces/{NIC_NAME}',
                        primary=True
                    )
                ]
            )
        }

        # Attempt to create the VM
        try:
            vm_result = compute_client.virtual_machines.begin_create_or_update(
                RESOURCE_GROUP_NAME, VM_NAME, vm_parameters).result()
            logging.info(f"VM '{VM_NAME}' deployed successfully with size '{vm_size}'.")
        except ResourceExistsError as e:
            logging.error(f"Resource exists error while creating VM: {e}")
            raise
        except HttpResponseError as e:
            logging.error(f"Failed to deploy VM, retrying due to error: {e}")
            raise

        # Verify VM creation
        for _ in range(10):
            try:
                vm_info = compute_client.virtual_machines.get(RESOURCE_GROUP_NAME, VM_NAME)
                logging.info(f"VM '{VM_NAME}' has been created successfully.")
                break
            except ResourceNotFoundError:
                logging.warning(f"VM '{VM_NAME}' not found yet. Retrying...")
                time.sleep(5)
        else:
            raise Exception(f"VM '{VM_NAME}' could not be verified after creation.")

    except Exception as e:
        logging.error(f"Error deploying VM: {e}")
        raise

# Set up Azure SQL Database
def generate_unique_name(base_name):
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{base_name}{suffix}"

def setup_sql_database():
    try:
        logging.info("Setting up SQL database...")

        # Generate a unique SQL Server name
        unique_sql_server_name = generate_unique_name(SQL_SERVER_NAME)
        logging.info(f"Generated unique SQL Server name: {unique_sql_server_name}")

        # Create SQL Server
        server_params = Server(
            location=LOCATION,
            administrator_login="sqladmin",
            administrator_login_password="P@ssw0rd1234"
        )

        server_result = sql_client.servers.begin_create_or_update(
            RESOURCE_GROUP_NAME, unique_sql_server_name, server_params).result()

        logging.info(f"SQL Server '{unique_sql_server_name}' created successfully.")

        # Verify the SQL Server exists before proceeding
        for _ in range(10):
            try:
                sql_client.servers.get(RESOURCE_GROUP_NAME, unique_sql_server_name)
                logging.info(f"SQL Server '{unique_sql_server_name}' has been verified successfully.")
                break
            except ResourceNotFoundError:
                logging.warning(f"SQL Server '{unique_sql_server_name}' not found yet. Retrying...")
                time.sleep(5)
        else:
            raise Exception(f"SQL Server '{unique_sql_server_name}' could not be verified after creation.")

        # Create SQL Database
        db_params = Database(location=LOCATION)
        db_result = sql_client.databases.begin_create_or_update(
            RESOURCE_GROUP_NAME, unique_sql_server_name, SQL_DB_NAME, db_params).result()

        logging.info(f"SQL Database '{SQL_DB_NAME}' setup successfully.")
    except HttpResponseError as e:
        logging.error(f"Failed to set up SQL database: {e.message}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error occurred: {e}")
        raise

# Configure Storage Account
def configure_storage_account():
    try:
        logging.info("Configuring storage account...")

        # Generate a unique storage account name
        unique_storage_account_name = generate_unique_name(STORAGE_ACCOUNT_NAME)
        logging.info(f"Generated unique storage account name: {unique_storage_account_name}")

        storage_params = StorageAccountCreateParameters(
            sku=Sku(name="Standard_LRS"),
            kind=Kind.STORAGE_V2,
            location=LOCATION
        )

        storage_account_result = storage_client.storage_accounts.begin_create(
            RESOURCE_GROUP_NAME, unique_storage_account_name, storage_params).result()

        logging.info(f"Storage account '{unique_storage_account_name}' configured successfully.")
    except ResourceExistsError as e:
        logging.error(f"Storage account name already taken: {e.message}")
        raise
    except Exception as e:
        logging.error(f"Error configuring storage account: {e}")
        raise

# Start VM
def start_vm():
    try:
        logging.info(f"Starting VM '{VM_NAME}'...")
        compute_client.virtual_machines.begin_start(RESOURCE_GROUP_NAME, VM_NAME).result()
        logging.info(f"VM '{VM_NAME}' started successfully.")
    except ResourceNotFoundError as e:
        logging.error(f"VM not found: {e}")
    except Exception as e:
        logging.error(f"Error starting VM: {e}")
        raise

# Stop VM
def stop_vm():
    try:
        logging.info(f"Stopping VM '{VM_NAME}'...")
        compute_client.virtual_machines.begin_power_off(RESOURCE_GROUP_NAME, VM_NAME).result()
        logging.info(f"VM '{VM_NAME}' stopped successfully.")
    except ResourceNotFoundError as e:
        logging.error(f"VM not found: {e}")
    except Exception as e:
        logging.error(f"Error stopping VM: {e}")
        raise

# Delete VM
def delete_vm():
    try:
        logging.info(f"Deleting VM '{VM_NAME}'...")
        compute_client.virtual_machines.begin_delete(RESOURCE_GROUP_NAME, VM_NAME).result()
        logging.info(f"VM '{VM_NAME}' deleted successfully.")
    except ResourceNotFoundError as e:
        logging.error(f"VM not found: {e}")
    except Exception as e:
        logging.error(f"Error deleting VM: {e}")
        raise

if __name__ == "__main__":
    create_resource_group()
    create_virtual_machine()
    setup_sql_database()
    configure_storage_account()
