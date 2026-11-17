#!/bin/bash

# 🚀 CELIA Multi-Agent System Startup Script for GitPod
# تم تطويره بواسطة MiniMax Agent

set -e

echo "🚀 Starting CELIA Multi-Agent System on GitPod..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Wait for Docker to be ready in GitPod
echo -e "${YELLOW}⏳ Waiting for Docker to be ready...${NC}"
sleep 15

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}❌ Docker is not running. Starting Docker...${NC}"
    sudo dockerd > /var/log/dockerd.log 2>&1 &
    sleep 20
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  .env file not found. Creating from template...${NC}"
    cp .env.example .env
    
    # Set GitPod-specific environment variables
    echo -e "${YELLOW}🔧 Setting GitPod-specific environment variables...${NC}"
    
    # Get GitPod URLs
    FRONTEND_URL="https://3000-${GITPOD_WORKSPACE_URL#https://}"
    BACKEND_URL="https://3001-${GITPOD_WORKSPACE_URL#https://}"  
    MONITORING_URL="https://3002-${GITPOD_WORKSPACE_URL#https://}"
    PROMETHEUS_URL="https://9090-${GITPOD_WORKSPACE_URL#https://}"
    
    cat >> .env << EOL

# GitPod Specific Settings
GITPOD=true
FRONTEND_URL=${FRONTEND_URL}
BACKEND_URL=${BACKEND_URL}
MONITORING_URL=${MONITORING_URL}
PROMETHEUS_URL=${PROMETHEUS_URL}
DATABASE_HOST=postgres
REDIS_HOST=redis
MONGODB_HOST=mongodb

# Update frontend environment for GitPod
REACT_APP_API_URL=${BACKEND_URL}
REACT_APP_WS_URL=wss://3001-${GITPOD_WORKSPACE_URL#https://}
EOL
fi

# Source environment variables
set -a
source .env
set +a

# Create necessary directories
mkdir -p logs uploads ssl grafana-dashboards

echo -e "${BLUE}🐳 Building Docker images (this may take a few minutes)...${NC}"
docker-compose build --no-cache

echo -e "${BLUE}🚀 Starting all services...${NC}"
docker-compose up -d

# Wait for services to be healthy
echo -e "${YELLOW}⏳ Waiting for services to start...${NC}"
sleep 30

# Function to check service health
check_service() {
    local service_name=$1
    local check_command=$2
    local max_attempts=10
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if eval $check_command > /dev/null 2>&1; then
            echo -e "${GREEN}✅ $service_name is ready${NC}"
            return 0
        fi
        echo -e "${YELLOW}⏳ Waiting for $service_name... (attempt $attempt/$max_attempts)${NC}"
        sleep 10
        ((attempt++))
    done
    echo -e "${RED}❌ $service_name failed to start${NC}"
    return 1
}

# Check service health
echo -e "${BLUE}🔍 Checking service health...${NC}"

check_service "PostgreSQL" "docker-compose exec -T postgres pg_isready -U agent_admin"
check_service "Redis" "docker-compose exec -T redis redis-cli ping | grep -q PONG"
check_service "MongoDB" "docker-compose exec -T mongodb mongosh --eval 'db.adminCommand(\"ping\")' | grep -q \"ok\""
check_service "Backend API" "curl -f http://localhost:3001/health > /dev/null 2>&1 || curl -f http://localhost:3001 > /dev/null 2>&1"
check_service "Frontend" "curl -f http://localhost:3000 > /dev/null 2>&1"
check_service "Grafana" "curl -f http://localhost:3002/api/health > /dev/null 2>&1"

echo ""
echo -e "${GREEN}🎉 CELIA Multi-Agent System is now running!${NC}"
echo ""

# Display URLs
FRONTEND_URL="https://3000-${GITPOD_WORKSPACE_URL#https://}"
BACKEND_URL="https://3001-${GITPOD_WORKSPACE_URL#https://}"  
MONITORING_URL="https://3002-${GITPOD_WORKSPACE_URL#https://}"
PROMETHEUS_URL="https://9090-${GITPOD_WORKSPACE_URL#https://}"

echo -e "${BLUE}📊 Access URLs:${NC}"
echo "   🌟 Dashboard:     $FRONTEND_URL"
echo "   🔧 Backend API:   $BACKEND_URL"
echo "   📈 Monitoring:    $MONITORING_URL"
echo "   📊 Prometheus:    $PROMETHEUS_URL"
echo ""

echo -e "${BLUE}🔐 Default Credentials:${NC}"
echo "   Grafana: admin / admin_pass"
echo "   PostgreSQL: agent_admin / secure_pass_2025"
echo ""

echo -e "${BLUE}🤖 CELIA Agents Ready:${NC}"
echo "   🎛️  Orchestrator Agent"
echo "   💬 Customer Support Agent"
echo "   📝 Content Generator Agent"
echo "   📊 Analytics Agent"
echo "   🔍 Monitoring Agent"
echo "   🔒 Security Agent"
echo "   🔗 Integration Agent"
echo "   🧠 Learning Agent"
echo "   🎨 UX Advisor Agent"
echo "   🛠️  DevOps Agent"
echo ""

# Show service status
docker-compose ps

echo ""
echo -e "${GREEN}✨ CELIA System is ready for testing!${NC}"