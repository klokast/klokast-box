"""Same-box controller state; authority and provider credentials stay separate."""

# Same-controller state only. Managed /etc trees, source checkouts, provider
# credentials and obsolete authority publications are deliberately absent.
PRESERVE = (
    'var/lib/tailscale', 'etc/machine-id',
    'etc/ssh/ssh_host_ed25519_key', 'etc/ssh/ssh_host_ed25519_key.pub',
    'etc/ssh/ssh_host_rsa_key', 'etc/ssh/ssh_host_rsa_key.pub',
    'etc/ssh/ssh_host_ecdsa_key', 'etc/ssh/ssh_host_ecdsa_key.pub',
    'home/neo/.ssh/authorized_keys', 'home/smith/.ssh/authorized_keys',
    'home/smith/.ssh/github-klokast-codex', 'home/smith/.ssh/github-klokast-codex.pub',
    'home/smith/.ssh/github-klokast-instance', 'home/smith/.ssh/github-klokast-instance.pub',
    'home/smith/private/klokast/dom0-console.yml',
    'home/smith/private/klokast/platform-resources.yml',
    'home/smith/private/klokast/platform-users.yml',
    'home/smith/private/klokast/controller-ha.yml',
    'home/smith/private/klokast/openclaw-vpn.yml',
    'home/smith/private/klokast/vpn-egress',
    'home/smith/private/klokast/huawei-tailscale-pinhole.json',
    'home/smith/private/klokast/archive', 'home/smith/private/klokast/codex-archives',
    'home/smith/private/klokast/apps', 'home/smith/private/klokast/evidence',
    'home/smith/private/klokast/logs',
    'var/lib/klokast/operations-journal', 'var/lib/klokast/updates',
    'var/lib/klokast/apply-used-nonces', 'var/lib/klokast/apply-executions',
    'var/lib/klokast/freebox-ipv6',
    'var/lib/klokast/airunner-migration', 'var/lib/klokast/legacy-input-recovery',
    'var/lib/klokast/private-source-recoveries', 'var/lib/klokast/instance-recovery-manifests',
    'var/log/klokast',
)
PROVIDER_FILES = ('etc/klokast/tailscale-policy.env', 'etc/klokast/tailscale-devices.env',
                  'etc/klokast/freebox-ipv6.json', 'etc/klokast/secret-authority/github-app.env',
                  'etc/klokast/secret-authority/github-app.pem',
                  'home/smith/.ssh/github-klokast-instance-write',
                  'etc/klokast/freebox-ipv6.pending.json',
                  'etc/klokast/secret-authority/apps/static-site/cloudflared-token',
                  'etc/klokast/secret-authority/instance-bootstrap/github-app.env',
                  'etc/klokast/secret-authority/instance-bootstrap/github-app.pem',
                  'etc/klokast/secret-authority/instance-development/github-app.env',
                  'etc/klokast/secret-authority/instance-development/github-app.pem')

