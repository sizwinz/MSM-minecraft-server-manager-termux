#!/usr/bin/env bash

set -euo pipefail

C_RESET='\033[0m'
C_BOLD='\033[1m'
C_GREEN='\033[92m'
C_YELLOW='\033[93m'
C_CYAN='\033[96m'
C_RED='\033[91m'

log_info() { echo -e "${C_BOLD}${C_CYAN}[INFO]${C_RESET} $*"; }
log_success() { echo -e "${C_BOLD}${C_GREEN}[SUCCESS]${C_RESET} $*"; }
log_warning() { echo -e "${C_BOLD}${C_YELLOW}[WARNING]${C_RESET} $*"; }
log_error() { echo -e "${C_BOLD}${C_RED}[ERROR]${C_RESET} $*"; }

REPO_URL="https://github.com/sizwinz/MSM-minecraft-server-manager-termux.git"
REPO_DIR="MSM-minecraft-server-manager-termux"
DRY_RUN="${MSM_INSTALL_DRY_RUN:-0}"
TARGET_HOME="${HOME}"
TARGET_USER="$(id -u -n)"
TARGET_GROUP="$(id -g -n)"
SUDO_CMD=()

run() {
    if [ "${DRY_RUN}" = "1" ]; then
        echo "$*"
    else
        "$@"
    fi
}

priv() {
    run "${SUDO_CMD[@]}" "$@"
}

as_install_user() {
    if [ "$(id -u)" -eq 0 ] && [ "${TARGET_USER}" != "root" ] && command -v sudo >/dev/null 2>&1; then
        run sudo -u "${TARGET_USER}" -H "$@"
    else
        run "$@"
    fi
}

is_termux() {
    [ -n "${PREFIX:-}" ] && [[ "${PREFIX}" == *"/com.termux/"* ]] && command -v pkg >/dev/null 2>&1
}

is_debian_like() {
    command -v apt-get >/dev/null 2>&1
}

setup_privilege() {
    if [ "$1" = "termux" ]; then
        SUDO_CMD=()
        return
    fi

    if [ "$(id -u)" -eq 0 ]; then
        SUDO_CMD=()
        if [ -n "${SUDO_USER:-}" ]; then
            TARGET_USER="${SUDO_USER}"
            TARGET_GROUP="$(id -g -n "${SUDO_USER}")"
            TARGET_HOME="$(getent passwd "${SUDO_USER}" 2>/dev/null | cut -d: -f6 || true)"
            TARGET_HOME="${TARGET_HOME:-${HOME}}"
        fi
    elif command -v sudo >/dev/null 2>&1; then
        # Check if we can sudo (prompts if not cached)
        if ! sudo -v 2>/dev/null; then
            log_error "Root privileges are required for Debian/Ubuntu system packages."
            exit 1
        fi
        SUDO_CMD=(sudo)
    else
        log_error "Root privileges are required for Debian/Ubuntu system packages."
        log_info "Please install 'sudo' or run as root."
        exit 1
    fi
}

install_termux_dependencies() {
    log_info "Termux detected. Updating packages..."
    run pkg update -y
    run pkg upgrade -y

    log_info "Installing Termux dependencies..."
    run pkg install -y python git screen php python-psutil tur-repo playit

    if command -v playit >/dev/null 2>&1 && ! command -v playit-cli >/dev/null 2>&1; then
        run ln -sf "$(command -v playit)" "${PREFIX}/bin/playit-cli"
    fi
}

install_adoptium_java() {
    local version="$1"
    local java_dir="${TARGET_HOME}/.config/msm/java/${version}"

    if [ -d "${java_dir}" ] && [ -x "${java_dir}/bin/java" ]; then
        log_info "Java ${version} is already installed at ${java_dir}."
        return
    fi

    log_info "Downloading Java ${version} from Adoptium..."
    local arch
    arch=$(uname -m)
    case "${arch}" in
        x86_64) arch="x64" ;;
        aarch64) arch="aarch64" ;;
        armv7l|armv8l) arch="arm" ;;
        *) log_error "Unsupported architecture: ${arch}"; return 1 ;;
    esac

    local os="linux"
    # Adoptium Uses 'linux' for both standard Linux and Termux/Android environments.

    # Using the /v3/binary/latest endpoint which redirects directly to the tarball.
    local download_url="https://api.adoptium.net/v3/binary/latest/${version}/ga/${os}/${arch}/jre/hotspot/normal/eclipse"

    log_info "Downloading: ${download_url}"
    local tmp_tar="${TMPDIR:-/tmp}/java_${version}.tar.gz"

    # Use -L to follow redirects from the Adoptium API to GitHub
    if ! run curl -fsSL "${download_url}" -o "${tmp_tar}"; then
        log_error "Failed to download Java ${version} from Adoptium."
        return 1
    fi

    log_info "Extracting Java ${version}..."
    as_install_user mkdir -p "${java_dir}"
    as_install_user tar -xzf "${tmp_tar}" -C "${java_dir}" --strip-components=1
    run rm -f "${tmp_tar}"
}
install_apt_package_if_available() {
    local package_name="$1"
    local required="${2:-required}"
    if apt-cache show "${package_name}" >/dev/null 2>&1; then
        priv apt-get install -y "${package_name}"
    elif [ "${required}" = "required" ]; then
        log_warning "Package '${package_name}' was not found in apt repositories."
    fi
}

install_playit_debian() {
    if command -v playit >/dev/null 2>&1 || command -v playit-cli >/dev/null 2>&1; then
        log_info "Playit is already installed."
        return
    fi

    log_info "Installing Playit from the official apt repository..."
    local key_path="${TMPDIR:-/tmp}/playit-cloud-key.gpg"
    run curl -fsSL https://playit-cloud.github.io/ppa/key.gpg -o "${key_path}"
    priv gpg --dearmor -o /etc/apt/trusted.gpg.d/playit.gpg "${key_path}"
    priv sh -c "printf '%s\n' 'deb [signed-by=/etc/apt/trusted.gpg.d/playit.gpg] https://playit-cloud.github.io/ppa/data ./' > /etc/apt/sources.list.d/playit-cloud.list"
    run rm -f "${key_path}"
    priv apt-get update -y
    priv apt-get install -y playit
}

install_debian_dependencies() {
    log_info "Debian/Ubuntu/WSL detected. Updating packages..."
    priv apt-get update -y

    log_info "Installing base dependencies..."
    priv apt-get install -y git screen python3 python3-pip python3-venv curl gnupg ca-certificates

    log_info "Installing php-cli when available..."
    install_apt_package_if_available php-cli optional

    install_playit_debian
}

using_current_checkout() {
    [ -f "msm.py" ] && [ -f "requirements.txt" ]
}

prepare_checkout() {
    if using_current_checkout; then
        INSTALL_DIR="$(pwd)"
        log_info "Using current checkout: ${INSTALL_DIR}"
    else
        INSTALL_DIR="${MSM_INSTALL_DIR:-${TARGET_HOME}/${REPO_DIR}}"
        if [ -f "${INSTALL_DIR}/msm.py" ] && [ -f "${INSTALL_DIR}/requirements.txt" ]; then
            log_info "Reusing existing checkout: ${INSTALL_DIR}"
        else
            log_info "Cloning MSM into ${INSTALL_DIR}..."
            as_install_user git clone "${REPO_URL}" "${INSTALL_DIR}"
        fi
    fi

    # Fix ownership of the installation directory if it exists and we are not root
    if [ -d "${INSTALL_DIR}" ] && [ "${TARGET_USER}" != "root" ] && { [ "$(id -u)" -eq 0 ] || [ "${#SUDO_CMD[@]}" -ne 0 ]; }; then
        log_info "Ensuring correct ownership of ${INSTALL_DIR} for ${TARGET_USER}..."
        priv chown -R "${TARGET_USER}:${TARGET_GROUP}" "${INSTALL_DIR}"
    fi

    cd "${INSTALL_DIR}"
}

configure_python_environment() {
    local python_bin="python3"
    local venv_args=()

    if is_termux; then
        python_bin="python"
        venv_args=(--system-site-packages)
    elif ! command -v python3 >/dev/null 2>&1 && command -v python >/dev/null 2>&1; then
        python_bin="python"
    fi

    log_info "Creating Python virtual environment..."
    # If .venv exists but isn't writable, remove it with privileges
    if [ -d ".venv" ] && [ ! -w ".venv" ]; then
        log_info "Removing unwritable .venv..."
        priv rm -rf .venv
    fi
    as_install_user "${python_bin}" -m venv "${venv_args[@]}" .venv

    log_info "Installing Python dependencies..."
    as_install_user .venv/bin/python -m pip install --upgrade pip
    as_install_user .venv/bin/python -m pip install -r requirements.txt
    as_install_user chmod +x msm.py
}

main() {
    log_info "Starting MSM installation..."

    if is_termux; then
        setup_privilege termux
        install_termux_dependencies
    elif is_debian_like; then
        setup_privilege debian
        install_debian_dependencies
    else
        log_error "Only Termux and Debian/Ubuntu/WSL are supported by this installer."
        log_info "Install python, git, screen, Java 17/21, php, and playit manually, then run MSM."
        exit 1
    fi

    log_info "Installing Java runtimes from Adoptium..."
    install_adoptium_java 17
    install_adoptium_java 21

    prepare_checkout
    configure_python_environment

    log_success "MSM has been installed successfully."
    echo -e "\nRun MSM with:"
    echo -e "${C_GREEN}cd ${INSTALL_DIR} && source .venv/bin/activate && python msm.py${C_RESET}"
}

main "$@"
