Our VPN client Clash Verge has set up a local proxy on the MacBook. Its bypass list has normal private LAN ranges, but not Tailscale's range `100.64.0.0/10`. So browsers would send Tailnet traffic into that local proxy, and the proxy closes it.
`curl` reaches Tailscale directly without using the proxy.

# 0. Updated way: `Clash Verge Rev` instructions

On MacBook, if using VPN, setup direct connection to Tailnet sites:

Clash Verge Rev -> Settings -> System Proxy (the settings icon)
-> untick "Always use Default Bypass" (otherwise you cannot edit the Proxy Bypass list)
-> add these to the Proxy Bypass Settings: `100.64.0.0/10,*.tail000000.ts.net`

Clash Verge Rev -> Profiles -> right click the profile -> Edit Rules ->

```
Rule Type: IP-CIDR6
Rule Content: fd7a:115c:a1e0::/48
Proxy Policy: DIRECT
```

```
Rule Type: IP-CIDR
Rule Content: 100.64.0.0/10
Proxy Policy: DIRECT
```
-> prepend rule

```
Rule Type: DOMAIN-SUFFIX
Rule Content: tail000000.ts.net
Proxy Policy: DIRECT
```
-> prepend rule

-> prepend rule

-> save

Clash Verge Rev -> Home -> Network Settings
   -> System proxy: ticked
   -> Tun Mode: only ticked when using the terminal to reach sites via VPN (e.g. `chatgpt.com` CLI download)
   -> Proxy Mode -> Rule

# 1. Diagnosis

The proxy can be identified by checking the port that was blocked (here, NextCloud), then check the PID of the listening process:
```
sudo lsof -nP -iTCP:7897 -sTCP:LISTEN
Password:
COMMAND    PID USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
verge-mih 1058 root    5u  IPv4 0xa62874b4f5f000f6      0t0  TCP 127.0.0.1:7897 (LISTEN)

xiaoju@og klokast-box % ps -p 1058 -o pid,ppid,user,comm,args
  PID  PPID USER COMM             ARGS
 1058   548 root /Applications/Cl /Applications/Clash Verge.app/Contents/MacOS/verge-mihomo -d /Us
```

Other useful diagnosis commands:
```
networksetup -getwebproxy "Wi-Fi"
networksetup -getsecurewebproxy "Wi-Fi"
networksetup -getsocksfirewallproxy "Wi-Fi"
```

# 2. Quick Repair

From a checkout of this repo on the MacBook:

```sh
cd ~/src/klokast/klokast-box
klokast-dev/bin/macbook-tailnet-direct apply --service "Wi-Fi"
klokast-dev/bin/macbook-tailnet-direct apply --service "iPhone USB"
klokast-dev/bin/macbook-tailnet-direct test
klokast-dev/bin/macbook-tailnet-direct clash-rules
```

The `clash-rules` output is the rule set to prepend in Clash Verge.

# 3. Bypass MacBook proxy to access Tailscale web pages, e.g. NextCloud and NanoKVM

## 3.1. List the internet interfaces of your MacBook:
```
networksetup -listallnetworkservices
```

## 3.2. Among those, run this command for the interfaces your MacBook normally accesses internet, for example:
```
networksetup -getproxybypassdomains "Wi-Fi"
networksetup -getproxybypassdomains "iPhone USB"
```

## 3.3. Then take the output lists, and expand them with the Tailscale addresses:
```
100.64.0.0/10 \
"*.example.ts.net" \
next.example.ts.net
```

For example:
```
sudo networksetup -setproxybypassdomains "Wi-Fi" \
  127.0.0.1 \
  192.168.0.0/16 \
  10.0.0.0/8 \
  172.16.0.0/12 \
  172.29.0.0/16 \
  localhost \
  "*.local" \
  "*.crashlytics.com" \
  "<local>" \
  100.64.0.0/10 \
  "*.example.ts.net" \
  next.example.ts.net

sudo networksetup -setproxybypassdomains "iPhone USB" \
  127.0.0.1 \
  192.168.0.0/16 \
  10.0.0.0/8 \
  172.16.0.0/12 \
  172.29.0.0/16 \
  localhost \
  "*.local" \
  "*.crashlytics.com" \
  "<local>" \
  100.64.0.0/10 \
  "*.example.ts.net" \
  next.example.ts.net
```

# 4. Configure Clash Verge

So it's better to also apply changes (proxy bypass, TUN exclusion, custom routing rules) in Clash Verge too, because:
- Clash Verge may reapply proxy settings when networks change, and
- TUN mode can intercept traffic beyond normal system proxy.
See `https://www.clashverge.dev/guide/bypass.html` and `https://www.clashverge.dev/guide/rules.html`

For these custom direct rules to apply, use Clash Verge in "Rule" mode, not "Global".

## 4.1 Create 4 prepend DIRECT rules

Clash Verge > left sidebar > Right-click active subscription or profile > Edit Rules

Create the following 4 rules, one a time, near the top before broad proxy rules
(Clash rules are first-match, top-to-bottom. Prepend rules win before subscription rules.)

| Rule type | Value / Content | Proxy Policy | tick |
|---|---|---|---|
| DOMAIN | next.example.ts.net | DIRECT | - |
| DOMAIN-SUFFIX | example.ts.net | DIRECT | - |
| IP-CIDR | 100.64.0.0/10 | DIRECT | no resolve |
| IP-CIDR6 | fd7a:115c:a1e0::/48 | DIRECT | no resolve |

  > button "Prepend Rule" > Save > reload/apply the profile

## 4.2 If you also use TUN Mode

(Probably not necessary)

TUN mode is Clash Verge acting like a local virtual network adapter. Without TUN mode, Clash usually works by setting macOS HTTP/HTTPS/SOCKS proxy settings.
Apps that respect system proxy settings, like browsers, send traffic to Clash.
Apps that ignore proxy settings may bypass it.

With TUN mode enabled, Clash creates a virtual network interface and captures IP traffic more broadly at the network layer. That can affect apps even if they do not use the system proxy. It is closer to VPN-style interception.

- If TUN mode is off, your `DIRECT` rules plus macOS proxy bypass are usually enough.
- If TUN mode is on, Clash may still intercept Tailnet IP traffic like `100.112.102.45` unless the TUN route/exclude/bypass list also excludes Tailscale ranges.

Settings > TUN Mode > add the same Tailnet CIDRs/domains to the TUN bypass/exclude list:
```
100.64.0.0/10
fd7a:115c:a1e0::/48
```

If Clash Verge has a "System Proxy Bypass" field under Settings or the System Proxy gear, add:
```
100.64.0.0/10
*.example.ts.net
next.example.ts.net
```

# 5. When it breaks again

Clash Verge and macOS updates can rewrite proxy and TUN settings. Keep the
repair manual and rerun this flow when private Tailnet access breaks.

```sh
cd ~/src/klokast/klokast-box
git pull --ff-only
klokast-dev/bin/macbook-tailnet-direct check --service "Wi-Fi"
klokast-dev/bin/macbook-tailnet-direct apply --service "Wi-Fi"
klokast-dev/bin/macbook-tailnet-direct apply --service "iPhone USB"
klokast-dev/bin/macbook-tailnet-direct test
```
