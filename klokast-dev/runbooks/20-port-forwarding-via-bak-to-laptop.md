Instructions to commission a new box, from a remote location.
Typically, this is after are decommissioning of the system, and you want to reinstall from scrap. But you're not on site. So you use another box that is connected to the LAN to forward the initial onboarding page.


# 1. Establish the ssh port forwarding

Run this command from the development laptop.

- pre-requisite: Tailscale is running on the development laptop.
- `k001` is the name of the box that is already online in the LAN. Adjust with yours.
- `tail123456` is the code of the Tailscale network. Adjust with yours.
- `neo` is the name of user on `<box>-bak`. This is standard.
- `192.168.1.16` is the IP address of the new box. Adjust with yours.
     Find this address by running this command from the NanoKVM console: `avahi-resolve-host-name kk.local`
     #TODO: let the bootstrap ISO server console show this address, together with these instructions.
- `ops` is machine name of the deployment server in Tailscale. Check this is correct for your deployment.

```
ssh -N \
  -J neo@codex.tail123456.ts.net \
  -L 127.0.0.1:8080:192.168.1.16:80 \
  neo@k001-bak.tail123456.ts.net
```

Let it running until you're done with step 2.

# 2. Access the page

```
http://127.0.0.1:8080/
```
