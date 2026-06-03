#!/bin/bash
# ============================================
#  Marcus AI Trading Platform - 一键部署脚本
#  适用于 Ubuntu 20.04+ / Debian 11+ / CentOS 8+
#            OpenCloudOS 8+ / RHEL 8+ / Fedora
#  用法: curl -fsSL https://raw.githubusercontent.com/QoungYoung/marcus-platform/main/scripts/deploy.sh | bash
# ============================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

REPO_URL="https://github.com/QoungYoung/marcus-platform.git"
INSTALL_DIR="/opt/marcus-platform"
NEED_PYTHON=false

# ============================================
# 打印 Banner
# ============================================
banner() {
    echo -e "${CYAN}"
    echo "  ╔═══════════════════════════════════════════╗"
    echo "  ║                                           ║"
    echo "  ║     Marcus AI Trading Platform            ║"
    echo "  ║     Linux 一键部署                        ║"
    echo "  ║                                           ║"
    echo "  ╚═══════════════════════════════════════════╝"
    echo -e "${NC}"
}

# ============================================
# 检测操作系统
# ============================================
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VER=$VERSION_ID
    else
        echo -e "${RED}[ERROR] 无法检测操作系统${NC}"
        exit 1
    fi

    # OpenCloudOS 映射到 RHEL/CentOS 生态
    case $OS in
        opencloudos|tencentos|anolis|alinux)
            OS_FAMILY="rhel"
            PKG_MGR="dnf"
            ;;
        centos|rhel|fedora|rocky|almalinux)
            OS_FAMILY="rhel"
            PKG_MGR="dnf"
            ;;
        ubuntu|debian)
            OS_FAMILY="debian"
            PKG_MGR="apt-get"
            ;;
    esac

    echo -e "${GREEN}[✓] 操作系统: $OS $VER (${OS_FAMILY} 系)${NC}"
}

# ============================================
# 安装 Docker
# ============================================
install_docker() {
    # 检测 Docker 是否已安装
    if command -v docker &>/dev/null; then
        DOCKER_VER=$(docker --version 2>/dev/null | grep -oP '\d+\.\d+' | head -1)
        echo -e "${GREEN}[✓] Docker v${DOCKER_VER:-?} 已安装${NC}"
        systemctl start docker 2>/dev/null || true
    else
        echo -e "${YELLOW}[*] 正在安装 Docker...${NC}"
        install_docker_engine
    fi

    # 检测 docker compose 插件
    if docker compose version &>/dev/null; then
        COMPOSE_VER=$(docker compose version --short 2>/dev/null)
        echo -e "${GREEN}[✓] Docker Compose v${COMPOSE_VER:-?} 已就绪${NC}"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_VER=$(docker-compose --version 2>/dev/null | grep -oP '\d+\.\d+' | head -1)
        echo -e "${GREEN}[✓] docker-compose (standalone) v${COMPOSE_VER:-?} 已就绪${NC}"
    else
        echo -e "${YELLOW}[*] 正在安装 Docker Compose...${NC}"
        install_docker_compose
    fi
}

install_docker_engine() {
    case $OS_FAMILY in
        debian)
            apt-get update -y
            apt-get install -y ca-certificates curl gnupg lsb-release
            install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
            apt-get update -y
            apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
            ;;
        rhel)
            if [ "$OS" = "fedora" ]; then
                dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
                dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
            else
                dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
                dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
            fi
            ;;
    esac
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}[✓] Docker 安装完成${NC}"
}

install_docker_compose() {
    # 优先安装插件版 docker compose
    if command -v dnf &>/dev/null; then
        dnf install -y docker-compose-plugin 2>/dev/null || true
    elif command -v apt-get &>/dev/null; then
        apt-get install -y docker-compose-plugin 2>/dev/null || true
    fi

    # 如果插件装不上，装独立版本
    if ! docker compose version &>/dev/null; then
        COMPOSE_BIN="/usr/local/bin/docker-compose"
        curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o "$COMPOSE_BIN"
        chmod +x "$COMPOSE_BIN"
    fi
    echo -e "${GREEN}[✓] Docker Compose 安装完成${NC}"
}

# ============================================
# 安装 Git
# ============================================
install_git() {
    if command -v git &>/dev/null; then
        echo -e "${GREEN}[✓] Git 已安装${NC}"
        return
    fi
    echo -e "${YELLOW}[*] 正在安装 Git...${NC}"
    case $OS_FAMILY in
        debian) apt-get install -y git ;;
        rhel)   dnf install -y git || yum install -y git ;;
    esac
    echo -e "${GREEN}[✓] Git 安装完成${NC}"
}

# ============================================
# 克隆/更新仓库
# ============================================
clone_repo() {
    if [ -d "$INSTALL_DIR/.git" ]; then
        echo -e "${YELLOW}[*] 仓库已存在，正在更新...${NC}"
        cd "$INSTALL_DIR"
        git pull origin main
        echo -e "${GREEN}[✓] 代码已更新${NC}"
    else
        echo -e "${YELLOW}[*] 正在克隆仓库...${NC}"
        git clone "$REPO_URL" "$INSTALL_DIR"
        echo -e "${GREEN}[✓] 代码已克隆到 $INSTALL_DIR${NC}"
    fi
}

# ============================================
# 配置环境变量
# ============================================
configure_env() {
    cd "$INSTALL_DIR"

    if [ -f .env ]; then
        ENV_EXISTS=true
    else
        ENV_EXISTS=false
        cp .env.example .env
    fi

    echo ""
    echo -e "${CYAN}═══════════════════════════════════════${NC}"
    echo -e "${CYAN}  配置 API Keys${NC}"
    echo -e "${CYAN}═══════════════════════════════════════${NC}"

    if [ "$ENV_EXISTS" = false ] || grep -q 'your_deepseek_api_key_here' .env 2>/dev/null; then
        echo ""
        echo -e "${YELLOW}请输入 API Keys（直接回车跳过）:${NC}"
        echo ""

        # 从 /dev/tty 读取以兼容管道模式
        printf "${YELLOW}DeepSeek API Key [必填]: ${NC}" > /dev/tty
        read -r DEEPSEEK_KEY < /dev/tty
        if [ -n "$DEEPSEEK_KEY" ]; then
            sed -i "s|^DEEPSEEK_API_KEY=.*|DEEPSEEK_API_KEY=$DEEPSEEK_KEY|" .env
        fi

        printf "${YELLOW}Tushare Token [必填]: ${NC}" > /dev/tty
        read -r TUSHARE_KEY < /dev/tty
        if [ -n "$TUSHARE_KEY" ]; then
            sed -i "s|^TUSHARE_TOKEN=.*|TUSHARE_TOKEN=$TUSHARE_KEY|" .env
        fi

        printf "${YELLOW}雪球 xq_a_token [必填]: ${NC}" > /dev/tty
        read -r XUEQIU_KEY < /dev/tty
        if [ -n "$XUEQIU_KEY" ]; then
            sed -i "s|^XUEQIU_TOKEN=.*|XUEQIU_TOKEN=$XUEQIU_KEY|" .env
        fi

        # QQ Bot
        printf "${YELLOW}QQ Bot APP ID [可选]: ${NC}" > /dev/tty
        read -r QQ_ID < /dev/tty
        if [ -n "$QQ_ID" ]; then
            sed -i "s|^QQ_APP_ID=.*|QQ_APP_ID=$QQ_ID|" .env
            sed -i "s|^QQ_BOT_ENABLED=.*|QQ_BOT_ENABLED=true|" .env
            printf "${YELLOW}QQ Bot APP Secret: ${NC}" > /dev/tty
            read -r QQ_SECRET < /dev/tty
            if [ -n "$QQ_SECRET" ]; then
                sed -i "s|^QQ_APP_SECRET=.*|QQ_APP_SECRET=$QQ_SECRET|" .env
            fi
        fi

        # 数据库
        sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://marcus:marcus_password@postgres:5432/marcus_trading|" .env
        sed -i "s|^REDIS_URL=.*|REDIS_URL=redis://redis:6379/0|" .env

        echo -e "${GREEN}[✓] .env 配置完成${NC}"
    fi

    echo -e "${GREEN}[✓] 环境变量就绪${NC}"
}

# ============================================
# 创建目录结构
# ============================================
create_directories() {
    cd "$INSTALL_DIR"
    mkdir -p logs data

    echo -e "${GREEN}[✓] 目录结构就绪${NC}"
}

# ============================================
# Docker 部署
# ============================================
docker_deploy() {
    cd "$INSTALL_DIR/docker"

    # 优先使用 docker compose 插件，其次 docker-compose
    if docker compose version &>/dev/null; then
        COMPOSE_CMD="docker compose"
    else
        COMPOSE_CMD="docker-compose"
    fi

    echo -e "${YELLOW}[*] 正在构建并启动所有服务...${NC}"
    $COMPOSE_CMD --env-file ../.env up -d --build

    # 等待服务启动
    echo -e "${YELLOW}[*] 等待服务就绪...${NC}"
    sleep 5

    # 显示状态
    $COMPOSE_CMD ps

    echo ""
    echo -e "${GREEN}═══════════════════════════════════════${NC}"
    echo -e "${GREEN}  部署完成！${NC}"
    echo -e "${GREEN}═══════════════════════════════════════${NC}"
    echo ""
    echo -e "  前端仪表盘:  ${CYAN}http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'YOUR_IP'):3000${NC}"
    echo -e "  API 文档:    ${CYAN}http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'YOUR_IP'):8000/docs${NC}"
    echo -e "  Pi Server:   ${CYAN}http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'YOUR_IP'):3001/health${NC}"
    echo ""
    echo -e "  管理命令:"
    echo -e "    cd $INSTALL_DIR/docker"
    echo -e "    $COMPOSE_CMD ps        ${YELLOW}# 查看状态${NC}"
    echo -e "    $COMPOSE_CMD logs -f   ${YELLOW}# 查看日志${NC}"
    echo -e "    $COMPOSE_CMD restart   ${YELLOW}# 重启服务${NC}"
    echo -e "    $COMPOSE_CMD down      ${YELLOW}# 停止服务${NC}"
    echo ""
}

# ============================================
# 主流程
# ============================================
main() {
    if [ "$(id -u)" -eq 0 ]; then
        RUNNING_AS_ROOT=true
    else
        echo -e "${YELLOW}[!] 需要 root 权限来安装依赖${NC}"
        echo -e "${YELLOW}[!] 正在尝试获取 sudo 权限...${NC}"
        if command -v sudo &>/dev/null; then
            exec sudo bash "$0" "$@"
            exit 0
        fi
    fi

    banner

    echo -e "${YELLOW}[1/6] 检测系统环境...${NC}"
    detect_os

    echo ""
    echo -e "${YELLOW}[2/6] 安装 Git...${NC}"
    install_git

    echo ""
    echo -e "${YELLOW}[3/6] 检测 Docker 环境...${NC}"
    install_docker

    echo ""
    echo -e "${YELLOW}[4/6] 获取代码...${NC}"
    clone_repo

    echo ""
    echo -e "${YELLOW}[5/6] 配置环境...${NC}"
    create_directories
    configure_env

    echo ""
    echo -e "${YELLOW}[6/6] 启动服务...${NC}"
    docker_deploy
}

main "$@"
