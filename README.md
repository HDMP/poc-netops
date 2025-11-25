# poc-netops
# Network Configuration Automation PoC

> **Automated VLAN provisioning and configuration management using Nautobot as the Source of Truth**

[![Nautobot](https://img.shields.io/badge/Nautobot-Source%20of%20Truth-blue)](https://nautobot.com)
[![Juniper](https://img.shields.io/badge/Platform-Juniper%20JunOS-orange)](https://www.juniper.net)
[![Python](https://img.shields.io/badge/Python-3.8%2B-green)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [Overview](#overview)
- [Use Case](#use-case)
- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Pipeline Workflow](#pipeline-workflow)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Extending the PoC](#extending-the-poc)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## 🎯 Overview

This repository contains a **Proof of Concept (PoC)** for automated network configuration management using **Nautobot** as the central Source of Truth. The system automatically synchronizes VLAN changes from customer-facing Socket interfaces to their connected Switch ports, then pushes the configuration to physical/virtual network devices.

### Key Features

✅ **Bidirectional Sync** - Automatic VLAN synchronization between Socket and Switch interfaces  
✅ **GitOps Workflow** - All configurations version-controlled in Git  
✅ **Backup Safety** - Device configs backed up before any changes  
✅ **Intended State** - Configuration generated from Nautobot data via Jinja2 templates  
✅ **Automated Push** - Changes automatically deployed to devices  
✅ **Audit Trail** - Complete history of changes tracked in Git commits  

---

## 💡 Use Case

### Business Problem

When customers request VLAN changes on their Socket (customer-facing port), network engineers traditionally need to:

1. Manually update the Socket interface VLAN in Nautobot
2. Manually update the connected Switch port VLAN in Nautobot
3. Manually SSH into the switch
4. Manually configure the interface
5. Hope nothing breaks 🤞

**This is slow, error-prone, and doesn't scale.**

### Solution

This PoC automates the entire workflow:

```
Customer requests VLAN change
         ↓
Engineer updates Socket VLAN in Nautobot UI
         ↓
JobHook automatically detects change
         ↓
Switch interface VLAN updated in Nautobot (sync)
         ↓
Pipeline triggered automatically:
  ├─ Backup current device config
  ├─ Generate intended config from Nautobot
  └─ Push config to physical device
         ↓
Customer VLAN is live ✅
```

**Result:** What took 15-30 minutes now takes seconds, with full audit trail and zero manual device access.

---

## 🏗️ Architecture

### Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         NAUTOBOT                                │
│                    (Source of Truth)                            │
│                                                                 │
│  ┌──────────────┐         ┌──────────────┐                    │
│  │   Socket     │◄───────►│    Switch    │                    │
│  │  Interface   │  Sync   │  Interface   │                    │
│  │  (Customer)  │         │  (Uplink)    │                    │
│  └──────────────┘         └──────────────┘                    │
│         │                         │                            │
│         │    JobHook Trigger      │                            │
│         └─────────┬───────────────┘                            │
│                   ▼                                             │
│         ┌─────────────────────┐                                │
│         │  Config Pipeline    │                                │
│         │  Orchestrator Job   │                                │
│         └─────────────────────┘                                │
│                   │                                             │
└───────────────────┼─────────────────────────────────────────────┘
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
    ┌───────┐  ┌────────┐  ┌──────┐
    │Backup │  │Intended│  │ Push │
    │ Job   │  │  Job   │  │ Job  │
    └───┬───┘  └───┬────┘  └──┬───┘
        │          │           │
        ▼          ▼           ▼
    ┌──────────────────────────────┐
    │      Git Repository          │
    │  /backups  /intended         │
    └──────────────────────────────┘
                   │
                   ▼
            ┌─────────────┐
            │   Device    │
            │  (vJunOS)   │
            └─────────────┘
```

### Data Flow

1. **Trigger**: User changes VLAN on Socket interface in Nautobot
2. **Sync**: JobHook detects change and syncs VLAN to Switch interface
3. **Backup**: Current device config retrieved and saved to `/backups`
4. **Intended**: Desired config rendered from Nautobot data to `/intended`
5. **Push**: Configuration commands sent to device via SSH (Netmiko)
6. **Commit**: All changes tracked in Git with commit messages

---

## 📁 Repository Structure

```
poc_netops/
├── README.md                          # This file
├── .git/                              # Git version control
├── .gitignore                         # Git ignore rules
│
├── backups/                           # Device configuration backups
│   ├── switch01.set                   # Backup in Junos "set" format
│   └── switch02.set                   # One file per device
│
├── intended/                          # Intended (desired) configurations
│   ├── switch01.conf                  # Generated from Nautobot + template
│   └── switch02.conf                  # What the device SHOULD look like
│
├── templates/                         # Jinja2 templates
│   └── juniper_junos.j2               # Template for Junos configuration
│
└── jobs/                              # Nautobot job definitions
    ├── __init__.py
    ├── sync_socket_job.py             # JobHook: Socket↔Switch VLAN sync
    ├── config_pipeline_job.py         # Main orchestrator
    ├── backup_config_job.py           # Step 1: Backup device config
    ├── intended_config_job.py         # Step 2: Build intended config
    └── push_config_job.py             # Step 3: Push config to device
```

### Directory Purposes

| Directory | Purpose | Generated By |
|-----------|---------|--------------|
| `/backups` | Current device configurations in "set" format | Backup job or Golden Config plugin |
| `/intended` | Desired configurations rendered from Nautobot | Intended job or Golden Config plugin |
| `/templates` | Jinja2 templates for config generation | Manual (version controlled) |
| `/jobs` | Nautobot job definitions (Python) | Manual (version controlled) |

---

## 🔄 Pipeline Workflow

### Automatic Execution Flow

```mermaid
sequenceDiagram
    participant User
    participant Nautobot
    participant JobHook
    participant Pipeline
    participant Git
    participant Device

    User->>Nautobot: Change Socket VLAN
    Nautobot->>JobHook: Trigger on Interface update
    JobHook->>Nautobot: Sync VLAN to Switch interface
    JobHook->>Pipeline: Start ConfigPipeline
    
    Pipeline->>Device: Connect & retrieve config
    Device->>Pipeline: Return current config
    Pipeline->>Git: Commit to /backups
    
    Pipeline->>Nautobot: Query interface data
    Nautobot->>Pipeline: Return interface details
    Pipeline->>Pipeline: Render Jinja2 template
    Pipeline->>Git: Commit to /intended
    
    Pipeline->>Device: SSH connect & push config
    Device->>Pipeline: Confirm changes
    Pipeline->>Git: Push commits to remote
    
    Pipeline->>User: Log completion
```

### Step-by-Step Breakdown

#### **Step 0: JobHook Trigger** (`sync_socket_job.py`)

- Listens for Interface updates in Nautobot
- Detects Socket ↔ Switch connections
- Syncs untagged VLAN bidirectionally
- Triggers pipeline if changes made

#### **Step 1: Backup** (`backup_config_job.py`)

```bash
# What happens:
SSH to device → Run "show configuration | display set"
                ↓
Save to /backups/<device>.set
                ↓
Git commit: "Backup config for device switch01"
```

**Purpose:** Safety net for rollback if something goes wrong

#### **Step 2: Intended Config** (`intended_config_job.py`)

```bash
# What happens:
Query Nautobot for device interfaces
                ↓
Render /templates/juniper_junos.j2 with data
                ↓
Save to /intended/<device>.conf
                ↓
Git commit: "Update intended config for device switch01"
```

**Purpose:** Generate "should-be" state from Source of Truth

#### **Step 3: Push Config** (`push_config_job.py`)

```bash
# What happens:
Render template for specific interface
                ↓
Generate Junos "set" commands
                ↓
SSH to device → send_config_set()
                ↓
Device applies configuration
```

**Purpose:** Make reality match the intended state

#### **Step 4: Git Push** (`config_pipeline_job.py`)

```bash
# What happens:
Git push to remote repository
                ↓
Sync all commits to central server
```

**Purpose:** Team visibility and backup

---

## 🔧 Prerequisites

### System Requirements

- **Nautobot**: v2.0+ running and accessible
- **Python**: 3.8 or higher
- **Git**: Installed on Nautobot server
- **Network Access**: SSH connectivity to managed devices

### Python Dependencies

```bash
# Required packages
nautobot >= 2.0.0
netmiko >= 4.0.0
jinja2 >= 3.0.0
```

### Device Requirements

- **Platform**: Juniper JunOS (vJunOS or physical)
- **Access**: SSH enabled with authentication configured
- **Credentials**: Stored in Nautobot Secrets Group or environment variables

### Nautobot Setup

1. Device roles configured: `Socket`, `Switch`
2. Devices created with:
   - Platform set to `juniper_junos`
   - Primary IPv4 address assigned
   - Secrets Group with SSH credentials (optional)
3. Interfaces created and cabled between Socket and Switch devices

---

## 📦 Installation

### 1. Clone Repository

```bash
# On Nautobot server
cd /opt/nautobot/
git clone <repository-url> git/poc_netops
cd git/poc_netops
```

### 2. Set Environment Variable

```bash
# Add to Nautobot environment (e.g., ~/.bashrc or systemd unit file)
export POC_NETOPS_REPO="/opt/nautobot/git/poc_netops"

# Optional: Fallback credentials
export NETMIKO_USERNAME="admin"
export NETMIKO_PASSWORD="your_password"
```

### 3. Install Jobs in Nautobot

```bash
# Copy jobs to Nautobot jobs directory
cp -r jobs/* /opt/nautobot/jobs/poc_vlan_automation/

# Or create symlink
ln -s /opt/nautobot/git/poc_netops/jobs /opt/nautobot/jobs/poc_vlan_automation
```

### 4. Reload Nautobot

```bash
# Restart Nautobot to load new jobs
sudo systemctl restart nautobot nautobot-worker
```

### 5. Verify Installation

1. Log into Nautobot web UI
2. Navigate to **Jobs**
3. Look for job group: `00_Vlan-Change-Jobs`
4. You should see:
   - `00_Config pipeline (POC)`
   - `01_Backup device config (POC)`
   - `02_Build intended config (POC)`
   - `03_Push config to device (POC)`
   - `99_Sync Socket VLAN to Switch`

---

## ⚙️ Configuration

### Secrets Group Setup (Recommended)

1. Navigate to **Secrets** → **Secrets Groups** in Nautobot
2. Create a new Secrets Group (e.g., "Device SSH Credentials")
3. Add secrets:
   - **Type**: Username → **Value**: `admin`
   - **Type**: Password → **Value**: `your_device_password`
4. Assign Secrets Group to devices:
   - Edit device → **Secrets Group** field → Select your group

### Template Customization

Edit `/templates/juniper_junos.j2` to customize configuration generation:

```jinja
{# Example: Generate config for all interfaces #}
{% for interface in interfaces %}
set interfaces {{ interface.name }} unit 0 family ethernet-switching interface-mode access
{% if interface.untagged_vlan %}
set interfaces {{ interface.name }} unit 0 family ethernet-switching vlan members {{ interface.untagged_vlan.vid }}
{% endif %}
{% endfor %}
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POC_NETOPS_REPO` | `/opt/nautobot/git/poc_netops` | Path to Git repository |
| `NETMIKO_USERNAME` | - | Fallback SSH username |
| `NETMIKO_PASSWORD` | - | Fallback SSH password |

---

## 🚀 Usage

### Automatic Workflow (Production)

1. **Update VLAN in Nautobot UI:**
   - Navigate to device's Socket interface
   - Change **Untagged VLAN** field
   - Save

2. **Watch it happen:**
   - JobHook detects change
   - Switch interface synced automatically
   - Pipeline executes
   - Device configured

3. **Check logs:**
   - Navigate to **Jobs** → **Job Results**
   - Find latest `99_Sync Socket VLAN to Switch` job
   - Review detailed logs

### Manual Pipeline Execution

To run pipeline manually for testing:

1. Navigate to **Jobs** in Nautobot
2. Select **00_Config pipeline (POC)**
3. Select target device
4. Click **Run Job**

### Git History

```bash
# View pipeline activity
cd /opt/nautobot/git/poc_netops
git log --oneline

# Example output:
a1b2c3d Update intended config for device switch01
e4f5g6h Backup config for device switch01
i7j8k9l Update intended config for device switch02
```

### Rollback Procedure

If a config push causes issues:

```bash
# 1. Find previous working config
cd /opt/nautobot/git/poc_netops
git log --oneline backups/switch01.set

# 2. Checkout previous version
git show <commit-hash>:backups/switch01.set > /tmp/rollback.set

# 3. Manually apply to device or create restore job
# SSH to device and load configuration
```

---

## 🔮 Extending the PoC

### Scaling to Production

This PoC focuses on **VLAN changes only**. To extend to full configuration management:

#### 1. **Expand Template Coverage**

Add sections to `juniper_junos.j2`:

```jinja
{# Hostname #}
set system host-name {{ device.name }}

{# SNMP #}
{% if device.snmp_community %}
set snmp community {{ device.snmp_community }} authorization read-only
{% endif %}

{# NTP #}
{% for ntp_server in ntp_servers %}
set system ntp server {{ ntp_server }}
{% endfor %}

{# Interfaces #}
{% for interface in interfaces %}
  {# ... existing interface config ... #}
  
  {# Description #}
  {% if interface.description %}
  set interfaces {{ interface.name }} description "{{ interface.description }}"
  {% endif %}
  
  {# Tagged VLANs #}
  {% if interface.mode == "tagged" %}
  set interfaces {{ interface.name }} unit 0 family ethernet-switching interface-mode trunk
  set interfaces {{ interface.name }} unit 0 family ethernet-switching vlan members [ {{ interface.tagged_vlans|join(' ') }} ]
  {% endif %}
{% endfor %}
```

#### 2. **Add More Device Platforms**

Create additional templates:

- `/templates/cisco_ios.j2`
- `/templates/arista_eos.j2`
- `/templates/palo_alto.j2`

Modify jobs to select template based on device platform.

#### 3. **Integrate Golden Config Plugin**

This PoC can work alongside [Nautobot Golden Config](https://docs.nautobot.com/projects/golden-config/en/latest/):

- Use Golden Config for compliance checking
- Use this pipeline for automated remediation
- Compare `/backups` vs `/intended` for drift detection

#### 4. **Add Validation Steps**

Before pushing config:

```python
# In push_config_job.py
# Add dry-run check
output = conn.send_config_set(config_lines, exit_config_mode=False)
if "error" in output.lower():
    conn.send_command("rollback 0")
    raise Exception("Config validation failed")
conn.commit()
```

#### 5. **Add Approval Workflow**

For production safety:

- Integrate with Slack/Teams for approval requests
- Require peer review before push
- Add scheduled maintenance windows

#### 6. **Multi-Vendor Support**

Update jobs to handle different platforms:

```python
# In push_config_job.py
PLATFORM_DRIVERS = {
    "juniper_junos": "juniper_junos",
    "cisco_ios": "cisco_ios",
    "arista_eos": "arista_eos",
}

driver = PLATFORM_DRIVERS.get(platform.network_driver)
```

---

## 🐛 Troubleshooting

### Common Issues

#### 1. **Jobs Not Appearing in Nautobot**

**Symptom:** Jobs don't show up in Jobs menu

**Solution:**
```bash
# Check job installation
ls -la /opt/nautobot/jobs/poc_vlan_automation/

# Check Nautobot logs
tail -f /opt/nautobot/logs/nautobot.log

# Restart services
sudo systemctl restart nautobot nautobot-worker
```

#### 2. **SSH Connection Failed**

**Symptom:** `[BackupDeviceConfig] Failed to retrieve configuration`

**Solution:**
- Verify device primary IP is correct
- Test SSH manually: `ssh admin@<device-ip>`
- Check Secrets Group credentials
- Verify firewall rules allow SSH from Nautobot server

#### 3. **Template Rendering Error**

**Symptom:** `[BuildIntendedConfig] Failed to render template`

**Solution:**
```bash
# Validate Jinja2 syntax
python3 << EOF
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))
template = env.get_template('juniper_junos.j2')
print("Template is valid")
EOF
```

#### 4. **Git Push Failed**

**Symptom:** `git push failed`

**Solution:**
```bash
# Check Git remote
cd /opt/nautobot/git/poc_netops
git remote -v

# Add remote if missing
git remote add origin <repository-url>

# Configure credentials
git config user.name "Nautobot Automation"
git config user.email "nautobot@example.com"
```

#### 5. **VLAN Not Syncing**

**Symptom:** Socket VLAN changes but Switch doesn't update

**Solution:**
- Verify devices have correct roles: "Socket" and "Switch"
- Check cable connections in Nautobot
- Review JobHook logs: Jobs → Job Results → `99_Sync Socket VLAN to Switch`
- Ensure JobHook is enabled in Nautobot

### Debug Mode

Enable verbose logging:

```python
# In any job file, set logging level
import logging
self.logger.setLevel(logging.DEBUG)
```

### Support

- Check job logs: **Jobs → Job Results** in Nautobot UI
- Review Git commits: `git log --all --oneline`
- Test individual jobs manually before relying on automation

---

## 🤝 Contributing

### Development Workflow

1. Create feature branch
2. Make changes to jobs or templates
3. Test with manual job execution
4. Commit with descriptive messages
5. Create pull request

### Code Style

- Follow PEP 8 for Python code
- Add comprehensive comments
- Update README for significant changes
- Test with actual devices before merging

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **Nautobot** - Network Source of Truth platform
- **Netmiko** - Multi-vendor SSH library
- **Jinja2** - Templating engine
- **Juniper Networks** - vJunOS platform for testing

---

## 📞 Contact

For questions, issues, or suggestions:

- Create an issue in this repository
- Contact the Network Automation team
- Review Nautobot documentation: https://docs.nautobot.com

---

**Status:** ✅ Proof of Concept - Working  
**Last Updated:** 2025-01-XX  
**Version:** 1.0.0

---

*This PoC demonstrates the power of treating your network as code. What started as a simple VLAN change automation can evolve into a complete configuration management system. Happy automating! 🚀*
