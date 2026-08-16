#!/usr/bin/env bash

set -euo pipefail

C_RESET='\033[0m'
C_BOLD='\033[1m'
C_DIM='\033[2m'
C_GREEN='\033[92m'
C_YELLOW='\033[93m'
C_CYAN='\033[96m'
C_RED='\033[91m'

GLYPH_CHECK="✔"
GLYPH_CROSS="✖"

REPO_URL="https://github.com/sizwinz/MSM-minecraft-server-manager-termux.git"
REPO_DIR="MSM-minecraft-server-manager-termux"
DRY_RUN="${MSM_INSTALL_DRY_RUN:-0}"
VERBOSE="${VERBOSE:-0}"
LOG_FILE="${MSM_INSTALL_LOG:-${TMPDIR:-/tmp}/msm-install.log}"
TARGET_HOME="${HOME}"
TARGET_USER="$(id -u -n 2>/dev/null || id -u 2>/dev/null || echo "${USER:-user}")"
TARGET_GROUP="$(id -g -n 2>/dev/null || id -g 2>/dev/null || echo "${USER:-user}")"
SUDO_CMD=()

for arg in "$@"; do
    case "$arg" in
        -v|--verbose)
            VERBOSE=1
            ;;
    esac
done

log_info() { echo -e "${C_BOLD}${C_CYAN}[INFO]${C_RESET} $*"; }
log_success() { echo -e "${C_BOLD}${C_GREEN}[SUCCESS]${C_RESET} $*"; }
log_warning() { echo -e "${C_BOLD}${C_YELLOW}[WARNING]${C_RESET} $*"; }
log_error() { echo -e "${C_BOLD}${C_RED}[ERROR]${C_RESET} $*"; }

get_term_width() {
    local width=60
    if command -v tput >/dev/null 2>&1; then
        width=$(tput cols 2>/dev/null || echo 60)
    fi
    if [ -z "${width}" ] || [ "${width}" -lt 40 ]; then
        width=40
    elif [ "${width}" -gt 70 ]; then
        width=70
    fi
    echo "${width}"
}

print_banner() {
    local w
    w=$(get_term_width)
    local line
    line=$(printf '─%.0s' $(seq 1 "${w}"))

    echo -e "${C_CYAN}  __  __ ____  __  __ ${C_RESET}"
    echo -e "${C_CYAN} |  \/  / ___||  \/  |${C_RESET}  ${C_BOLD}Minecraft Server Manager${C_RESET}"
    echo -e "${C_CYAN} | |\/| \___ \| |\/| |${C_RESET}  ${C_DIM}Termux & Linux Edition${C_RESET}"
    echo -e "${C_CYAN} |_|  |_|____/|_|  |_|${C_RESET}  ${C_DIM}v6.0${C_RESET}"
    echo -e "${C_CYAN}${line}${C_RESET}\n"
}

show_failure_card() {
    local failed_step="$1"
    local w
    w=$(get_term_width)
    local line
    line=$(printf '─%.0s' $(seq 1 "$((w - 2))"))

    echo -e "\n${C_RED}╭${line}╮${C_RESET}"
    echo -e "${C_RED}│${C_RESET}  ${C_BOLD}Installation Error${C_RESET}"
    echo -e "${C_RED}│${C_RESET}  Failed step: ${failed_step}"
    echo -e "${C_RED}│${C_RESET}  Log file   : ${LOG_FILE}"
    echo -e "${C_RED}├${line}┤${C_RESET}"
    if [ -f "${LOG_FILE}" ]; then
        tail -n 10 "${LOG_FILE}" | while IFS= read -r l; do
            local clean_l="${l:0:$((w - 6))}"
            echo -e "${C_DIM}│  ${clean_l}${C_RESET}"
        done
    fi
    echo -e "${C_RED}╰${line}╯${C_RESET}"
    echo -e "\n${C_YELLOW}Tip:${C_RESET} Re-run with ${C_BOLD}VERBOSE=1 bash install.sh${C_RESET} to view detailed output."
}

run_step() {
    local step_num="$1"
    local step_total="$2"
    local title="$3"
    shift 3

    if [ "${DRY_RUN}" = "1" ]; then
        echo -e " ${C_GREEN}${GLYPH_CHECK}${C_RESET} [${step_num}/${step_total}] ${title}"
        "$@"
        return $?
    fi

    if [ "${VERBOSE}" = "1" ]; then
        echo -e "\n${C_BOLD}${C_CYAN}──▶ [${step_num}/${step_total}] ${title}...${C_RESET}"
        "$@"
        return $?
    fi

    mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true
    echo -e "\n=== STEP [${step_num}/${step_total}]: ${title} ===" >> "${LOG_FILE}"

    "$@" >> "${LOG_FILE}" 2>&1 &
    local cmd_pid=$!

    local spin_chars=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    local i=0

    if [ -t 1 ]; then
        while kill -0 "${cmd_pid}" 2>/dev/null; do
            local spinner="${spin_chars[i % 10]}"
            printf "\r \033[96m%s\033[0m [%s/%s] %s" "${spinner}" "${step_num}" "${step_total}" "${title}..."
            sleep 0.08
            i=$((i + 1))
        done
    else
        wait "${cmd_pid}"
    fi

    wait "${cmd_pid}"
    local exit_code=$?

    if [ "${exit_code}" -eq 0 ]; then
        if [ -t 1 ]; then
            printf "\r\033[K \033[92m%s\033[0m [%s/%s] %s\n" "${GLYPH_CHECK}" "${step_num}" "${step_total}" "${title}"
        else
            echo -e " ${C_GREEN}${GLYPH_CHECK}${C_RESET} [${step_num}/${step_total}] ${title}"
        fi
        return 0
    else
        if [ -t 1 ]; then
            printf "\r\033[K \033[91m%s\033[0m [%s/%s] %s\n" "${GLYPH_CROSS}" "${step_num}" "${step_total}" "${title}"
        else
            echo -e " ${C_RED}${GLYPH_CROSS}${C_RESET} [${step_num}/${step_total}] ${title}"
        fi
        show_failure_card "${title}"
        return "${exit_code}"
    fi
}

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

is_arch_like() {
    command -v pacman >/dev/null 2>&1
}

is_fedora_like() {
    command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1
}

is_alpine_like() {
    command -v apk >/dev/null 2>&1
}

is_suse_like() {
    command -v zypper >/dev/null 2>&1
}

is_void_like() {
    command -v xbps-install >/dev/null 2>&1
}

is_macos() {
    [ "${MSM_PLATFORM:-}" = "macos" ] || [ "$(uname -s 2>/dev/null)" = "Darwin" ]
}

setup_privilege() {
    if [ "$1" = "termux" ] || [ "$1" = "macos" ]; then
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
        if [ "${DRY_RUN}" != "1" ] && ! sudo -v 2>/dev/null; then
            log_error "Root privileges are required for system package management."
            exit 1
        fi
        SUDO_CMD=(sudo)
    else
        log_error "Root privileges are required for system package management."
        log_info "Please install 'sudo' or run as root."
        exit 1
    fi
}

update_termux_repos() {
    if ! run pkg update -y; then
        rm -rf "${PREFIX}/var/lib/apt/lists/"* 2>/dev/null || true
        run pkg update -y || true
    fi
    run pkg upgrade -y || true
}

install_termux_dependencies() {
    run pkg install -y python git screen php python-psutil tur-repo playit openjdk-25 openjdk-21 openjdk-17 || run pkg install -y openjdk-21 openjdk-17 || run pkg install -y openjdk-17 || true

    if command -v playit >/dev/null 2>&1 && ! command -v playit-cli >/dev/null 2>&1; then
        run ln -sf "$(command -v playit)" "${PREFIX}/bin/playit-cli"
    fi
}

install_adoptium_java() {
    local version="$1"
    local java_dir="${TARGET_HOME}/.config/msm/java/${version}"

    if [ -d "${java_dir}" ] && [ -x "${java_dir}/bin/java" ]; then
        return 0
    fi

    local arch
    arch=$(uname -m)
    case "${arch}" in
        x86_64|amd64) arch="x64" ;;
        aarch64|arm64) arch="aarch64" ;;
        armv7l|armv8l) arch="arm" ;;
        *) log_error "Unsupported architecture: ${arch}"; return 1 ;;
    esac

    local os="linux"
    if [ "${MSM_PLATFORM:-}" = "macos" ] || [ "$(uname -s)" = "Darwin" ]; then
        os="mac"
    fi

    local download_url="https://api.adoptium.net/v3/binary/latest/${version}/ga/${os}/${arch}/jre/hotspot/normal/eclipse"
    local tmp_tar="${TMPDIR:-/tmp}/java_${version}.tar.gz"

    if ! run curl -fsSL "${download_url}" -o "${tmp_tar}"; then
        log_error "Failed to download Java ${version} from Adoptium."
        return 1
    fi

    as_install_user mkdir -p "${java_dir}"
    as_install_user tar -xzf "${tmp_tar}" -C "${java_dir}" --strip-components=1
    run rm -f "${tmp_tar}"
}

setup_all_java_runtimes() {
    if is_termux; then
        run pkg install -y openjdk-25 openjdk-21 openjdk-17 || run pkg install -y openjdk-21 openjdk-17 || run pkg install -y openjdk-17 || true
        return 0
    fi
    install_adoptium_java 17
    install_adoptium_java 21
    install_adoptium_java 25 || true
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
        return 0
    fi

    local key_path="${TMPDIR:-/tmp}/playit-cloud-key.gpg"
    run curl -fsSL https://playit-cloud.github.io/ppa/key.gpg -o "${key_path}"
    priv gpg --dearmor -o /etc/apt/trusted.gpg.d/playit.gpg "${key_path}"
    priv sh -c "printf '%s\n' 'deb [signed-by=/etc/apt/trusted.gpg.d/playit.gpg] https://playit-cloud.github.io/ppa/data ./' > /etc/apt/sources.list.d/playit-cloud.list"
    run rm -f "${key_path}"
    priv apt-get update -y
    priv apt-get install -y playit
}

install_debian_dependencies() {
    priv apt-get install -y git screen python3 python3-pip python3-venv curl gnupg ca-certificates
    install_apt_package_if_available php-cli optional
    install_playit_debian
}

install_arch_dependencies() {
    priv pacman -S --noconfirm --needed git screen python python-pip curl gnupg
}

install_fedora_dependencies() {
    if command -v dnf >/dev/null 2>&1; then
        priv dnf install -y git screen python3 python3-pip curl gnupg
    else
        priv yum install -y git screen python3 python3-pip curl gnupg
    fi
}

install_alpine_dependencies() {
    priv apk add --no-cache git screen python3 py3-pip py3-virtualenv curl gnupg bash tar
}

install_suse_dependencies() {
    priv zypper install -y git screen python3 python3-pip curl gpg2 tar
}

install_void_dependencies() {
    priv xbps-install -y git screen python3 python3-pip curl gnupg
}

install_macos_dependencies() {
    if command -v brew >/dev/null 2>&1; then
        run brew install git screen python
    else
        log_warning "Homebrew is recommended to install dependencies on macOS ('brew install git screen python')."
    fi
}

using_current_checkout() {
    [ -f "msm.py" ] && [ -f "requirements.txt" ]
}

prepare_checkout() {
    if using_current_checkout; then
        INSTALL_DIR="$(pwd)"
    else
        INSTALL_DIR="${MSM_INSTALL_DIR:-${TARGET_HOME}/${REPO_DIR}}"
        if [ -f "${INSTALL_DIR}/msm.py" ] && [ -f "${INSTALL_DIR}/requirements.txt" ]; then
            :
        else
            as_install_user git clone "${REPO_URL}" "${INSTALL_DIR}"
        fi
    fi

    if [ -d "${INSTALL_DIR}" ] && [ "${TARGET_USER}" != "root" ] && { [ "$(id -u)" -eq 0 ] || [ "${#SUDO_CMD[@]}" -ne 0 ]; }; then
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

    if [ -d ".venv" ] && [ ! -w ".venv" ]; then
        priv rm -rf .venv
    fi
    as_install_user "${python_bin}" -m venv "${venv_args[@]}" .venv

    as_install_user .venv/bin/python -m pip install --upgrade pip
    as_install_user .venv/bin/python -m pip install -r requirements.txt
    as_install_user chmod +x msm.py
}

print_success_card() {
    local w
    w=$(get_term_width)
    local line
    line=$(printf '─%.0s' $(seq 1 "$((w - 2))"))

    echo -e "\n${C_GREEN}╭${line}╮${C_RESET}"
    echo -e "${C_GREEN}│${C_RESET}  ${C_BOLD}✨ MSM installed successfully!${C_RESET}"
    echo -e "${C_GREEN}│${C_RESET}"
    echo -e "${C_GREEN}│${C_RESET}  To launch MSM:"
    echo -e "${C_GREEN}│${C_RESET}    ${C_CYAN}cd ${INSTALL_DIR}${C_RESET}"
    echo -e "${C_GREEN}│${C_RESET}    ${C_CYAN}source .venv/bin/activate${C_RESET}"
    echo -e "${C_GREEN}│${C_RESET}    ${C_CYAN}python msm.py${C_RESET}"
    echo -e "${C_GREEN}╰${line}╯${C_RESET}\n"
}

main() {
    print_banner

    if is_termux; then
        setup_privilege termux
        run_step 1 5 "Updating package repositories" update_termux_repos
        run_step 2 5 "Installing core dependencies (git, screen, playit, python)" install_termux_dependencies
    elif is_debian_like; then
        setup_privilege debian
        run_step 1 5 "Updating package repositories" priv apt-get update -y
        run_step 2 5 "Installing core dependencies (git, screen, playit, python)" install_debian_dependencies
    elif is_arch_like; then
        setup_privilege arch
        run_step 1 5 "Updating package repositories" priv pacman -Sy
        run_step 2 5 "Installing core dependencies (git, screen, python, curl)" install_arch_dependencies
    elif is_fedora_like; then
        setup_privilege fedora
        if command -v dnf >/dev/null 2>&1; then
            run_step 1 5 "Updating package repositories" priv dnf check-update || true
        else
            run_step 1 5 "Updating package repositories" priv yum check-update || true
        fi
        run_step 2 5 "Installing core dependencies (git, screen, python3, curl)" install_fedora_dependencies
    elif is_alpine_like; then
        setup_privilege alpine
        run_step 1 5 "Updating package repositories" priv apk update
        run_step 2 5 "Installing core dependencies (git, screen, python3, bash)" install_alpine_dependencies
    elif is_suse_like; then
        setup_privilege suse
        run_step 1 5 "Updating package repositories" priv zypper refresh -f
        run_step 2 5 "Installing core dependencies (git, screen, python3, curl)" install_suse_dependencies
    elif is_void_like; then
        setup_privilege void
        run_step 1 5 "Updating package repositories" priv xbps-install -S
        run_step 2 5 "Installing core dependencies (git, screen, python3, curl)" install_void_dependencies
    elif is_macos; then
        setup_privilege macos
        if command -v brew >/dev/null 2>&1; then
            run_step 1 5 "Updating Homebrew" brew update
            run_step 2 5 "Installing core dependencies (git, screen, python)" install_macos_dependencies
        else
            run_step 1 5 "Checking environment" true
            run_step 2 5 "Installing core dependencies (manual/brew)" install_macos_dependencies
        fi
    else
        log_warning "Unsupported or unrecognized distribution."
        log_info "Attempting installation with existing system tools..."
    fi

    run_step 3 5 "Setting up Java runtimes (Adoptium 17 & 21)" setup_all_java_runtimes
    run_step 4 5 "Preparing MSM codebase" prepare_checkout
    run_step 5 5 "Configuring Python virtual environment" configure_python_environment

    print_success_card
}

main "$@"
