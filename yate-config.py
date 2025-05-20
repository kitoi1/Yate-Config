#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress
#from textual.app import App, ComposeResult
from textual.widgets import (
    Header, Footer, Static, Button, Input, 
    Select, Switch, DataTable, Log
)
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual import events
import socket
import psutil
import netifaces

# Constants
CONFIG_DIR = "/etc/yate"
CONFIG_FILE = f"{CONFIG_DIR}/yate.conf"
CERT_DIR = f"{CONFIG_DIR}/certs"
CERT_FILE = f"{CERT_DIR}/public_cert.pem"
LOG_DIR = "/var/log/yate"
BACKUP_DIR = "/etc/yate/backups"

# Enhanced Configuration Template
CONFIG_TEMPLATE = """# YateBTS Configuration - Secure Edition v2.3
# Last modified: {timestamp}
# Authorized operators: {user}@{hostname}

[core]
http.enabled=yes
http.port=5038
http.allowed=127.0.0.1,192.168.0.0/16

[GSM]
Radio.Band={band}
Radio.C0={channel}
Radio.MaxTxPower={power}
Radio.CountryCode={mcc}
Radio.NetworkCode={mnc}
Radio.LAC={lac}
Radio.CellID={cell_id}
Radio.Encryption.A5.1=no
Radio.Encryption.A5.3=yes

[Security]
TLS.Enabled={tls_enabled}
TLS.Certificate={cert_path}
TLS.Key={key_path}
AccessControl={users}
AuditLog={audit_log}
FailedLoginAttempts=3
LoginBanTime=300

[Monitoring]
SystemStats.Interval=60
CallDataRecords=yes
SMS.Logs=yes

[Interfaces]
GSM.Interface=eth0
SIP.Interface=eth0
"""

class YateConfigManager:
    def __init__(self):
        self.console = Console()
        self.current_config = {}
        self.load_or_create_config()

    def load_or_create_config(self):
        """Load existing config or create default"""
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR, mode=0o750)
            os.makedirs(CERT_DIR, mode=0o700)
            os.makedirs(BACKUP_DIR, mode=0o750)
            os.makedirs(LOG_DIR, mode=0o775)

        if os.path.exists(CONFIG_FILE):
            self.parse_config()
        else:
            self.create_default_config()

    def parse_config(self):
        """Parse existing config file"""
        with open(CONFIG_FILE, 'r') as f:
            for line in f:
                if '=' in line and not line.strip().startswith('#'):
                    key, value = line.split('=', 1)
                    self.current_config[key.strip()] = value.strip()

    def create_default_config(self):
        """Generate default configuration"""
        hostname = socket.gethostname()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        default_config = CONFIG_TEMPLATE.format(
            timestamp=timestamp,
            user=os.getlogin(),
            hostname=hostname,
            band="GSM900",
            channel="62",
            power="20",
            mcc="645",
            mnc="01",
            lac="4101",
            cell_id="101",
            tls_enabled="yes",
            cert_path=CERT_FILE,
            key_path=f"{CERT_DIR}/private_key.pem",
            users="admin,root",
            audit_log=f"{LOG_DIR}/audit.log"
        )
        
        with open(CONFIG_FILE, 'w') as f:
            f.write(default_config)
        
        self.generate_self_signed_cert()
        self.console.print(f"[bold green]✓ Default configuration created at {CONFIG_FILE}")

    def generate_self_signed_cert(self):
        """Generate self-signed certificate"""
        key_file = f"{CERT_DIR}/private_key.pem"
        
        if not os.path.exists(CERT_FILE):
            subprocess.run([
                'openssl', 'req', '-x509', '-newkey', 'rsa:4096',
                '-keyout', key_file, '-out', CERT_FILE,
                '-days', '365', '-nodes', '-subj',
                '/CN=yatebts.local/O=YateBTS/C=US'
            ], check=True)
            os.chmod(key_file, 0o600)
            self.console.print(f"[bold green]✓ Generated TLS certificate pair")

    def backup_config(self):
        """Create timestamped backup"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"{BACKUP_DIR}/yate.conf.{timestamp}"
        os.system(f"cp {CONFIG_FILE} {backup_file}")
        return backup_file

    def validate_config(self):
        """Validate configuration syntax"""
        # Implementation would check for valid values
        return True

class YateDashboard(App):
    CSS_PATH = "hacker_theme.css"
    BINDINGS = [
        ("ctrl+r", "restart", "Restart Yate"),
        ("ctrl+s", "save", "Save Config"),
        ("ctrl+b", "backup", "Create Backup"),
        ("ctrl+m", "monitor", "Open Monitor"),
        ("ctrl+q", "quit", "Quit"),
    ]
    
    # Reactive properties
    gsm_status = reactive("stopped")
    system_load = reactive(0.0)
    active_calls = reactive(0)
    
    def __init__(self, config_manager):
        super().__init__()
        self.config = config_manager
        self.network_interfaces = self.get_network_interfaces()
        
    def compose(self) -> ComposeResult:
        """Create the main application layout"""
        yield Header(show_clock=True)
        
        with Container(id="app-grid"):
            with Vertical(id="left-panel"):
                yield Static("[b]YateBTS[/] Control Panel", classes="panel-title")
                yield Switch("GSM Service", id="gsm-switch")
                yield Switch("TLS Encryption", id="tls-switch")
                yield Switch("Call Recording", id="recording-switch")
                yield Button("Apply Changes", id="apply-btn", variant="success")
                yield Button("Emergency Stop", id="stop-btn", variant="error")
                
                yield Static("[b]System[/] Metrics", classes="panel-title")
                yield Static(id="system-metrics")
                
            with Vertical(id="center-panel"):
                yield Static("[b]Configuration[/] Editor", classes="panel-title")
                yield Input(placeholder="Search config...", id="config-search")
                yield DataTable(id="config-table")
                
                with Horizontal():
                    yield Button("Add Parameter", id="add-param")
                    yield Button("Delete Parameter", id="del-param")
                
            with Vertical(id="right-panel"):
                yield Static("[b]Network[/] Status", classes="panel-title")
                yield Static(id="network-status")
                
                yield Static("[b]Event[/] Log", classes="panel-title")
                yield Log(max_lines=50, id="event-log")
        
        yield Footer()
    
    def on_mount(self) -> None:
        """Initialize the dashboard"""
        self.title = "YateBTS Hacker Dashboard"
        self.sub_title = "v2.3 | Secure GSM Network"
        
        # Initialize config table
        table = self.query_one("#config-table", DataTable)
        table.add_columns("Parameter", "Value")
        for param, value in self.config.current_config.items():
            table.add_row(param, value)
        
        # Start system monitoring
        self.set_interval(1.0, self.update_system_metrics)
    
    def update_system_metrics(self) -> None:
        """Update real-time system metrics"""
        self.system_load = os.getloadavg()[0]
        cpu_percent = psutil.cpu_percent()
        mem = psutil.virtual_memory()
        
        metrics = self.query_one("#system-metrics", Static)
        metrics.update(
            f"CPU: [cyan]{cpu_percent}%[/]\n"
            f"Load: [yellow]{self.system_load:.2f}[/]\n"
            f"Memory: [magenta]{mem.percent}%[/]\n"
            f"Active Calls: [green]{self.active_calls}[/]"
        )
    
    def get_network_interfaces(self) -> list:
        """Get available network interfaces"""
        interfaces = []
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            if netifaces.AF_INET in addrs:
                ip = addrs[netifaces.AF_INET][0]['addr']
                interfaces.append(f"{iface}: {ip}")
        return interfaces
    
    def action_restart(self) -> None:
        """Restart Yate service"""
        self.query_one("#event-log", Log).write("Restarting Yate service...")
        # Implementation would call systemctl restart yate
    
    def action_save(self) -> None:
        """Save configuration changes"""
        self.query_one("#event-log", Log).write("Saving configuration...")
        # Implementation would write changes to config file
    
    def action_backup(self) -> None:
        """Create configuration backup"""
        backup_file = self.config.backup_config()
        self.query_one("#event-log", Log).write(f"Created backup: {backup_file}")
    
    def action_monitor(self) -> None:
        """Open monitoring view"""
        self.query_one("#event-log", Log).write("Opening real-time monitor...")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks"""
        if event.button.id == "apply-btn":
            self.action_save()
        elif event.button.id == "stop-btn":
            self.gsm_status = "stopped"
            self.query_one("#gsm-switch", Switch).value = False

def main():
    # Check for root privileges
    if os.geteuid() != 0:
        print("[error]This tool requires root privileges. Use sudo.[/error]")
        return
    
    # Initialize configuration manager
    config = YateConfigManager()
    
    # Launch the dashboard
    dashboard = YateDashboard(config)
    dashboard.run()

if __name__ == "__main__":
    main()

def create_gui_css():
    css_code = """/* Dark cyberpunk theme */
Screen {
    background: #0a0a12;
    color: #00ffaa;
    layers: base overlay notes;
}

#app-grid {
    layout: grid;
    grid-size: 3 1;
    grid-columns: 1fr 2fr 1fr;
    padding: 1;
    height: 100%;
}

#left-panel, #center-panel, #right-panel {
    border: solid #444;
    padding: 1;
}

.panel-title {
    text-style: bold;
    color: #ff5555;
    margin-bottom: 1;
}

Switch:focus {
    text-style: bold underline;
}

Button {
    width: 100%;
    margin: 1 0;
}

Button:hover {
    text-style: bold;
}

#apply-btn {
    background: #006600;
}

#stop-btn {
    background: #660000;
}

DataTable {
    height: 60%;
}

Log {
    height: 60%;
    border: solid #333;
    background: #111122;
    padding: 1;
}

Footer {
    background: #222233;
}
"""
    with open("cyberpunk.tcss", "w") as f:
        f.write(css_code)

