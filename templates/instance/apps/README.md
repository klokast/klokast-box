This directory contains instance-specific configuration for applications defined by klokast-box.

Application implementation belongs upstream.

`klokast/klokast-box` is the upstream compiler and runtime.
`<family>-klokast-instance` is the declarative input for the specific `<family>` instance.

Example:

`klokast-box/apps/nextcloud/`
    -> application definition

`klokast-instance/apps/nextcloud/`
    -> this installation configuration

# Application configuration

This directory is reserved for private, instance-specific configuration
for applications implemented by `klokast-box`.

The exact files accepted under `apps/<app>/` are defined by that
application's upstream documentation and schema.

Application implementation, container definitions, generic defaults,
installers, and platform-resource manifests belong in `klokast-box`.

Secrets and application data do not belong here.
