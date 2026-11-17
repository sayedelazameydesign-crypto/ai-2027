#!/bin/bash
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   CELIA Fashion Design - Setup Script${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════${NC}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root or with sudo${NC}"
    exit 1
fi

echo -e "${YELLOW}Step 1/10: Updating System...${NC}"
apt-get update && apt-get upgrade -y
apt-get install -y curl wget git ufw fail2ban htop ncdu jq

echo -e "${YELLOW}Step 2/10: Installing Docker...${NC}"
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker ubuntu
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}✓ Docker installed${NC}"
else
    echo -e "${GREEN}✓ Docker already installed${NC}"
fi

echo -e "${YELLOW}Step 3/10: Installing Docker Compose...${NC}"
if ! command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep -oP '"tag_name": "\K(.*)(?=")')
    curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    echo -e "${GREEN}✓ Docker Compose installed${NC}"
else
    echo -e "${GREEN}✓ Docker Compose already installed${NC}"
fi

echo -e "${YELLOW}Step 4/10: System Optimization for ARM...${NC}"
cat >> /etc/sysctl.conf <<EOF
# CELIA Optimizations
vm.swappiness=10
vm.vfs_cache_pressure=50
net.core.somaxconn=1024
net.ipv4.tcp_max_syn_backlog=2048
EOF
sysctl -p

echo -e "${YELLOW}Step 5/10: Docker Daemon Optimization...${NC}"
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<EOF
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "storage-driver": "overlay2",
  "features": {
    "buildkit": true
  }
}
EOF
systemctl restart docker

echo -e "${YELLOW}Step 6/10: Firewall Setup...${NC}"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
echo -e "${GREEN}✓ Firewall configured${NC}"

echo -e "${YELLOW}Step 7/10: Creating Directories...${NC}"
mkdir -p /opt/celia/{infrastructure,ai-core,agents,frontend,backups}
mkdir -p /opt/celia/infrastructure/{traefik,monitoring,vault,init-scripts}
mkdir -p /opt/celia/infrastructure/traefik/dynamic
mkdir -p /opt/celia/infrastructure/monitoring/{prometheus,grafana/provisioning}
echo -e "${GREEN}✓ Directories created${NC}"

echo -e "${YELLOW}Step 8/10: Swap Configuration (4GB)...${NC}"
if [ ! -f /swapfile ]; then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo -e "${GREEN}✓ Swap file created${NC}"
else
    echo -e "${GREEN}✓ Swap file already exists${NC}"
fi

echo -e "${YELLOW}Step 9/10: Fail2ban Configuration...${NC}"
cat > /etc/fail2ban/jail.local <<EOF
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true

[docker-auth]
enabled = true
filter = docker-auth
logpath = /var/lib/docker/containers/*/*.log
maxretry = 3
EOF
systemctl enable fail2ban
systemctl restart fail2ban

echo -e "${YELLOW}Step 10/10: Creating Systemd Service...${NC}"
cat > /etc/systemd/system/celia.service <<EOF
[Unit]
Description=CELIA Fashion Design Platform
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/celia/infrastructure
User=root
StandardOutput=journal
StandardError=journal
ExecStart=/usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable celia.service

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   ✅ Setup Complete!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Clone your repository to /opt/celia/"
echo "2. Copy infrastructure/.env.example to infrastructure/.env"
echo "3. Edit .env with your configuration"
echo "4. Generate secrets with: openssl rand -base64 32"
echo "5. Run: cd /opt/celia/infrastructure && docker-compose up -d"
echo ""
echo -e "${BLUE}Reboot recommended: sudo reboot${NC}"
