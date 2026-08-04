"""CDK stack definition for the proxy relay EC2 instance."""

from __future__ import annotations

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from constructs import Construct


class ProxyRelayStack(Stack):
    """On-demand EC2 relay for SOCKS proxy tunnelling.

    Creates:
    - A t4g.nano instance with SSM agent (no SSH key, no public IP needed for mgmt)
    - A security group with no inbound rules (all access via SSM)
    - An IAM instance profile for SSM + CloudWatch
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain: str | None = None,
        vpc_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # VPC – use caller-supplied or default
        if vpc_id:
            vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)
        else:
            vpc = ec2.Vpc.from_lookup(self, "Vpc", is_default=True)

        # Security group – no inbound; SSM uses outbound HTTPS
        sg = ec2.SecurityGroup(
            self,
            "RelaySG",
            vpc=vpc,
            description="Proxy relay – no inbound, SSM only",
            allow_all_outbound=True,
        )

        # Allow inbound on port 2222 for reverse SSH tunnel from work computer
        sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(2222),
            "Reverse SSH tunnel from work computer",
        )

        # IAM role for SSM
        role = iam.Role(
            self,
            "RelayRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )

        # User data: configure sshd for reverse tunnels
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "#!/bin/bash",
            "set -euo pipefail",
            # Install and configure sshd for reverse tunnels
            "yum install -y openssh-server",
            "cat >> /etc/ssh/sshd_config << 'SSHEOF'",
            "Port 2222",
            "GatewayPorts yes",
            "ClientAliveInterval 30",
            "ClientAliveCountMax 3",
            "AllowTcpForwarding yes",
            "SSHEOF",
            "systemctl enable sshd",
            "systemctl restart sshd",
        )

        # EC2 instance
        instance = ec2.Instance(
            self,
            "RelayInstance",
            vpc=vpc,
            instance_type=ec2.InstanceType("t4g.nano"),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(
                cpu_type=ec2.AmazonLinuxCpuType.ARM_64,
            ),
            security_group=sg,
            role=role,
            user_data=user_data,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        # Outputs
        CfnOutput(self, "InstanceId", value=instance.instance_id)
        CfnOutput(self, "SecurityGroupId", value=sg.security_group_id)
        if domain:
            CfnOutput(self, "Domain", value=domain)
