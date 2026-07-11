# 🌐 browser-use-cloak-stealth

[![GitHub Stars](https://img.shields.io/github/stars/nnishad/browser-use-cloak-stealth?style=social)](https://github.com/nnishad/browser-use-cloak-stealth)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Powered by browser-use](https://img.shields.io/badge/Powered%20by-browser--use-orange.svg)](https://github.com/browser-use/browser-use)

A local, lightweight, and stealth-hardened execution stack for **[browser-use](https://github.com/browser-use/browser-use)** AI agents. It enables completely free, local, and anti-detection web automation without paid cloud browser APIs (like Browserbase or Steel).

Includes first-class integration and auto-onboarding for the **[Hermes Agent](https://github.com/nousresearch/hermes-agent)** ecosystem.

---

## ✨ Features

*   **Zero Cloud API Fees**: Connects directly to a locally running CloakBrowser instance via CDP.
*   **Stealth & Anti-Detection**: Integrates the Camoufox-powered `cloakbrowser` engine to bypass Cloudflare, Akamai, and fingerprint sensors out of the box.
*   **Virtual Screen Framebuffer**: Runs inside an isolated virtual display (`Xvfb`), keeping your host monitor completely clean.
*   **Visual Human-in-the-Loop**: Spawns an on-demand VNC server (`websockify` + `noVNC`) on port `6080` for solving CAPTCHAs, MFA, or manual overrides, then resumes execution autonomously.

---

## 🛠 Prerequisites

Ensure `uv` is installed to manage Python environments, and install the following OS-level dependencies:

**Debian/Ubuntu-based:**
```bash
sudo apt update
sudo apt install -y xvfb x11vnc websockify novnc
```

**Arch-based (CachyOS/Arch Linux):**
```bash
paru -S x11vnc Xvfb websockify novnc
```

*(Note: Python libraries like `browser-use` and `cloakbrowser` are automatically managed and installed in the local virtual environment via `uv` during setup).*

---

## 🚀 Installation & Setup

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/nnishad/browser-use-cloak-stealth ~/tool/browser-use-cloak-stealth
    cd ~/tool/browser-use-cloak-stealth
    ```

2.  **Run the installer script**:
    Point the script to your local `hermes-agent` installation to apply the core integrations:
    ```bash
    ./install.sh /path/to/hermes-agent
    ```
    *(Defaults to `~/.hermes/hermes-agent` if path is omitted).*

---

## ⚙️ Service Management & Customization

The systemd user service (`stealth-browser.service`) handles launching Xvfb (virtual display), x11vnc, websockify, and CloakBrowser as a background process.

### Service Commands (No `sudo` required)
```bash
# Start the background browser
systemctl --user start stealth-browser.service

# Stop the browser
systemctl --user stop stealth-browser.service

# Restart / Apply changes
systemctl --user daemon-reload && systemctl --user restart stealth-browser.service

# Stream logs
journalctl --user -u stealth-browser.service -f
```

### Customization
To customize parameters, edit `~/.config/systemd/user/stealth-browser.service`:

*   **Changing screen resolution**: Change `-screen 0 1280x800x24` in `Xvfb` arguments (e.g., to `-screen 0 1920x1080x24`).
*   **Changing VNC Web UI Port**: Change the websockify port parameter (e.g., `6080` to `9090`).
*   **Running Headful (Visible)**: Set `Environment=DISPLAY=:0` (your primary monitor), remove all `ExecStartPre` lines, and CloakBrowser will launch directly on your physical screen.

---

## 🤖 Hermes Agent Integration

Once the installation script completes, onboard the tool inside Hermes:

1.  Run the Hermes tool configurator:
    ```bash
    hermes tools
    ```
2.  Enable **`🤖 browser-use (Local, no API)`** from the checklist.
3.  Select **`browser-use (Local, no API)`** in the provider step. It will automatically run health checks to verify your systemd service, Python packages, and local CDP connection.

### Usage
Ask Hermes to perform any browser task:
```bash
hermes "Log in to my Github account and check my notifications"
```
If a CAPTCHA or security verification is encountered, Hermes will output a local VNC url (`http://localhost:6080/vnc.html`) in the chat for you to solve it manually, then resume autonomously.

---

## 🤝 Credits & Acknowledgements

*   Built on top of the excellent [browser-use](https://github.com/browser-use/browser-use) library for agentic web interaction.
*   Leverages [CloakBrowser](https://github.com/jo-inc/camofox-browser) (Camoufox engine) for local stealth anti-detection capabilities.
